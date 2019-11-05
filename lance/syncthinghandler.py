import os
import shutil
import errno
import copy
import subprocess
import urllib.request as requester
import json
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom
import string
import random

import hashlib

from .servercomponent import ServerComponent
from . import lance_utils
from .lance_utils import async_method, BaseEvent
from .eventtypes import *
from . import eventprocessor
from .logger import get_logger

from typing import Union, Optional, Iterable, Set

def listdir(path):
    return filter(lambda x: re.match(r'^\.syncthing\..*\.tmp$', x) is None, os.listdir(path))


#  EXCEPTIONS
class SyncthingNotReadyError(RuntimeError):
    pass


class SyncthingHandlerConfigError(RuntimeError):
    pass

class NoInitialConfiguration(RuntimeError):
    pass
#  HELPERS

class Device:
    def __init__(self, id: str, name: Optional[str] = None):
        self.__stid = id
        self.__name = name

    def id(self) -> str:
        """
        :return: syncthing id 
        """
        return self.__stid

    def name(self) -> str:
        """
        :return: human readable name of the device
        """
        return self.__name if self.__name is not None else 'device %s' % self.__stid[:6]

    def __eq__(self, other):
        return self.__stid == other.__stid and \
               self.__name == other.__name

    def serialize(self) -> str:
        return json.dumps({"id": self.__stid,
                           "name": self.__name
                           })

    @classmethod
    def deserialize(cls, s: str):
        data = json.loads(s)
        return Device(data['id'], data['name'])


class Folder:
    def __init__(self, id: str, label: str, path: Optional[str] = None, devices: Optional[Iterable] = None):
        self.__stfid = id
        self.__label = label
        self.__path = path
        self.__devices = set(devices) if devices is not None else set()

    def id(self) -> str:
        return self.__stfid

    def label(self) -> str:
        return self.__label

    def path(self) -> Optional[str]:
        return self.__path

    def set_path(self, path:Optional[str], move_contents=True) -> None:
        """
        sets path for the folder to be synced into
        if move_contents is True - contents of existing folder will be moved to the new location
        :param path:
        :param move_contents:
        :return:
        """
        os.makedirs(path, exist_ok=True)
        if move_contents and self.__path is not None:
            shutil.rmtree(path, True)
            shutil.copytree(self.__path, path)
            shutil.rmtree(self.__path, True)
        self.__path = path

    def active(self) -> bool:
        return self.__path is not None

    def devices(self) -> Set[str]:
        """
        MUTABLE !!
        :return: set of devices of this folder (NOT INCLUDING SERVERS)
        """
        return self.__devices

    def add_device(self, device: str) -> None:
        self.__devices.add(device)

    def remove_device(self, device: str) -> None:
        self.__devices.remove(device)

    def __eq__(self, other):
        return self.__stfid == other.__stfid and \
               self.__label == other.__label and \
               self.__path == other.__path and \
               self.__devices == other.__devices


#  MAIN GUY
class SyncthingHandler(ServerComponent):

    class ControlFolder:  #TODO: replace with Folder class? or subclass at least?
        def __init__(self, fid, path):
            self.__fid = fid
            self.__path = path

        def fid(self):
            return self.__fid

        def path(self):
            return self.__path

    def __init__(self, server):
        super(SyncthingHandler, self).__init__(server)

        self.__log = get_logger(self.__class__.__name__)
        self.__log.min_log_level = 1

        self.syncthing_bin = r'syncthing'
        self.data_root = server.config['data_root']  # os.path.join(os.path.split(os.path.abspath(__file__))[0], r'data')
        self.config_root = server.config['config_root']  # os.path.join(os.path.split(os.path.abspath(__file__))[0], r'config')

        self.syncthing_ip = '127.0.0.1'
        self.syncthing_port = 9394 + int(random.uniform(0, 1000))
        self.syncthing_listenaddr = "tcp4://127.0.0.1:%d" % int(random.uniform(22000, 23000))
        self.syncthing_proc = None
        self.__servers = set()  # set of ids in __devices dict that are servers
        self.__devices = {}  # dicts with id:Device
        self.__folders = {}  # data folder dict:  fid:Folder
        self.__ignoreDevices = set()  # set of devices

        self.__apikey = None
        self.__myid = None
        self.__server_secret = None

        self._last_event_id = 0

        self.__isValidState = True
        self.__configInSync = False
        self.__reload_configuration()
        if self.isServer():  # register special server event processors
            self.__updateClientConfigs()


    def start(self):
        super(SyncthingHandler, self).start()
        self.__start_syncthing()

    def stop(self):
        super(SyncthingHandler, self).stop()
        self.__stop_syncthing()

    def myId(self):
        if self.__myid is not None:
            return self.__myid
        proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-browser', '-no-restart', '-device-id'], stdout=subprocess.PIPE)
        res = proc.communicate()[0]
        if res.endswith(b'\n'):
            res = res[:-1]
        res = res.decode()
        if proc.wait() != 0:
            raise NoInitialConfiguration()
        self.__myid = res
        return res

    def isServer(self):
        return self.myId() in self.__servers

    def _runLoopLoad(self):
        while True:
            # TODO: check for device/folder connection events to check for blacklisted, just in case
            if self.syncthing_proc is not None and self.__isValidState:  # TODO: skip this for some set time to wait for events to accumulate
                try:
                    stevents = self.__get('/rest/events', since=self._last_event_id, timeout=1)
                except:
                    time.sleep(2)
                    yield
                    continue
                event = None
                self.__log(0, "syncthing event", stevents)
                # loop through rest events and pack them into lance events
                if stevents is None:
                    time.sleep(2)
                    yield
                    continue
                for stevent in stevents:
                    # filter and pack events into our wrapper
                    if stevent['type'] == 'ItemFinished':
                        data = stevent['data']
                        if data['folder'] in self.get_config_folder().fid():
                            event = ControlEvent(stevent)
                    else:  # General event
                        event = SyncthingEvent(stevent)

                    # Config sincronization event processing
                    if self.__configInSync and stevent['type'] == 'ItemStarted' and stevent['data']['folder'] == self.get_config_folder().fid():  # need to send him the configuration
                        self.__configInSync = False
                    elif not self.__configInSync and stevent['type'] == 'FolderSummary' and stevent['data']['folder'] == self.get_config_folder().fid():
                        data = stevent['data']
                        if data['summary']['needTotalItems'] == 0:
                            self.__reload_configuration()

                    if event is not None:
                        self._enqueueEvent(event)

                    if self._last_event_id < stevent['id']:
                        self._last_event_id = stevent['id']
            time.sleep(1)
            yield

    def __generateInitialConfig(self):
        dorestart = self.syncthing_running()
        try:
            if dorestart:
                self.__stop_syncthing()
            try:
                self.myId()
                return  # if we can get id - config is already generated
            except NoInitialConfiguration:
                pass

            # we dont care about syncthing config, but we need certeficates!
            proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-browser', '-no-restart', '-gui-address={addr}:{port}'.format(addr=self.syncthing_ip, port=self.syncthing_port), '-paused'], stdout=subprocess.PIPE)

            def _killproc(proc):
                proc.stdout.close()
                proc.terminate()
                time.sleep(5)
                if proc.poll() is None:
                    proc.kill()
                    proc.join()

            for i in range(100):
                line = proc.stdout.readline().decode()
                self.__log(1, line)
                if 'Default config saved' in line:
                    _killproc(proc)
                    break
            else:
                _killproc(proc)
                raise lance_utils.SyncthingError('unable to generate config!')

            # lets generate initial cache
            self.myId()
            self.__apikey = hashlib.sha1((self.myId() + '-apikey-%s' % ''.join((random.choice(string.ascii_letters) for _ in range(16)))).encode('UTF-8')).hexdigest()
            self.__server_secret = ''.join((random.choice(string.ascii_letters) for _ in range(24)))

            self.__servers = set()
            self.__devices = {self.myId(): Device(self.myId(), 'myself')}
            self.__save_configuration()

        finally:
            if dorestart:
                self.__start_syncthing()

    def __updateClientConfigs(self):
        pass

    def __del__(self):
        self.__stop_syncthing()

    def get_devices(self):
        return self.__devices  # TODO: make immutable

    # def get_config_path(self):
    #     if self.isServer():
    #         return os.path.join(self.data_root, 'server', 'configuration')
    #     else:
    #         return os.path.join(self.data_root, 'control', self.myId())

    def get_config_folder(self, devid=None):
        """
        get control folder of a client if server and device is given
        get server config folder if server and device is None
        else return client's control folder
        :param devid:
        :return:
        """
        if not self.isServer():
            if devid is None:
                return SyncthingHandler.ControlFolder(fid='control-%s' % hashlib.sha1((':'.join([self.__server_secret, self.myId()])).encode('UTF-8')).hexdigest(),
                                                      path=os.path.join(self.data_root, 'control', self.myId())
                                                      )
            else:
                raise RuntimeError('wat do u think ur doin?')
        if self.isServer() and devid is None:
            return SyncthingHandler.ControlFolder(fid='server_configuration-%s' % hashlib.sha1(self.__server_secret.encode('UTF-8')).hexdigest(),
                                                  path=os.path.join(self.data_root, 'server')
                                                  )

        if devid not in self.__devices:
            raise RuntimeError('unknown device')
        return SyncthingHandler.ControlFolder(fid='control-%s' % hashlib.sha1((':'.join([self.__server_secret, devid])).encode('UTF-8')).hexdigest(),
                                              path=os.path.join(self.data_root, 'control', devid)
                                              )
        #return self.__devices[device].get('controlfolder', None)

    def get_servers(self):
        return self.__servers

    def get_folders(self):
        return self.__folders

    @async_method
    def addServer(self, deviceid):
        return self.__interface_addServer(deviceid)

    @async_method
    def addDevice(self, deviceid):
        return self.__interface_addDevice(deviceid)

    @async_method
    def addFolder(self, folderPath, label, devList=None):
        return self.__interface_addFolder(folderPath, label, devList)

    @async_method
    def setServerSecret(self, secret):
        assert isinstance(secret, str), 'secret must be a str'
        self.__server_secret = secret
        self.__save_configuration()
        self.__reload_configuration()

    @async_method
    def reload_configuration(self):
        return self.__reload_configuration()

    def __interface_addDevice(self, deviceid):
        if not self.isServer():
            raise RuntimeError('device list is provided by server')
        if deviceid in self.__devices:
            return
        self.__devices[deviceid] = Device(deviceid)
        self.__save_configuration()
        self.__save_st_config()
        if self.isServer():
            for dev in self.__devices:
                self.__save_device_configuration(dev)

    def __interface_addServer(self, deviceid):
        if deviceid in self.__servers:
            return
        if deviceid not in self.__devices:
            self.__devices[deviceid] = Device(deviceid)
        self.__servers.add(deviceid)

        self.__save_configuration()
        self.__save_st_config()
        if self.isServer():
            for dev in self.__devices:
                self.__save_device_configuration(dev)

    def __interface_addFolder(self, folderPath, label, devList=None):
        if devList is None:
            devList = []
        assert isinstance(label, str), 'label must be string'
        assert isinstance(devList, list) or isinstance(devList, set) or isinstance(devList, tuple), 'devList must be a list'
        assert isinstance(folderPath, str), 'folderPath must be a string'

        for dev in devList:
            if dev not in self.__devices:
                raise RuntimeError('device %s does not belong to this server' % dev)

        if len([x for x in self.__folders if self.__folders[x].path() == folderPath]) > 0:
            return

        fid = 'folder-' + ''.join(random.choice(string.ascii_lowercase) for _ in range(16))
        self.__folders[fid] = Folder(fid, label, folderPath, devList)
            #{'attribs': {'path': folderPath, 'label':label}, 'devices': set(devList)}

        self.__save_configuration()
        self.__save_st_config()
        if self.isServer():
            for dev in devList:
                self.__save_device_configuration(dev)
        return fid

    def __reload_configuration(self):
        """
        loads server/client configuration, ensures configuration is up to date
        :return: bool if configuration has changed
        """
        self.__log(2, 'reloading configuration')
        if not os.path.exists(os.path.join(self.config_root, 'syncthinghandler_config.json')):  # initialization time!
            self.__generateInitialConfig()

        with open(os.path.join(self.config_root, 'syncthinghandler_config.json'), 'r') as f:
            config = json.load(f)
            self.__log(1, config)

        configChanged = False

        def checkAndReturn(oldval, newval):  #TODO: we actually use this twice or so - do we even need it?
            nonlocal configChanged
            configChanged = configChanged or oldval != newval
            return newval

        self.__apikey = checkAndReturn(self.__apikey, config['apikey'])
        self.__log(1, 'api key = %s' % self.__apikey)
        self.httpheaders = {'X-API-Key': self.__apikey, 'Content-Type': 'application/json'}
        self.__server_secret = checkAndReturn(self.__server_secret, config.get('server_secret', None))
        self.__log(1, "server secret = %s" % self.__server_secret)
        if self.__server_secret is None:
            self.__isValidState = False
            raise SyncthingHandlerConfigError()
        oldservers = self.__servers
        olddevices = self.__devices
        oldfolders = self.__folders
        oldignoredevices = self.__ignoreDevices

        if len(self.__servers) == 0:  # Initial config loading
            self.__servers = set(config.get('servers', ()))  # bootstrap to have isServer resolving correctly
            self.__log(1, 'server list loaded from bootstrap config')
        self.__log(1, 'server list:', self.__servers)

        configFoldPath = os.path.join(self.get_config_folder().path(), 'configuration')
        self.__log(1, 'configuration path: %s' % configFoldPath)
        # if not os.path.exists(configFoldPath):
        #     for fpath in (configFoldPath,
        #                   os.path.join(configFoldPath, 'servers'),
        #                   os.path.join(configFoldPath, 'devices'),
        #                   os.path.join(configFoldPath, 'folders'),
        #                   os.path.join(configFoldPath, 'ignoredevices')):
        #         try:
        #             os.makedirs(fpath)
        #         except OSError as e:
        #             if e.errno != errno.EEXIST:
        #                 raise

        try:
            self.__servers = set(listdir(os.path.join(configFoldPath, 'servers')))
            self.__log(1, 'final server list:', self.__servers)
            self.__devices = {}
            for dev in listdir(os.path.join(configFoldPath, 'devices')):
                with open(os.path.join(configFoldPath, 'devices', dev), 'r') as f:
                    fdata = json.load(f)
                self.__devices[dev] = Device(dev, fdata.get('name', None))
                #if isServer:
                #    self.__devices[dev]['controlfolder'] = {'fid': 'control-%s' % hashlib.sha1((':'.join([self.__server_secret, dev])).encode('UTF-8')).hexdigest(),
                #                                            'path': os.path.join(self.data_root, 'control', dev)
                #                                            }  # ensure controlfolder attr exist
            self.__log(1, 'final device list:', self.__devices.keys())

            # check server-dev list consistency
            for srv in set(self.__servers):
                if srv not in self.__devices:
                    self.__log(4, 'server %s not in device list! skipping...' % srv)
                    self.__servers.remove(srv)

            self.__folders = {}
            for fid in listdir(os.path.join(configFoldPath, 'folders')):
                with open(os.path.join(configFoldPath, 'folders', fid, 'attribs'), 'r') as f:
                    fattrs = json.load(f)
                self.__folders[fid] = Folder(fid, fattrs['label'], config.get('folders', {}).get(fid, {}).get('attribs', {}).get('path', None))  # TODO: if path changed suddenly and st noticed it - it will give error about missing .stfolder. We have to deal with this one way or another
                for dev in listdir(os.path.join(configFoldPath, 'folders', fid, 'devices')):
                    if dev not in self.__devices:
                        self.__log(4, 'folder device %s is not part of device list. skipping...' % dev)
                        continue
                    self.__folders[fid].add_device(dev)

                if self.__folders[fid].path() is None:
                    # TODO: add an option to control this, allow folders to stay without path
                    # TODO: ensure path does not exist already
                    self.__folders[fid].set_path(os.path.join(self.data_root, self.__folders[fid].label()))

            self.__ignoreDevices = set()
            for dev in listdir(os.path.join(configFoldPath, 'ignoredevices')):
                self.__ignoreDevices.add(dev)
        except:  # config folder is malformed. try load cached servers and wait for config sync
            raise

            #TODO: save local json config, just servers, all else should be empty

        configChanged = configChanged or oldservers != self.__servers or olddevices != self.__devices or oldfolders != self.__folders or oldignoredevices != self.__ignoreDevices

        self.__configInSync = True
        if configChanged:
            print('state changed, resaving st config')
            self.__save_st_config()
            if self.isServer():
                for dev in self.__devices:
                    self.__save_device_configuration(dev)
        return configChanged

    def __save_device_configuration(self, dev):
        assert self.isServer(), "must be server to save config for devices"
        assert dev in self.__devices, "unknown device"

        devfids = [fid for fid in self.__folders if dev in self.__folders[fid].devices()]
        devfiddevs = {}  # all devices that share allowed folders
        for fid in devfids:
            devfiddevs.update({dev: self.__devices[dev] for dev in self.__folders[fid].devices()})

        for srvid in self.__servers:  # add servers to all devices list
            devfiddevs[srvid] = self.__devices[srvid]

        configFoldPath = os.path.join(self.get_config_folder(dev).path(), 'configuration')
        _devpath = os.path.join(configFoldPath, 'devices')
        shutil.rmtree(_devpath, ignore_errors=True)  # clear existing

        _srvpath = os.path.join(configFoldPath, 'servers')
        shutil.rmtree(_srvpath, ignore_errors=True)  # clear existing

        _fldpath = os.path.join(configFoldPath, 'folders')
        shutil.rmtree(_fldpath, ignore_errors=True)  # clear existing

        _ignpath = os.path.join(configFoldPath, 'ignoredevices')
        shutil.rmtree(_ignpath, ignore_errors=True)  # clear existing

        os.makedirs(_devpath)
        os.makedirs(_srvpath)
        os.makedirs(_fldpath)
        os.makedirs(_ignpath)

        # save servers
        for server in self.__servers:
            os.mknod(os.path.join(_srvpath, server))
        # save devices
        for dev in devfiddevs:
            with open(os.path.join(_devpath, dev), 'w') as f:
                f.write(devfiddevs[dev].serialize())
        # save folders
        for fid in devfids:
            fiddevpath = os.path.join(_fldpath, fid, 'devices')
            shutil.rmtree(fiddevpath, ignore_errors=True)
            os.makedirs(fiddevpath)
            for dev in self.__folders[fid].devices():
                os.mknod(os.path.join(fiddevpath, dev))
            with open(os.path.join(_fldpath, fid, 'attribs'), 'w') as f:
                json.dump({'fid': fid, 'label': self.__folders[fid].label()}, f)
        # save ign dev
        for dev in self.__ignoreDevices:
            os.mknod(os.path.join(_ignpath, dev))


    def __save_configuration(self):
        """
        server saves configuration to shared server folder
        both server and client dumps cache of current config to json file, though  it should only be used as abootstrap
        :return:
        """
        # check consistency, just in case
        for srv in tuple(self.__servers):
            if srv not in self.__devices:
                self.__log(4, 'presave check: server %s not in device list! skipping...' % srv)
                self.__servers.remove(srv)

        for fid in self.__folders:
            for dev in tuple(self.__folders[fid].devices()):
                if dev not in self.__devices:
                    self.__log(4, 'presave check: folder device %s is not part of device list. skipping...' % dev)
                    self.__folders[fid].remove_device(dev)

        try:
            if self.syncthing_running():
                self.__post('/rest/system/pause', {})

            config = {}
            config['apikey'] = self.__apikey
            config['server_secret'] = self.__server_secret

            # only bootstrap and local info is saved here, like servers list and folder paths
            config['servers'] = list(self.__servers)
            config['devices'] = {id: {'id': self.__devices[id].id(), 'name': self.__devices[id].name()} for id in self.__devices}
            config['folders'] = {id: {'attribs': {'path': self.__folders[id].path()}} for id in self.__folders}
            #for fid in config['folders']:
            #    config['folders'][fid]['devices'] = list(config['folders'][fid]['devices'])
            config['ignoreDevices'] = list(self.__ignoreDevices)

            with open(os.path.join(self.config_root, 'syncthinghandler_config.json'), 'w') as f:
                json.dump(config, f, indent=4)

            configFoldPath = os.path.join(self.get_config_folder().path(), 'configuration')

            _devpath = os.path.join(configFoldPath, 'devices')
            shutil.rmtree(_devpath, ignore_errors=True)  # clear existing

            _srvpath = os.path.join(configFoldPath, 'servers')
            shutil.rmtree(_srvpath, ignore_errors=True)  # clear existing

            _fldpath = os.path.join(configFoldPath, 'folders')
            shutil.rmtree(_fldpath, ignore_errors=True)  # clear existing

            _ignpath = os.path.join(configFoldPath, 'ignoredevices')
            shutil.rmtree(_ignpath, ignore_errors=True)  # clear existing

            os.makedirs(_devpath)
            os.makedirs(_srvpath)
            os.makedirs(_fldpath)
            os.makedirs(_ignpath)

            # save servers
            for server in self.__servers:
                os.mknod(os.path.join(_srvpath, server))
            # save devices
            for dev in self.__devices:
                with open(os.path.join(_devpath, dev), 'w') as f:
                    f.write(self.__devices[dev].serialize())
                    #json.dump(self.__devices[dev], f)
            # save folders
            for fid in self.__folders:
                fiddevpath = os.path.join(_fldpath, fid, 'devices')
                shutil.rmtree(fiddevpath, ignore_errors=True)
                os.makedirs(fiddevpath)
                for dev in self.__folders[fid].devices():
                    os.mknod(os.path.join(fiddevpath, dev))
                with open(os.path.join(_fldpath, fid, 'attribs'), 'w') as f:
                    json.dump({'fid': fid, 'label': self.__folders[fid].label()}, f)
            # save ign dev
            for dev in self.__ignoreDevices:
                os.mknod(os.path.join(_ignpath, dev))
        finally:
            if self.syncthing_running():
                self.__post('/rest/system/resume', {})

    def __save_st_config(self):
        """
        saves current configuration to syncthing config and restarts syncthing if needed
        :return:
        """
        conf = ET.Element('configuration', {'version': '28'})

        servers = self.__servers
        devices = self.__devices
        blacklist = self.__ignoreDevices
        folders = self.__folders

        if self.isServer():
            serverConfigFolder = self.get_config_folder()
            #fid = 'server_configuration-%s' % hashlib.sha1(self.__server_secret.encode('UTF-8')).hexdigest()
            sconf = ET.SubElement(conf, 'folder', {'id': serverConfigFolder.fid(), 'label': 'server configuration', 'path': serverConfigFolder.path(), 'type': 'sendreceive'})
            ET.SubElement(sconf, 'maxConflicts').text = '0'
            os.makedirs(serverConfigFolder.path(), exist_ok=True)


            for dev in devices:
                dvc = ET.SubElement(conf, 'device', {'id': dev, 'name': devices[dev].name(), 'compression': 'metadata', 'introducer': 'false'})
                ET.SubElement(dvc, 'address').text = 'dynamic'

                if dev in self.__servers:
                    continue  # dont create control folders for servers

                # now create control folder
                controlfolder = self.get_config_folder(dev)

                #fid = 'control-%s' % hashlib.sha1((':'.join([self.__server_secret, dev])).encode('UTF-8')).hexdigest()  # ensure same fid for client and server
                foldelem = ET.SubElement(conf, 'folder', {'id': controlfolder.fid(),
                                                          'label': 'control for %s' % dev,
                                                          'path': controlfolder.path(),
                                                          'type': 'sendreceive',
                                                          'rescanIntervalS': '3600',
                                                          'fsWatcherEnabled': 'true',
                                                          'fsWatcherDelayS': '10',
                                                          'ignorePerms': 'true',
                                                          'autoNormalize': 'true'
                                                          })
                ET.SubElement(foldelem, 'maxConflicts').text = '0'
                ET.SubElement(foldelem, 'device', {'id': dev})  # add the device we will control
                for sdev in servers:
                    ET.SubElement(foldelem, 'device', {'id': sdev})  # add all the servers

                os.makedirs(controlfolder.path(), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'active'), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'archive'), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'configuration'), exist_ok=True)

        else:  # not server
            for dev in devices:
                #if dev == self.myId():  # just in case
                #    continue
                dvc = ET.SubElement(conf, 'device', {'id': dev, 'name': devices[dev].name(), 'compression': 'metadata', 'introducer': 'false'})
                ET.SubElement(dvc, 'address').text = 'dynamic'

            controlfolder = self.get_config_folder()

            #fid = 'control-%s' % hashlib.sha1((':'.join([self.__server_secret, self.__myid])).encode('UTF-8')).hexdigest()  # ensure same fid for client and server
            foldelem = ET.SubElement(conf, 'folder', {'id': controlfolder.fid(),
                                                      'label': 'control for %s' % self.__myid,
                                                      'path': controlfolder.path(),
                                                      'type': 'sendreceive'
                                                      })
            ET.SubElement(foldelem, 'maxConflicts').text = '0'
            ET.SubElement(foldelem, 'device', {'id': self.__myid})
            for server in servers:
                ET.SubElement(foldelem, 'device', {'id': server})

            os.makedirs(controlfolder.path(), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'active'), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'archive'), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'configuration'), exist_ok=True)

        for dev in blacklist:
            ET.SubElement(conf, 'ignoredDevice').text = dev

        for fol in folders:
            foldc = ET.SubElement(conf, 'folder', {'id': fol,
                                                   'label': folders[fol].label(),
                                                   'path': folders[fol].path(),
                                                   'type': 'sendreceive',
                                                   'rescanIntervalS': str(3600),
                                                   'fsWatcherEnabled': 'true',
                                                   'fsWatcherDelayS': str(10),
                                                   'ignorePerms': 'true',
                                                   'autoNormalize': "true"
                                                   })
            for server in servers:
                ET.SubElement(foldc, 'device', {'id': server})

            for dev in folders[fol].devices():
                if dev in servers:
                    continue
                ET.SubElement(foldc, 'device', {'id': dev})

            os.makedirs(folders[fol].path(), exist_ok=True)


        guielem = ET.SubElement(conf, 'gui', {'enabled': 'true', 'tls': 'false', 'debugging': 'true'})
        ET.SubElement(guielem, 'address').text = '%s:%d' % (self.syncthing_ip, self.syncthing_port)
        ET.SubElement(guielem, 'apikey').text = self.__apikey

        optelem = ET.SubElement(conf, 'options')
        ET.SubElement(optelem, 'listenAddress').text = self.syncthing_listenaddr

        st_conffile = os.path.join(self.config_root, 'config.xml')

        # ET.tostring(conf, encoding='UTF-8') #
        conftext = minidom.parseString(ET.tostring(conf, encoding='UTF-8')).toprettyxml()

        dorestartst = self.syncthing_running()
        if dorestartst:
            self.__stop_syncthing()
        with open(st_conffile, 'w') as f:
            f.write(conftext)
        if dorestartst:
            self.__start_syncthing()
        #TODO: IMPROVE: its faster to get-modify-post config, but that config is in json format, and file is in xml... ffs

        if self.syncthing_proc is not None and not self.__isValidState:
            self.__stop_syncthing()

    def __start_syncthing(self):
        if not self.syncthing_proc or self.syncthing_proc.poll() is not None:
            self.syncthing_proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-browser', '-no-restart', '-gui-address={addr}:{port}'.format(addr=self.syncthing_ip, port=self.syncthing_port)])
            return True
        return False

    def __stop_syncthing(self):
        if self.syncthing_proc:
            self.syncthing_proc.terminate()
            self.syncthing_proc.wait()
            self.syncthing_proc = None
            return True
        return False

    def syncthing_running(self):
        return self.syncthing_proc is not None and self.syncthing_proc.poll() is None

    def __get(self, path, **kwargs):
        if self.syncthing_proc is None:
            raise SyncthingNotReadyError()
        url = "http://%s:%d%s" % (self.syncthing_ip, self.syncthing_port, path)
        if len(kwargs) > 0:
            url += '?' + '&'.join(['%s=%s' % (k, str(kwargs[k])) for k in kwargs.keys()])
        self.__log(0, "getting %s" % url)
        self.__log(0, self.httpheaders)
        req = requester.Request(url, headers=self.httpheaders)
        rep = requester.urlopen(req)
        data = json.loads(rep.read())
        return data

    def __post(self, path, data):
        if self.syncthing_proc is None:
            raise SyncthingNotReadyError()
        req = requester.Request("http://%s:%d%s" % (self.syncthing_ip, self.syncthing_port, path), data, headers=self.httpheaders)
        req.get_method = lambda: 'POST'
        rep = requester.urlopen(req)
        return json.loads(rep.read())

    @async_method
    def get(self, path, **kwargs):
        return self.__get(path, **kwargs)

    @async_method
    def post(self, path, data):
        return self.__post(path, data)

import sys
import os
import shutil
import errno
import copy
import subprocess
import threading
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
from .lance_utils import async_method, AsyncMethodBatch
from .eventtypes import *
from . import eventprocessor
from .logger import get_logger

from typing import Union, Optional, Iterable, Set, Dict


def listdir(path):
    return filter(lambda x: re.match(r'^\.syncthing\..*\.tmp$', x) is None, os.listdir(path))


#  EXCEPTIONS
class SyncthingNotReadyError(RuntimeError):
    pass


class SyncthingHandlerConfigError(RuntimeError):
    pass


class NoInitialConfiguration(RuntimeError):
    pass

class ConfigNotInSyncError(RuntimeError):
    pass


#  HELPERS
class DeviceVolatileData:
    # explicitly state names of methods, not use __getattr__, to help ourselves later with static code analisys
    def __init__(self):
        self.__data = {'addr': '',
                       'paused': False,
                       'connected': False,
                       'clientName': '',
                       'clientVersion': ''}

    def _update_data(self, data):
        self.__data.update(data)

    def address(self):
        return self.__data.get('addr', '')

    def paused(self):
        return self.__data.get('paused', False)

    def connected(self):
        return self.__data.get('connected', False)

    def client_name(self):
        return self.__data.get('clientName', '')

    def client_vertion(self):
        return self.__data.get('clientVersion', '')

    def get(self, key, default=None):
        return self.__data.get(key, default)

    def keys(self):
        return self.__data.keys()

    def __len__(self):
        return len(self.__data)


class FolderVolatileData:
    # explicitly state names of methods, not use __getattr__, to help ourselves later with static code analisys
    def __init__(self):
        self.__data = {'globalBytes': 0,
                       'inSyncBytes': 0,
                       'connected': False,
                       'needBytes': 0,
                       'needFiles': 0,
                       'needTotalItems': 0,
                       'state': '',
                       'stateChanged': '',
                       'version': 0}

    def _update_data(self, data):
        self.__data.update(data)

    def global_bytes(self):
        return self.__data.get('globalBytes', 0)

    def in_sync_bytes(self):
        return self.__data.get('inSyncBytes', 0)

    def connected(self):
        return self.__data.get('connected', False)

    def need_bytes(self):
        return self.__data.get('needBytes', 0)

    def need_total_items(self):
        return self.__data.get('needTotalItems', 0)

    def state(self):
        return self.__data.get('state', '')

    def state_changed(self):
        return self.__data.get('stateChanged', '')

    def version(self):
        return self.__data.get('version', 0)

    def get(self, key, default):
        return self.__data.get(key, default)

    def keys(self):
        return self.__data.keys()

    def __len__(self):
        return len(self.__data)


class Device:
    def __init__(self, sthandler: 'SyncthingHandler', id: str, name: Optional[str] = None):
        self.__stid = id  # type: str
        self.__name = name  # type: str
        self.__sthandler = sthandler
        self.__volatiledata = DeviceVolatileData()
        self.__ismyself = self.__sthandler is not None and self.__sthandler.myId() == id
        self.__delete_on_sync_after = None

    def volatile_data(self):
        return self.__volatiledata

    def _update_volatile_data(self, data):
        self.__volatiledata._update_data(data)

    def force_reload_volatile_data(self):
        if self.__sthandler is None:
            raise RuntimeError('SyncthingHandler is not set on Device')
        #TODO: do the reload
        raise NotImplementedError()

    def id(self) -> str:
        """
        :return: syncthing id 
        """
        return self.__stid

    def _setName(self, newname: str):
        """
        this should only be called from syncthingHandler, not from outside
        set name, no fancy callbacks
        """
        self.__name = newname

    def name(self) -> str:
        """
        :return: human readable name of the device
        """
        if self.__ismyself:
            return 'myself'
        return self.__name if self.__name is not None else 'device %s' % self.__stid[:6]

    def schedule_for_deletion(self):
        if self.__delete_on_sync_after is None:
            self.__delete_on_sync_after = time.time()

    def unschedule_for_deletion(self):
        self.__delete_on_sync_after = None

    def is_schediled_for_deletion(self):
        return self.__delete_on_sync_after is not None

    def get_delete_after_time(self):
        return self.__delete_on_sync_after

    def __eq__(self, other):  # comparing all but volatile data, like connection state
        return other is not None and\
               self.__stid == other.__stid and \
               self.__name == other.__name and \
               self.__delete_on_sync_after == other.__delete_on_sync_after  # TODO: should name be a part of this? on one hand - yes, cuz we need it to detect changes in config

    def __deepcopy__(self, memodict=None):
        newone = copy.copy(self)
        newone.__volatiledata = copy.deepcopy(self.__volatiledata, memo=memodict)
        return newone

    def __copy__(self):
        newone = Device(self.__sthandler, self.__stid, self.__name)
        newone.__volatiledata = self.__volatiledata
        return newone

    def serialize(self) -> str:
        return json.dumps({"id": self.__stid,
                           "name": self.__name,
                           "delete_on_sync_after": self.__delete_on_sync_after
                           })

    @classmethod
    def deserialize(cls, sthandler, s: Union[str, dict]):
        if isinstance(s, str):
            data = json.loads(s)
        else:
            data = s
        newdev = Device(sthandler, data['id'], data['name'])
        newdev.__delete_on_sync_after = data['delete_on_sync_after']
        return newdev


class Folder:
    def __init__(self, sthandler: 'SyncthingHandler', id: str, label: str, path: Optional[str] = None, devices: Optional[Iterable] = None, metadata=None):
        self.__stfid = id
        self.__label = label
        self.__path = path
        self.__devices = set(devices) if devices is not None else set()
        self.__sthandler = sthandler
        self.__volatiledata = DeviceVolatileData()
        if metadata is None:
            metadata = {}
        else:
            metadata = copy.deepcopy(metadata)
        self.__metadata = metadata

    def _setMetadata(self, metadata):
        """
        supposed to be called from SyncthingHandler
        DOES NOT updates syncthinghandler itself
        """
        self.__metadata = copy.deepcopy(metadata)

    def metadata(self):
        """
        this is supposed to be immutable,
        """
        return self.__metadata

    def is_synced(self) -> bool:
        return self.__volatiledata.get('summary', {}).get('needTotalItems', -1) == 0

    def volatile_data(self):
        return self.__volatiledata

    def _updateVolatileData(self, data):
        self.__volatiledata._update_data(data)

    def force_reload_volatile_data(self):
        if self.__sthandler is None:
            raise RuntimeError('SyncthingHandler is not set on Folder')
        #TODO: do the reload
        raise NotImplementedError()

    def id(self) -> str:
        return self.__stfid

    def label(self) -> str:
        return self.__label

    def path(self) -> Optional[str]:
        return self.__path

    def _setPath(self, path: Optional[str], move_contents=True) -> None:
        """
        supposed to be called from SyncthingHandler
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

    def __eq__(self, other):  # comparing all but volatile data, like connection state
        return other is not None and\
               self.__stfid == other.__stfid and \
               self.__label == other.__label and \
               self.__path == other.__path and \
               self.__devices == other.__devices and \
               self.__metadata == other.__metadata

    def __deepcopy__(self, memodict=None):
        newone = copy.copy(self)
        newone.__metadata = copy.deepcopy(self.__metadata)
        newone.__volatiledata = copy.deepcopy(self.__volatiledata, memo=memodict)
        newone.__devices = copy.deepcopy(self.__devices, memo=memodict)  # deepcopy of devices
        return newone

    def __copy__(self):
        newone = Folder(self.__sthandler, self.__stfid, self.__label, self.__path, self.__devices)  # devices will be same but in a new set
        newone.__metadata = copy.copy(self.__metadata)
        newone.__volatiledata = self.__volatiledata
        return newone


# Events
class ConfigurationEvent(BaseEvent):
    def __init__(self, source: str):
        super(ConfigurationEvent, self).__init__()
        self.__source = source

    def  source(self):
        return self.__source


class DevicesConfigurationEvent(ConfigurationEvent):
    def __init__(self, devices: Iterable[Device], source: str):
        super(DevicesConfigurationEvent, self).__init__(source)
        self.__devices = tuple(devices)

    def devices(self):
        return self.__devices

    def __repr__(self):
        return '<{typename}>: devs=({names})'.format(typename=type(self).__name__, names=', '.join(map(lambda x: x.name(), self.__devices)))


class DevicesAddedEvent(DevicesConfigurationEvent):
    pass


class DevicesRemovedEvent(DevicesConfigurationEvent):
    pass


class DevicesChangedEvent(DevicesConfigurationEvent):
    pass


class DevicesVolatileDataChangedEvent(DevicesConfigurationEvent):
    pass


class FoldersConfigurationEvent(ConfigurationEvent):
    def __init__(self, folders: Iterable[Folder], source: str):
        super(FoldersConfigurationEvent, self).__init__(source)
        self.__folders = tuple(folders)

    def folders(self):
        return self.__folders

    def __repr__(self):
        return '<{typename}>: ({folderslist})'.format(typename=type(self).__name__, folderslist=', '.join(map(lambda x: x.label(), self.__folders)))


class FoldersAddedEvent(FoldersConfigurationEvent):
    pass


class FoldersRemovedEvent(FoldersConfigurationEvent):
    pass


class FoldersConfigurationChangedEvent(FoldersConfigurationEvent):
    pass


class FoldersVolatileDataChangedEvent(FoldersConfigurationEvent):
    pass


class FoldersSyncedEvent(FoldersConfigurationEvent):
    pass


class ConfigSyncChangedEvent(BaseEvent):
    def __init__(self, insync: bool):
        super(ConfigSyncChangedEvent, self).__init__()
        self.__insync = insync

    def in_sync(self) -> bool:
        return self.__insync

    def progress(self) -> float:
        return 1 if self.__insync else 0

    def __repr__(self):
        return '<{typename}: sync={sync}>'.format(typename=type(self).__name__, sync=repr(self.__insync))


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

    class SyncthingPauseLock:
        __lockSet = set()

        def __init__(self, sthandler: 'SyncthingHandler'):
            self.__sthandler = sthandler
            self.__doUnpause = False

        def __enter__(self):
            if not self.__sthandler.syncthing_running() or self.__sthandler in SyncthingHandler.SyncthingPauseLock.__lockSet:
                return
            SyncthingHandler.SyncthingPauseLock.__lockSet.add(self.__sthandler)
            self.__sthandler._SyncthingHandler__pause_syncthing()
            self.__doUnpause = True

        def __exit__(self, exc_type, exc_val, exc_tb):
            if not self.__doUnpause:
                return
            SyncthingHandler.SyncthingPauseLock.__lockSet.remove(self.__sthandler)
            self.__sthandler._SyncthingHandler__resume_syncthing()


    class ConfigMethodsBatch(AsyncMethodBatch):
        def __init__(self, sthandler: 'SyncthingHandler'):
            super(SyncthingHandler.ConfigMethodsBatch, self).__init__(sthandler)
            self.__sthandler = sthandler

        def __enter__(self):
            ret = super(SyncthingHandler.ConfigMethodsBatch, self).__enter__()
            self._methodbatch_hold_st_update()
            return ret

        def __exit__(self, exc_type, exc_val, exc_tb):
            self._methodbatch_resume_st_update()
            return super(SyncthingHandler.ConfigMethodsBatch, self).__exit__(exc_type, exc_val, exc_tb)

    def __init__(self, server):
        super(SyncthingHandler, self).__init__(server)

        self.__log = get_logger(self.__class__.__name__)
        self.__log.min_log_level = 1

        self.__myid_lock = threading.Lock()
        self.syncthing_bin = r'syncthing'
        self.data_root = server.config['data_root']  # os.path.join(os.path.split(os.path.abspath(__file__))[0], r'data')
        self.config_root = server.config['config_root']  # os.path.join(os.path.split(os.path.abspath(__file__))[0], r'config')

        self.syncthing_gui_ip = '127.0.0.1'
        self.syncthing_gui_port = 9394 + int(random.uniform(0, 1000))
        self.syncthing_listenaddr = "tcp4://127.0.0.1:%d" % int(random.uniform(22000, 23000))
        self.syncthing_proc = None
        self.__servers = set()  # set of ids in __devices dict that are servers
        self.__devices = {}  # type: Dict[str, Device]
        self.__folders = {}  # type: Dict[str, Folder]
        self.__ignoreDevices = set()  # set of devices

        self.__apikey = None
        self.__myid = None
        self.__server_secret = None

        self._last_event_id = 0

        self.__defer_stupdate = False
        self.__defer_stupdate_writerequired = False

        self.__isValidState = True
        self.__configInSync = False
        self.__reload_configuration()
        if self._isServer():  # register special server event processors
            self.__updateClientConfigs()
        self.__log = get_logger('%s %s' % (self.myId()[:5], self.__class__.__name__))
        self.__log.min_log_level = 1
        self.__st_config_locks = []

    def start(self):
        super(SyncthingHandler, self).start()
        self.__start_syncthing()

    def stop(self):
        super(SyncthingHandler, self).stop()
        self.__stop_syncthing()

    def myId(self):
        with self.__myid_lock:
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

    def _isServer(self):
        return self.myId() in self.__servers

    def _enqueueEvent(self, event):
        self.__log(1, 'enqueuing event: %s' % repr(event))
        super(SyncthingHandler, self)._enqueueEvent(event)

    def _runLoopLoad(self):
        while True:
            # TODO: check for device/folder connection events to check for blacklisted, just in case
            if self.syncthing_proc is not None and self.__isValidState:  # TODO: skip this for some set time to wait for events to accumulate
                try:
                    stevents = self.__get('/rest/events', since=self._last_event_id, timeout=2)  # TODO: get events in async way
                except:
                    time.sleep(2)
                    yield
                    continue

                self.__log(0, "syncthing event", stevents)
                current_session = hash(self.syncthing_proc)
                # loop through rest events and pack them into lance events
                if stevents is None:
                    #time.sleep(2)  # no need to sleep - __get has timeout
                    yield
                    continue

                if len(stevents) > 0:
                    self._last_event_id = max(stevents, key=lambda x: x['id'])['id']

                for stevent in stevents:
                    # filter and pack events into our wrapper

                    eventtime_datetime = datetime.datetime.strptime(re.sub('\d{3}(?=[\+-]\d{2}:\d{2}$)', '', stevent['time']), '%Y-%m-%dT%H:%M:%S.%f%z')
                    eventtime = eventtime_datetime.timestamp()
                    #just fancy logging:
                    logtext = 'event type "%s" from %s' % (stevent['type'], eventtime_datetime.strftime('%H:%M:%S.%f'))
                    if stevent['type'] == 'FolderSummary':
                        logtext += ' %s: %d' % (stevent['data']['folder'], stevent['data']['summary']['needTotalItems'])
                    elif stevent['type'] == 'ItemStarted':
                        logtext += '%s: %s: %s' % (stevent['data']['folder'], stevent['data']['action'], stevent['data']['item'])
                    self.__log(1, logtext)

                    if stevent['type'] == 'StartupComplete':
                        # syncthing loaded. either first load, or syncthing restarted after configuration save
                        # must check configuration
                        self.__log(1, 'StartupComplete event received, config in sync =%s' % repr(self.__configInSync))
                        try:
                            configstatus = self.__get('/rest/db/status', folder=self.get_config_folder().fid())
                            configsynced = configstatus['needTotalItems'] == 0
                            self.__log(1, 'probed config folder status, synced =%s' % repr(configsynced))
                            if self.__configInSync != configsynced:
                                self.__configInSync = configsynced
                                if self.__configInSync:
                                    try:
                                        self.__reload_configuration()
                                    except Exception as e:
                                        self.__log(2, 'config reload failed cuz of Exception %s probably being updated by syncthing' % repr(e))
                                        self.__configInSync = False
                                self._enqueueEvent(ConfigSyncChangedEvent(self.__configInSync))
                        except requester.HTTPError as e:
                            if e.code == 404:  # looks like syncthing config was not saved. how did this happen??
                                self.__log(4, 'syncthing config was not properly initialized')
                                self.__stop_syncthing()
                                self.__generateInitialConfig()
                                self.__start_syncthing()
                                return  # drop existing events, we will get new StartupComplete event
                            else:
                                raise

                    # Config sincronization event processing
                    elif self.__configInSync and stevent['type'] == 'ItemStarted' and stevent['data']['folder'] == self.get_config_folder().fid():  # need to send him the configuration
                        self.__log(1, 'starting to sync server configuration, config in sync = False')
                        self.__configInSync = False
                        self._enqueueEvent(ConfigSyncChangedEvent(False))
                    elif not self.__configInSync and stevent['type'] == 'FolderSummary' and stevent['data']['folder'] == self.get_config_folder().fid():
                        data = stevent['data']
                        if data['summary']['needTotalItems'] == 0:
                            self.__log(1, 'server configuration sync completed, config in sync = True')
                            self.__configInSync = True
                            try:
                                self.__reload_configuration()  # TODO: add parameter to nobootstrap, cuz we need to override bootstrap at this point
                                self.__save_bootstrapConfig()
                            except Exception as e:
                                self.__log(2, 'config reload failed cuz of %s. probably being updated by syncthing' % repr(e))
                                self.__configInSync = False
                            else:
                                self._enqueueEvent(ConfigSyncChangedEvent(True))

                    # Folder Statue event
                    elif stevent['type'] == 'FolderSummary':
                        fid = stevent['data']['folder']
                        if fid in self.__folders:
                            self.__folders[fid]._updateVolatileData(stevent['data'])
                            self.__log(1, repr(self.__folders[fid].volatile_data()))
                            fcopy = copy.deepcopy(self.__folders[fid])
                            self._enqueueEvent(FoldersVolatileDataChangedEvent((fcopy,), 'syncthing::event'))
                            if stevent['data']['summary']['needTotalItems'] == 0:
                                self._enqueueEvent(FoldersSyncedEvent((fcopy,), 'syncthing::event'))

                    # Device Status event
                    elif stevent['type'] == 'DeviceConnected':
                        did = stevent['data']['id']
                        if did in self.__devices:
                            self.__devices[did]._update_volatile_data(stevent['data'])
                            self.__devices[did]._update_volatile_data({'connected': True, 'error': None})
                            self.__log(1, repr(self.__devices[did].volatile_data()))
                            self._enqueueEvent(DevicesVolatileDataChangedEvent((copy.deepcopy(self.__devices[did]),), 'syncthing::event'))
                    elif stevent['type'] == 'DeviceDisconnected':
                        did = stevent['data']['id']
                        if did in self.__devices:
                            self.__devices[did]._update_volatile_data(stevent['data'])
                            self.__devices[did]._update_volatile_data({'connected': False})
                            self.__log(1, repr(self.__devices[did].volatile_data()))
                            self._enqueueEvent(DevicesVolatileDataChangedEvent((copy.deepcopy(self.__devices[did]),), 'syncthing::event'))
                    elif stevent['type'] == 'DeviceDiscovered':
                        did = stevent['data']['device']
                        if did in self.__devices:
                            self.__devices[did]._update_volatile_data(stevent['data'])
                            self.__log(1, repr(self.__devices[did].volatile_data()))
                            self._enqueueEvent(DevicesVolatileDataChangedEvent((copy.deepcopy(self.__devices[did]),), 'syncthing::event'))
                    elif stevent['type'] == 'FolderCompletion' and \
                            stevent['data']['device'] in self.__devices and \
                            self.__devices[stevent['data']['device']].is_schediled_for_deletion() and \
                            stevent['data']['folder'] == self.get_config_folder(stevent['data']['device']).fid():
                        did = stevent['data']['device']
                        self.__log(1, 'FolderCompletion event for a device scheduled for deletion. c=%d' % stevent['data']['completion'])
                        if stevent['data']['completion'] == 100 and eventtime > self.__devices[did].get_delete_after_time():
                            # so device is synced and now can be safely deleted
                            self.__log(1, 'now safe to delete device %s' % did)
                            del self.__devices[did]
                            self.__save_configuration(save_st_config=True)
                            #self.__save_st_config()
                            # Note that we don't update any device config, cuz if device scheduled for deletion - it must have already been removed from everything
                            # so here we just do sanity check
                            for fid, folder in self.__folders.items():
                                assert did not in folder.devices()
                    #elif stevent['type'] == 'ItemFinished':
                    #    data = stevent['data']
                    #    if data['folder'] in self.get_config_folder().fid():
                    #        event = ControlEvent(stevent)
                    else:  # General event
                        self._enqueueEvent(SyncthingEvent(stevent))

            #time.sleep(1)
            yield

    def __generateInitialConfig(self):
        self.__log(1, 'Generating initial configuration')
        dorestart = self.syncthing_running()
        try:
            if dorestart:
                self.__stop_syncthing()
            try:
                self.myId()
                self.__log(1, 'syncthing keys already exist')
                # if we can get id - config is already generated
            except NoInitialConfiguration:
                self.__log(1, 'generating syncthing keys')
                proc = subprocess.Popen([self.syncthing_bin, '-generate={home}'.format(home=self.config_root)], stdout=sys.stdout, stderr=sys.stderr)
                proc.wait()
                if proc.poll() != 0:
                    raise RuntimeError('Could not generate initial configuration')

                # lets generate initial cache
                self.myId()
                self.__apikey = hashlib.sha1((self.myId() + '-apikey-%s' % ''.join((random.choice(string.ascii_letters) for _ in range(16)))).encode('UTF-8')).hexdigest()
                self.__server_secret = ''.join((random.choice(string.ascii_letters) for _ in range(24)))

                self.__servers = set()
                self.__devices = {self.myId(): Device(self, self.myId())}
            self.__save_configuration(save_st_config=False)

        finally:
            if dorestart:
                self.__start_syncthing()

    def __updateClientConfigs(self):
        pass

    def __del__(self):
        self.__stop_syncthing()

    def get_config_folder(self, devid=None):
        """
        TODO: Document this - is it outside inderface? is it only for internal work?
        get control folder of a client if server and device is given
        get server config folder if server and device is None
        else return client's control folder
        :param devid:
        :return:
        """
        if not self._isServer():
            if devid is None:
                return SyncthingHandler.ControlFolder(fid='control-%s' % hashlib.sha1((':'.join([self.__server_secret, self.myId()])).encode('UTF-8')).hexdigest(),
                                                      path=os.path.join(self.data_root, 'control', self.myId())
                                                      )
            else:
                raise RuntimeError('wat do u think ur doin?')
        if self._isServer() and devid is None:
            return SyncthingHandler.ControlFolder(fid='server_configuration-%s' % hashlib.sha1(self.__server_secret.encode('UTF-8')).hexdigest(),
                                                  path=os.path.join(self.data_root, 'server')
                                                  )

        if devid not in self.__devices:
            raise RuntimeError('unknown device')
        return SyncthingHandler.ControlFolder(fid='control-%s' % hashlib.sha1((':'.join([self.__server_secret, devid])).encode('UTF-8')).hexdigest(),
                                              path=os.path.join(self.data_root, 'control', devid)
                                              )
        #return self.__devices[device].get('controlfolder', None)

    @async_method()
    def _methodbatch_hold_st_update(self):
        """
        :return:
        """
        self.__defer_stupdate = True
        self.__defer_stupdate_writerequired = False

    @async_method()
    def _methodbatch_resume_st_update(self):
        """
        :return:
        """
        self.__defer_stupdate = False
        if self.__defer_stupdate_writerequired:
            self.__save_st_config()
        self.__defer_stupdate_writerequired = False

    # INTERFACE
    # note: all getters are doing copy to avoid race conditions
    def config_synced(self):
        return self.__configInSync  # should be python-atomic

    @async_method()
    def is_server(self):
        """
        if config not in sync - this will return last valid configuration
        :return:
        """
        return self._isServer()

    @async_method()
    def get_devices(self):
        """
        if config not in sync - this will return last valid configuration
        :return:
        """
        return copy.deepcopy(self.__devices)

    @async_method()
    def get_servers(self):
        """
        if config not in sync - this will return last valid configuration
        :return:
        """
        return copy.deepcopy(self.__servers)

    @async_method()
    def get_folders(self):
        """
        if config not in sync - this will return last valid configuration
        :return:
        """
        return copy.deepcopy(self.__folders)

    @async_method()
    def add_server(self, deviceid: str):
        return self.__interface_addServer(deviceid)

    @async_method()
    def add_device(self, deviceid: str, name: Optional[str] = None):
        return self.__interface_addDevice(deviceid, name)

    @async_method()
    def remove_device(self, deviceid: str):
        return self.__interface_removeDevice(deviceid)

    @async_method()
    def set_devices(self, dids: Iterable[str]):
        return self.__interface_setDevices(dids)

    @async_method()
    def add_folder(self, folderPath, label, devList=None, metadata=None, overrideFid=None):
        return self.__interface_addFolder(folderPath, label, devList, metadata, overrideFid)

    @async_method()
    def remove_folder(self, folderId):
        return self.__interface_removeFolder(folderId)

    @async_method()
    def add_device_to_folder(self, fid, did):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if did not in self.__devices or self.__devices[did].is_schediled_for_deletion():
            raise RuntimeError('device %s does not belong to this server' % did)
        if fid not in self.__folders:
            raise RuntimeError('folder %s does not belong to this server' % did)
        self.__log(1, "adding %s to %s that has %s" % (did, fid, repr(self.__folders[fid].devices())))
        if did not in self.__folders[fid].devices():
            self.__log(1, 'adding')
            self.__folders[fid].add_device(did)
            self.__save_configuration(save_st_config=True)
            self.__log(1, "config saved %s" % repr(self.__folders[fid].devices()))
            #self.__save_st_config()
            for dev in self.__folders[fid].devices():
                self.__save_device_configuration(dev)
            self._enqueueEvent(FoldersConfigurationChangedEvent((copy.deepcopy(self.__folders[fid]),), 'external::add_device_to_folder'))

    @async_method()
    def remove_device_from_folder(self, fid, did):
        if did not in self.__devices:
            raise RuntimeError('device %s does not belong to this server' % did)
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if fid not in self.__folders:
            raise RuntimeError('folder %s does not belong to this server' % did)
        self.__log(1, "removing %s from %s that has %s" % (did, fid, repr(self.__folders[fid].devices())))
        if did in self.__folders[fid].devices():
            self.__log(1, 'removing')
            self.__folders[fid].remove_device(did)
            self.__save_configuration(save_st_config=True)
            self.__log(1, "config saved %s" % repr(self.__folders[fid].devices()))
            #self.__save_st_config()
            for dev in self.__folders[fid].devices():
                self.__save_device_configuration(dev)
            self.__save_device_configuration(did)
            self._enqueueEvent(FoldersConfigurationChangedEvent((copy.deepcopy(self.__folders[fid]),), 'external::remove_device_from_folder'))

    @async_method()
    def set_folder_devices(self, fid, dids: Iterable):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        for did in dids:
            if did not in self.__devices or self.__devices[did].is_schediled_for_deletion():
                raise RuntimeError('device %s does not belong to this server' % did)
        if fid not in self.__folders:
            raise RuntimeError('folder %s does not belong to this server' % did)
        self.__log(1, "setting folder's devices")
        if not isinstance(dids, set):
            dids = set(dids)
        olddids = self.__folders[fid].devices()
        if dids == olddids:
            return
        todel = olddids.difference(dids)
        toadd = dids.difference(olddids)
        for did in todel:
            self.__log(1, 'removing %s from %s' % (did, fid))
            self.__folders[fid].remove_device(did)
        for did in toadd:
            self.__log(1, 'adding %s to %s' % (did, fid))
            self.__folders[fid].add_device(did)
        self.__save_configuration(save_st_config=True)
        self.__log(1, "config saved, %s had devices: %s" % (fid, repr(self.__folders[fid].devices())))
        #self.__save_st_config()
        for did in self.__folders[fid].devices().union(todel):
            self.__save_device_configuration(did)
        # now send events
        eventdata = (copy.deepcopy(self.__folders[fid]),)
        self._enqueueEvent(FoldersConfigurationChangedEvent(eventdata, 'external::set_folder_devices'))


    @async_method()
    def set_server_secret(self, secret):  # TODO: hm.... need to figure out how to do this safely at runtime
        assert isinstance(secret, str), 'secret must be a str'
        self.__server_secret = secret
        self.__save_configuration(save_st_config=False)  # TODO: can we just save bootstrap here?
        self.__reload_configuration()

    @async_method()
    def set_device_name(self, did, name):
        if not self._isServer():
            raise RuntimeError('only server can do that')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if did not in self.__devices:
            raise RuntimeError('%s is not part of this server' % did)
        if name == self.__devices[did].name():
            return
        self.__devices[did]._setName(name)
        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()

        # now we need to inform ALL devices this one has contact with about the new name
        devfids = [fid for fid in self.__folders if did in self.__folders[fid].devices()]
        devfiddevs = {}  # all devices that share allowed folders
        for fid in devfids:
            devfiddevs.update({dev: self.__devices[dev] for dev in self.__folders[fid].devices()})
        for x in devfiddevs:
            self.__save_device_configuration(x)

        self._enqueueEvent(DevicesChangedEvent((copy.deepcopy(self.__devices[did]),), 'external::set_device_name'))

    @async_method()
    def reload_configuration(self):
        return self.__reload_configuration()
    # END INTERFACE

    def __interface_addDevice(self, deviceid, name=None):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if deviceid in self.__devices:
            if self.__devices[deviceid].is_schediled_for_deletion():
                self.__devices[deviceid].unschedule_for_deletion()
                self.__save_configuration(save_st_config=True)
                #self.__save_st_config()
                for dev in self.__devices:
                    self.__save_device_configuration(dev)
            return
        self.__log(1, 'adding device %s' % deviceid)
        self.__devices[deviceid] = Device(self, deviceid, name=name)
        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()
        self.__save_device_configuration(deviceid)
        #for dev in self.__devices:
        #    self.__save_device_configuration(dev)
        self._enqueueEvent(DevicesAddedEvent((copy.deepcopy(self.__devices[deviceid]),), 'external::add_device'))

    def __interface_removeDevice(self, deviceid):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if deviceid not in self.__devices or self.__devices[deviceid].is_schediled_for_deletion():
            self.__log(1, 'was about to removing device %s, but its already removed/scheduled for removal' % deviceid)
            return
        self.__log(1, 'removing device %s' % deviceid)
        dids_to_update = set()
        folders_updated = set()
        for fid, folder in self.__folders.items():
            if deviceid in folder.devices():
                folder.remove_device(deviceid)
                dids_to_update.update(folder.devices())
                folders_updated.add(copy.deepcopy(self.__folders[fid]))

        # now we cannot just delete device like we do with folders - it will not sync if we delete it straight away, and therefore will not know it was deleted.

        remdevice = self.__devices[deviceid]
        remdevice.schedule_for_deletion()
        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()
        dids_to_update.add(deviceid)
        for did in dids_to_update:
            self.__save_device_configuration(did)

        # send events
        if len(folders_updated) > 0:
            self._enqueueEvent(FoldersConfigurationChangedEvent(folders_updated, 'external::remove_device'))  # note that those are copied folder objects
        self._enqueueEvent(DevicesRemovedEvent([copy.deepcopy(remdevice)], 'external::remove_device'))  # TODO: since we delete it from server - deepcopy is excessive, no need to copy at all.... right? check it

    def __interface_setDevices(self, dids: Iterable[str]):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        self.__log(1, "setting device list to %s" % repr(dids))
        if not isinstance(dids, set):
            dids = set(dids)
        existing_dids = set((x for x, y in self.__devices.items() if not y.is_schediled_for_deletion() and x not in self.__servers))
        if dids == existing_dids:
            self.__log(1, "no changes required")
            return
        dids_to_add = dids.difference(existing_dids)
        dids_to_remove = existing_dids.difference(dids).difference(self.__servers)  # though there are no servers in existing_dids by construction

        devs_added_forevent = []
        devs_removed_forevent = []
        folders_updated_forevent = []

        self.__log(1, 'adding devices %s' % dids_to_add)
        for did in dids_to_add:
            self.__devices[did] = Device(self, did)
            devs_added_forevent.append(copy.deepcopy(self.__devices[did]))

        self.__log(1, 'removing devices %s' % dids_to_remove)
        dids_to_update = set()

        for did in dids_to_remove:
            for fid, folder in self.__folders.items():
                if did in folder.devices():
                    folder.remove_device(did)
                    dids_to_update.update(folder.devices())
                    folders_updated_forevent.append(copy.deepcopy(self.__folders[fid]))
            devs_removed_forevent.append(self.__devices[did])
            self.__devices[did].schedule_for_deletion()

        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()
        for did in dids_to_add.union(dids_to_update).union(dids_to_remove):
            self.__save_device_configuration(did)

        #send events
        if len(folders_updated_forevent) > 0:
            self._enqueueEvent(FoldersConfigurationChangedEvent(folders_updated_forevent, 'external::set_devices'))  # note that those are copied folder objects
        if len(devs_added_forevent) > 0:
            self._enqueueEvent(DevicesAddedEvent(devs_added_forevent, 'external::set_devices'))
        if len(devs_removed_forevent) > 0:
            self._enqueueEvent(DevicesRemovedEvent(devs_removed_forevent, 'external::set_devices'))

    def __interface_addServer(self, deviceid):
        if deviceid in self.__servers:
            return
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if deviceid not in self.__devices:
            self.__devices[deviceid] = Device(self, deviceid)
        self.__log(1, 'adding server %s' % deviceid)
        self.__servers.add(deviceid)

        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()
        if self._isServer():
            for dev in self.__devices:
                self.__save_device_configuration(dev)

    def __interface_addFolder(self, folderPath, label, devList=None, metadata=None, overrideFid=None):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        if devList is None:
            devList = []
        assert isinstance(label, str), 'label must be string'
        assert isinstance(devList, list) or isinstance(devList, set) or isinstance(devList, tuple), 'devList must be a list'
        assert isinstance(folderPath, str), 'folderPath must be a string'
        self.__log(1, 'adding folder %s' % label)
        for dev in devList:
            if dev not in self.__devices:
                raise RuntimeError('device %s does not belong to this server' % dev)

        if len([x for x in self.__folders if self.__folders[x].path() == folderPath]) > 0:
            return

        if overrideFid is not None:
            fid = overrideFid
            if fid in self.__folders:
                raise RuntimeError('given fid already exists! %s' % fid)
        else:
            for _ in range(32):
                fid = 'folder-' + ''.join(random.choice(string.ascii_lowercase) for _ in range(16))
                if fid not in self.__folders:
                    break
            else:
                raise RuntimeError('unexpected probability! call ghost busters')
        self.__folders[fid] = Folder(self, fid, label, folderPath, devList, metadata)

        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()
        for dev in devList:
            self.__save_device_configuration(dev)
        self._enqueueEvent(FoldersAddedEvent((copy.deepcopy(self.__folders[fid]),), 'external::add_folder'))
        return fid

    def __interface_removeFolder(self, folderId: str):
        if not self._isServer():
            raise RuntimeError('device list is provided by server')
        if not self.__configInSync:
            raise ConfigNotInSyncError()
        assert isinstance(folderId, str), 'folderId must be str'
        self.__log(1, 'removing folder %s' % folderId)
        if folderId not in self.__folders:
            self.__log(2, 'folder %s does not exist' % folderId)
            return

        folder = self.__folders[folderId]
        del self.__folders[folderId]

        self.__save_configuration(save_st_config=True)
        #self.__save_st_config()
        for dev in folder.devices():
            self.__save_device_configuration(dev)
        # note that server does NOT delete folder from disc when folder is removed
        self._enqueueEvent(FoldersRemovedEvent((copy.deepcopy(folder),), 'external::remove_folder'))

    def __reload_configuration(self, use_bootstrap=True):
        """
        loads server/client configuration, ensures configuration is up to date
        :return: bool if configuration has changed
        """
        # no point in syncthing pause lock, cuz either this must ensure synced folder state, or it's all the same
        self.__log(1, 'reloading configuration')
        if not self.__configInSync:
            self.__log(2, 'configuration not in sync!')
            #return None
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
        __debug_olddevices = copy.copy(self.__devices)
        __debug_oldfolders = copy.copy(self.__folders)
        oldservers = self.__servers
        olddevices = self.__devices
        oldfolders = self.__folders
        oldignoredevices = self.__ignoreDevices

        #if len(self.__servers) == 0:  # Initial config loading
        if use_bootstrap:
            self.__servers = set(config.get('servers', ()))  # bootstrap to have _isServer resolving correctly
            self.__log(1, 'server list loaded from bootstrap config: %s' % repr(self.__servers))
        else:
            self.__servers = {}
            self.__log(1, 'bootstrap server list ignored')
        #self.__log(1, 'server list:', self.__servers)

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
            self.__servers.update(set(listdir(os.path.join(configFoldPath, 'servers'))))  # either update with bootstrapped, or with empty
            self.__log(1, 'final server list:', self.__servers)
            self.__devices = {}
            for dev in listdir(os.path.join(configFoldPath, 'devices')):
                with open(os.path.join(configFoldPath, 'devices', dev), 'r') as f:
                    fdata = json.load(f)
                newdevice = Device.deserialize(self, fdata)
                    # Device(self, dev, fdata.get('name', None))
                if newdevice.id() != dev:
                    raise RuntimeError('config inconsistent!!')  # TODO: add special inconsistent config error

                if dev in olddevices:
                    olddevice = olddevices[dev]
                    olddevices[dev] = copy.copy(olddevice)  # we will modify old device, so we need to keep old copy for future comparison
                    olddevice.__dict__.update(newdevice.__dict__)
                    newdevice = olddevice
                self.__devices[dev] = newdevice
                #if _isServer:
                #    self.__devices[dev]['controlfolder'] = {'fid': 'control-%s' % hashlib.sha1((':'.join([self.__server_secret, dev])).encode('UTF-8')).hexdigest(),
                #                                            'path': os.path.join(self.data_root, 'control', dev)
                #                                            }  # ensure controlfolder attr exist
            self.__log(1, 'final device list:', self.__devices.keys())

            # check server-dev list consistency
            for srv in set(self.__servers):
                if srv not in self.__devices:
                    self.__log(4, 'server %s not in device list! adding...' % srv)
                    self.__devices[srv] = Device(self, srv, None)
                    #self.__servers.remove(srv)

            self.__folders = {}
            for fid in listdir(os.path.join(configFoldPath, 'folders')):
                with open(os.path.join(configFoldPath, 'folders', fid, 'attribs'), 'r') as f:
                    fattrs = json.load(f)
                newfolder = Folder(self, fid, fattrs['label'], config.get('folders', {}).get(fid, {}).get('attribs', {}).get('path', None))  # TODO: if path changed suddenly and st noticed it - it will give error about missing .stfolder. We have to deal with this one way or another
                for dev in listdir(os.path.join(configFoldPath, 'folders', fid, 'devices')):
                    if dev not in self.__devices:
                        self.__log(4, 'folder device %s is not part of device list. skipping...' % dev)
                        continue
                    newfolder.add_device(dev)
                with open(os.path.join(configFoldPath, 'folders', fid, 'metadata'), 'r') as f:
                    fmeta = json.load(f)
                newfolder._setMetadata(fmeta)

                if newfolder.path() is None:
                    # TODO: add an option to control this, allow folders to stay without path
                    # TODO: ensure path does not exist already
                    newfolder._setPath(os.path.join(self.data_root, newfolder.label()))  #TODO: convert label to a valid filesystem filename !!!

                if fid in oldfolders:
                    oldfolder = oldfolders[fid]
                    oldfolders[fid] = copy.copy(oldfolder)
                    oldfolder.__dict__.update(newfolder.__dict__)
                    newfolder = oldfolder
                self.__folders[fid] = newfolder

            self.__log(1, 'final folder list:', self.__folders.keys())
            self.__ignoreDevices = set()
            for dev in listdir(os.path.join(configFoldPath, 'ignoredevices')):
                self.__ignoreDevices.add(dev)
        except:  # config folder is malformed. try load cached servers and wait for config sync
            raise

            #TODO: save local json config, just servers, all else should be empty

        configChanged = configChanged or oldservers != self.__servers or olddevices != self.__devices or oldfolders != self.__folders or oldignoredevices != self.__ignoreDevices
        if not self._isServer():  # for client - if we have folders removed - those folders must be deleted immediately
            for fid in oldfolders:
                if fid in self.__folders:
                    continue
                fpath = oldfolders[fid].path()
                if fpath is None:
                    continue
                try:
                    if '.stfolder' in os.listdir(fpath):  # just sanity check
                        shutil.rmtree(fpath, ignore_errors=True)
                        self.__log(1, 'removed folder %s as server closed access to it' % fpath)
                    else:
                        self.__log(4, 'could not find .stfolder in what should be a synced folder: %s' % fpath)
                except Exception as e:
                    self.__log(5, 'unexpected error occured: %s' % repr(e))

        #if not self.__configInSync:
        #    self.__configInSync = True
        #    self._enqueueEvent(ConfigSyncChangedEvent(True))
        if configChanged:
            self.__log(1, 'state changed, resaving st config')
            self.__save_st_config()
            if self._isServer():  # TODO: update only affected ones !
                for dev in self.__devices:
                    self.__save_device_configuration(dev)
        else:
            self.__log(1, 'state hasn nott changed, no need to resave st config')

        # send events
        if olddevices != self.__devices:
            devicesadded = [copy.deepcopy(y) for x, y in self.__devices.items() if x not in olddevices]
            devicesremoved = [copy.deepcopy(y) for x, y in olddevices.items() if x not in self.__devices]
            devicesupdated = [copy.deepcopy(y) for x, y in olddevices.items() if x in self.__devices and y != self.__devices[x]]
            __debug_devicesupdated = [y for x, y in self.__devices.items() if x in olddevices and y != olddevices[x]]
            if len(devicesadded) > 0:
                self._enqueueEvent(DevicesAddedEvent(devicesadded, 'reload_configuration'))
                self.__log(1, 'devicesadded event enqueued %s' % repr(devicesadded))
            if len(devicesremoved) > 0:
                self._enqueueEvent(DevicesRemovedEvent(devicesremoved, 'reload_configuration'))
                self.__log(1, 'devicesremoved event enqueued %s' % repr(devicesremoved))
            if len(devicesupdated) > 0:
                self._enqueueEvent(DevicesChangedEvent(devicesupdated, 'reload_configuration'))
                self.__log(1, 'devicesupdated event enqueued %s' % repr(devicesupdated))
            #check
            for dev in __debug_devicesupdated:
                assert dev is __debug_olddevices[dev.id()], 'modified device is not the same object'
        if oldfolders != self.__folders:
            foldersadded = [copy.deepcopy(y) for x, y in self.__folders.items() if x not in olddevices]
            foldersremoved = [copy.deepcopy(y) for x, y in oldfolders.items() if x not in self.__folders]
            foldersupdated = [copy.deepcopy(y) for x, y in oldfolders.items() if x in self.__folders and y != self.__folders[x]]
            __debug_foldersupdated = [y for x, y in self.__folders.items() if x in oldfolders and y != oldfolders[x]]
            if len(foldersadded) > 0:
                self._enqueueEvent(FoldersAddedEvent(foldersadded, 'reload_configuration'))
                self.__log(1, 'foldersadded event enqueued %s' % repr(foldersadded))
            if len(foldersremoved) > 0:
                self._enqueueEvent(FoldersRemovedEvent(foldersremoved, 'reload_configuration'))
                self.__log(1, 'foldersremoved event enqueued %s' % repr(foldersremoved))
            if len(foldersupdated) > 0:
                self._enqueueEvent(FoldersConfigurationChangedEvent(foldersupdated, 'reload_configuration'))
                self.__log(1, 'foldersupdated event enqueued %s' % repr(foldersupdated))
            # check
            for fld in __debug_foldersupdated:
                assert fld is __debug_oldfolders[fld.id()], 'modified device is not the same object'
        # /events sent

        if not self.__configInSync and (self._isServer() and len(self.__servers) == 1 or len(self.__servers) == 0):  # so we are one and only server - we dont wait for config sync runtime check - there is noone to sync with
            self.__configInSync = True
            self._enqueueEvent(ConfigSyncChangedEvent(True))

        return configChanged

    def __save_device_configuration(self, dev: str):
        assert self._isServer(), "must be server to save config for devices"
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
            with open(os.path.join(_fldpath, fid, 'metadata'), 'w') as f:
                json.dump(self.__folders[fid].metadata(), f)
        # save ign dev
        for dev in self.__ignoreDevices:
            os.mknod(os.path.join(_ignpath, dev))

    def __save_bootstrapConfig(self):
        self.__log(1, "saving bootstrap configuration")
        config = {}
        config['apikey'] = self.__apikey
        config['server_secret'] = self.__server_secret

        # only bootstrap and local info is saved here, like servers list and folder paths
        config['servers'] = list(self.__servers)
        config['devices'] = {id: {'id': self.__devices[id].id(), 'name': self.__devices[id].name()} for id in self.__devices}
        config['folders'] = {id: {'attribs': {'path': self.__folders[id].path()}} for id in self.__folders}
        # for fid in config['folders']:
        #    config['folders'][fid]['devices'] = list(config['folders'][fid]['devices'])
        config['ignoreDevices'] = list(self.__ignoreDevices)

        with open(os.path.join(self.config_root, 'syncthinghandler_config.json'), 'w') as f:
            json.dump(config, f, indent=4)

    def __save_configuration(self, save_st_config=True):
        """
        server saves configuration to shared server folder
        both server and client dumps cache of current config to json file, though  it should only be used as abootstrap
        :return:
        """
        self.__log(1, 'saving configuration')
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

        #try:
        with SyncthingHandler.SyncthingPauseLock(self):
            #if self.syncthing_running():
            #    self.__post('/rest/system/pause', {})

            self.__save_bootstrapConfig()

            configFoldPath = os.path.join(self.get_config_folder().path(), 'configuration')

            _devpath = os.path.join(configFoldPath, 'devices')
            _srvpath = os.path.join(configFoldPath, 'servers')
            _fldpath = os.path.join(configFoldPath, 'folders')
            _ignpath = os.path.join(configFoldPath, 'ignoredevices')
            if self._isServer():
                shutil.rmtree(_devpath, ignore_errors=True)  # clear existing
                shutil.rmtree(_srvpath, ignore_errors=True)  # clear existing
                shutil.rmtree(_fldpath, ignore_errors=True)  # clear existing
                shutil.rmtree(_ignpath, ignore_errors=True)  # clear existing

            #not for server - just make sure these folders exists
            os.makedirs(_devpath, exist_ok=True)
            os.makedirs(_srvpath, exist_ok=True)
            os.makedirs(_fldpath, exist_ok=True)
            os.makedirs(_ignpath, exist_ok=True)

            if self._isServer():
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
                    os.makedirs(fiddevpath, exist_ok=True)
                    for dev in self.__folders[fid].devices():
                        os.mknod(os.path.join(fiddevpath, dev))
                    with open(os.path.join(_fldpath, fid, 'attribs'), 'w') as f:
                        json.dump({'fid': fid, 'label': self.__folders[fid].label()}, f)
                    with open(os.path.join(_fldpath, fid, 'metadata'), 'w') as f:
                        json.dump(self.__folders[fid].metadata(), f)
                # save ign dev
                for dev in self.__ignoreDevices:
                    os.mknod(os.path.join(_ignpath, dev))

            if save_st_config:
                self.__save_st_config()
        #finally:
        #    if self.syncthing_running():
        #        self.__post('/rest/system/resume', {})

    def __save_st_config(self):
        """
        saves current configuration to syncthing config and restarts syncthing if needed
        :return:
        """
        if self.__defer_stupdate:
            self.__log(1, 'st configuration will be updated when methodbatch finishes')
            self.__defer_stupdate_writerequired = True
            return
        self.__log(1, 'saving st configuration')
        conf = ET.Element('configuration', {'version': '28'})

        servers = self.__servers
        devices = self.__devices
        blacklist = self.__ignoreDevices
        folders = self.__folders

        if self._isServer():
            serverConfigFolder = self.get_config_folder()
            #fid = 'server_configuration-%s' % hashlib.sha1(self.__server_secret.encode('UTF-8')).hexdigest()
            sconf = ET.SubElement(conf, 'folder', {'id': serverConfigFolder.fid(),
                                                   'label': 'server configuration',
                                                   'path': serverConfigFolder.path(),
                                                   'type': 'sendreceive',
                                                   'rescanIntervalS': '3600',
                                                   'fsWatcherEnabled': 'true',
                                                   'fsWatcherDelayS': '5',
                                                   'ignorePerms': 'true',
                                                   'autoNormalize': 'true'
                                                   })
            ET.SubElement(sconf, 'maxConflicts').text = '0'
            for server in self.__servers:
                ET.SubElement(sconf, 'device', {'id': server})

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
                                                          'fsWatcherDelayS': '5',
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
        ET.SubElement(guielem, 'address').text = '%s:%d' % (self.syncthing_gui_ip, self.syncthing_gui_port)
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
        self.__log(1, conftext)
        if dorestartst:
            self.__start_syncthing()
        #TODO: IMPROVE: its faster to get-modify-post config, but that config is in json format, and file is in xml... ffs

        if self.syncthing_proc is not None and not self.__isValidState:
            self.__stop_syncthing()

    def __start_syncthing(self):
        self.__log(1, 'starting syncthing process...')
        if not self.syncthing_proc or self.syncthing_proc.poll() is not None:
            self._last_event_id = 0
            self.syncthing_proc = subprocess.Popen([self.syncthing_bin, '-home={home}'.format(home=self.config_root), '-no-browser', '-no-restart', '-gui-address={addr}:{port}'.format(addr=self.syncthing_gui_ip, port=self.syncthing_gui_port)], stdout=sys.stdout, stderr=sys.stderr)
            self.__log(1, 'syncthing started')
            return True
        self.__log(1, 'syncthing was not running')
        return False

    def __stop_syncthing(self):
        self.__log(1, 'stopping syncthing process...')
        if self.syncthing_proc:
            self.syncthing_proc.terminate()
            self.syncthing_proc.wait()
            self.syncthing_proc = None
            self.__log(1, 'syncthing stopped')
            return True
        self.__log(1, 'syncthing was not running')
        return False

    def __pause_syncthing(self):
        self.__log(1, 'pausing syncthing...')
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            self.__log(1, 'syncthing is not running, throwing')
            raise RuntimeError('syncthing is not running')
        self.__post('/rest/system/pause', {})

    def __resume_syncthing(self):
        self.__log(1, 'resuming syncthing...')
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            self.__log(1, 'syncthing is not running, throwing')
            raise RuntimeError('syncthing is not running')
        self.__post('/rest/system/resume', {})

    def syncthing_running(self):
        return self.syncthing_proc is not None and self.syncthing_proc.poll() is None

    def __get(self, path, **kwargs):
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            raise SyncthingNotReadyError()
        url = "http://%s:%d%s" % (self.syncthing_gui_ip, self.syncthing_gui_port, path)
        if len(kwargs) > 0:
            url += '?' + '&'.join(['%s=%s' % (k, str(kwargs[k])) for k in kwargs.keys()])
        self.__log(0, "getting %s" % url)
        self.__log(0, self.httpheaders)
        req = requester.Request(url, headers=self.httpheaders)
        for _ in range(32):  # 32 attempts
            try:
                rep = requester.urlopen(req)
                break
            except requester.URLError as e:
                if not isinstance(e.reason, ConnectionRefusedError):
                    raise
                # assume syncthing is not yet ready
                if self.syncthing_proc.poll() is not None:
                    raise SyncthingNotReadyError()
                time.sleep(1)
        else:
            raise SyncthingNotReadyError()
        data = json.loads(rep.read().decode('utf-8'))
        return data

    def __post(self, path, data):
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            raise SyncthingNotReadyError()
        req = requester.Request("http://%s:%d%s" % (self.syncthing_gui_ip, self.syncthing_gui_port, path), json.dumps(data).encode('utf-8'), headers=self.httpheaders)
        req.get_method = lambda: 'POST'
        for _ in range(32):  # 32 attempts
            try:
                rep = requester.urlopen(req)
                break
            except requester.URLError as e:
                if not isinstance(e.reason, ConnectionRefusedError):
                    raise
                # assume syncthing is not yet ready
                if self.syncthing_proc.poll() is not None:
                    raise SyncthingNotReadyError()
                time.sleep(1)

        else:
            raise SyncthingNotReadyError()
        return None  # json.loads(rep.read())

    @async_method()
    def get(self, path, **kwargs):
        return self.__get(path, **kwargs)

    @async_method()
    def post(self, path, data):
        return self.__post(path, data)

    def __copy__(self):
        raise RuntimeError('COPY SHOULD NEVER HAPPEN!')

    def __deepcopy__(self, memodict=None):
        raise RuntimeError('DEEPCOPY SHOULD NEVER HAPPEN!')

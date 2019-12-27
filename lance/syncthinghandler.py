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

def mknod(path, mode=0o600, device=0):
    try:
        return os.mknod(path, mode, device)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def remove(path):
    try:
        return os.remove(path)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise

def syncthing_timestamp_to_datetime(timestamp: str) -> datetime.datetime:
    return datetime.datetime.strptime(re.sub('\d{3}(?=[\+-]\d{2}:\d{2}$)', '', timestamp), '%Y-%m-%dT%H:%M:%S.%f%z')

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
    def __init__(self, sthandler: 'SyncthingHandler', id: str, name: Optional[str] = None, creation_time: Optional[float] = None):  #TODO: probably noone outside this class uses creation_time argument. check and consider removing!
        self.__stid = id  # type: str
        self.__name = name  # type: str
        self.__sthandler = sthandler
        self.__volatiledata = DeviceVolatileData()
        self.__ismyself = self.__sthandler is not None and self.__sthandler.myId() == id
        if creation_time is None:
            self.__added_at = time.time()  # type: float
        else:
            self.__added_at = creation_time
        self.__delete_on_sync_after = None
        self._st_event_synced = True  # for internal use by syncthinghandler stevent processor
        self._st_event_confighash = ""  #TODO: should this be saved and shared between servers?? can lead to all sorts of shits both ways!

    def replace_with(self, newdevice: 'Device'):
        newdict = copy.copy(newdevice.__dict__)
        del newdict['_Device__volatiledata']
        del newdict['_st_event_synced']
        del newdict['_st_event_confighash']
        self.__dict__.update(newdict)

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

    def created_at(self) -> float:
        """
        :return: timestamp when this device was added
        """
        return self.__added_at

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
               self.__added_at == other.__added_at and \
               self.__delete_on_sync_after == other.__delete_on_sync_after  # TODO: should name be a part of this? on one hand - yes, cuz we need it to detect changes in config

    def __deepcopy__(self, memodict=None):
        newone = copy.copy(self)
        newone.__volatiledata = copy.deepcopy(self.__volatiledata, memo=memodict)
        return newone

    def __copy__(self):
        newone = Device(self.__sthandler, self.__stid, self.__name, self.__added_at)
        newone.__volatiledata = self.__volatiledata
        return newone

    def configuration_hash(self):
        """
        hash unique to this device's configuration, not including volatile data or internal server/client related data
        :return:
        """
        ph = self.__stid
        if self.__name is not None:
            ph += '::' + self.__name
        return hash(ph)

    def serialize_to_dict(self) -> dict:
        return {"id": self.__stid,
                "name": self.__name,
                "delete_on_sync_after": self.__delete_on_sync_after,
                "added_at": self.__added_at
                }

    def serialize_to_str(self) -> str:
        return json.dumps(self.serialize_to_dict())

    @classmethod
    def deserialize(cls, sthandler, s: Union[str, dict]):
        if isinstance(s, str):
            data = json.loads(s)
        else:
            data = s
        newdev = Device(sthandler, data['id'], data['name'], data['added_at'])
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
        self._st_event_synced = True  # for internal use by syncthinghandler stevent processor

    def replace_with(self, newfolder: 'Folder'):
        newdict = copy.copy(newfolder.__dict__)
        del newdict['_Folder__volatiledata']
        del newdict['_st_event_synced']
        self.__dict__.update(newdict)

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
        if move_contents and self.__path is not None:
            if path is not None:
                os.makedirs(path, exist_ok=True)
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

    def configuration_hash(self):
        """
        hash unique to this device's configuration, not including volatile data or internal server/client related data
        :return:
        """
        ph = self.__stfid
        if self.__label is not None:
            ph += '::' + self.__label
        devhash = 0
        for dev in self.__devices:
            devhash ^= hash(dev)
        ph += '::' + str(devhash)
        ph += json.dumps(self.__metadata)
        return hash(ph)

    def serialize_to_dict(self) -> dict:
        config = {}
        config['devices'] = list(self.__devices)
        config['attribs'] = {'fid': self.__stfid, 'label': self.__label, 'path': self.__path}
        config['metadata'] = self.__metadata
        return config

    def serialize_to_str(self) -> str:
        return json.dumps(self.serialize_to_dict())

    @classmethod
    def deserialize(cls, sthandler, s: Union[str, dict]):
        if isinstance(s, str):
            data = json.loads(s)
        else:
            data = s
        newfol = Folder(sthandler, data['attribs']['fid'], data['attribs']['label'], data['attribs']['path'], data['devices'], data['metadata'])
        return newfol

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

        def __init__(self, sthandler: 'SyncthingHandler', devices: Optional[Iterable[str]] = None):
            self.__sthandler = sthandler
            self.__doUnpause = False
            self.__devids = None if devices is None else list(devices)

        def __enter__(self):
            if not self.__sthandler.syncthing_running() or self.__sthandler in SyncthingHandler.SyncthingPauseLock.__lockSet:
                return
            SyncthingHandler.SyncthingPauseLock.__lockSet.add(self.__sthandler)
            self.__sthandler._SyncthingHandler__pause_syncthing(self.__devids)
            self.__doUnpause = True

        def __exit__(self, exc_type, exc_val, exc_tb):
            if not self.__doUnpause:
                return
            SyncthingHandler.SyncthingPauseLock.__lockSet.remove(self.__sthandler)
            self.__sthandler._SyncthingHandler__resume_syncthing(self.__devids)


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

                    eventtime_datetime = syncthing_timestamp_to_datetime(stevent['time'])
                    eventtime = eventtime_datetime.timestamp()
                    #just fancy logging:
                    logtext = 'event type "%s" from %s' % (stevent['type'], eventtime_datetime.strftime('%H:%M:%S.%f'))
                    if stevent['type'] == 'FolderSummary':
                        logtext += ' %s: %d' % (stevent['data']['folder'], stevent['data']['summary']['needTotalItems'])
                    elif stevent['type'] == 'ItemStarted':
                        logtext += '%s: %s: %s' % (stevent['data']['folder'], stevent['data']['action'], stevent['data']['item'])
                    elif stevent['type'] == 'ItemFinished':
                        logtext += '%s: %s: %s' % (stevent['data']['folder'], stevent['data']['action'], stevent['data']['item'])
                    self.__log(1, logtext)

                    controlfolders = {self.get_config_folder(did).fid(): did for did, dev in self.__devices.items()} if self._isServer() else {}

                    if stevent.get('error', None) is not None or stevent.get('data', {}).get('error', None) is not None:
                        self.__log(1, 'syncthing event has error status, %s, skipping' % json.dumps(stevent))
                        continue  # TODO: for now we have NO error handling/reporting at all

                    if stevent['type'] == 'StartupComplete':
                        # syncthing loaded. either first load, or syncthing restarted after configuration save
                        # must check configuration
                        self.__log(1, 'StartupComplete event received, config in sync =%s' % repr(self.__configInSync))
                        try:
                            configstatus = self.__get('/rest/db/file', folder=self.get_config_folder().fid(), file='configuration/config.cfg')
                            configsynced = configstatus['global']['version'] == configstatus['local']['version']
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
                    elif self.__configInSync and stevent['type'] == 'ItemStarted' and stevent['data']['folder'] == self.get_config_folder().fid() and stevent['data']['type'] == 'file' and stevent['data']['item'] == 'configuration/config.cfg' and stevent['data']['action'] != 'metadata':
                        self.__log(1, 'starting to sync server configuration, config in sync = False')
                        self.__configInSync = False
                        self._enqueueEvent(ConfigSyncChangedEvent(False))
                    elif not self.__configInSync and stevent['type'] == 'ItemFinished' and stevent['data']['folder'] == self.get_config_folder().fid() and stevent['data']['type'] == 'file' and stevent['data']['item'] == 'configuration/config.cfg' and stevent['data']['action'] != 'metadata':
                        data = stevent['data']
                        #if data['summary']['needTotalItems'] == 0:
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

                    # Check control folder
                    elif self._isServer() and stevent['type'] == 'ItemFinished' and stevent['data']['folder'] in controlfolders and  stevent['data']['action'] != 'metadata':
                        if stevent['data']['item'] == 'config_sync/hash':  # hash sync
                            # not self.__devices[controlfolders[stevent['data']['folder']]]._st_event_synced and
                            clientdid = controlfolders[stevent['data']['folder']]
                            self.__log(1, 'control folder for device %s has updated hash' % clientdid)
                            self.__log(1, 'device %s sync status: %s' % (clientdid, repr(self.__devices[clientdid]._st_event_synced)))
                            if not self.__devices[clientdid]._st_event_synced:
                                try:
                                    with open(os.path.join(self.get_config_folder(clientdid).path(), 'config_sync', 'hash'), 'r') as f:
                                        syncedhash = f.read()
                                except OSError:
                                    self.__log(2, "couldn't read config hash though it was just synced. maybe already in sync again, skipping")
                                else:
                                    self.__log(1, 'expecting hash %s, got hash %s' % (self.__devices[clientdid]._st_event_confighash, syncedhash))
                                    if syncedhash == self.__devices[clientdid]._st_event_confighash:
                                        self.__devices[clientdid]._st_event_synced = True
                                        self.__log(1, 'device %s synced configuration' % clientdid)
                                        if self.__devices[clientdid].is_schediled_for_deletion():
                                            self.__log(1, 'now safe to delete device %s' % clientdid)
                                            del self.__devices[clientdid]
                                            self.__save_configuration(save_st_config=True)
                                            # Note that we don't update any device config, cuz if device scheduled for deletion - it must have already been removed from everything
                                            # so here we just do sanity check
                                            for fid, folder in self.__folders.items():
                                                assert clientdid not in folder.devices()
                                    else:
                                        self.__log(1, 'device %s config hash differs from expected, waiting' % clientdid)
                        elif stevent['data']['item'] == 'configuration/config.cfg':  # either another server updated it, or it may be index mismatch with removed and added back device
                            try:
                                fid = stevent['data']['folder']
                                stat = self.__get('/rest/db/file', folder=fid, file=stevent['data']['item'])
                            except Exception as e:
                                self.__log(4, 'couldnt stat config file: %s' % repr(e))
                            else:
                                did = controlfolders[fid]
                                modified_timestamp = syncthing_timestamp_to_datetime(stat['global']['modified']).timestamp()
                                if modified_timestamp < self.__devices[did].created_at():  # need to resave config
                                    self.__log(2, 'device %s has config of modification time before device was added. overriding config' % did)
                                    self.__save_device_configuration(did)
                    # Folder Statue event
                    elif stevent['type'] == 'ItemStarted' and stevent['data']['folder'] in self.__folders and stevent['data']['type'] == 'file' and stevent['data']['action'] != 'metadata':  # if starting to sync item in shared folder
                        if self.__folders[stevent['data']['folder']]._st_event_synced:
                            self.__folders[stevent['data']['folder']]._st_event_synced = False
                    elif stevent['type'] == 'FolderSummary' and stevent['data']['folder'] in self.__folders and not self.__folders[stevent['data']['folder']]._st_event_synced:
                        fid = stevent['data']['folder']

                        self.__folders[fid]._updateVolatileData(stevent['data'])
                        self.__log(1, repr(self.__folders[fid].volatile_data()))
                        fcopy = copy.deepcopy(self.__folders[fid])
                        self._enqueueEvent(FoldersVolatileDataChangedEvent((fcopy,), 'syncthing::event'))
                        if stevent['data']['summary']['needTotalItems'] == 0:
                            self.__folders[stevent['data']['folder']]._st_event_synced = True
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
                        self.__log(1, 'FolderCompletion event for a device scheduled for deletion. c=%f' % stevent['data']['completion'])
                        # if stevent['data']['completion'] == 100 and stevent['data']['needItems'] == 0 and stevent['data']['needDeletes'] == 0 and eventtime > self.__devices[did].get_delete_after_time():
                        #     # so device is synced and now can be safely deleted
                        #     self.__log(1, 'now safe to delete device %s' % did)
                        #     del self.__devices[did]
                        #     self.__save_configuration(save_st_config=True)
                        #     #self.__save_st_config()
                        #     # Note that we don't update any device config, cuz if device scheduled for deletion - it must have already been removed from everything
                        #     # so here we just do sanity check
                        #     for fid, folder in self.__folders.items():
                        #         assert did not in folder.devices()
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

    def get_config_folder(self, devid=None):  # TODO: rename to get_control_folder
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

        if len([x for x in self.__folders if self.__folders[x].path() is not None and self.__folders[x].path() == folderPath]) > 0:
            self.__log(4, 'given folder local path already belongs to a syncing folder!')
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
            config_bootstrap = json.load(f)
            self.__log(1, config_bootstrap)

        configChanged = False

        def checkAndReturn(oldval, newval):  #TODO: we actually use this twice or so - do we even need it?
            nonlocal configChanged
            configChanged = configChanged or oldval != newval
            return newval

        self.__apikey = checkAndReturn(self.__apikey, config_bootstrap['apikey'])
        self.__log(1, 'api key = %s' % self.__apikey)
        self.httpheaders = {'X-API-Key': self.__apikey, 'Content-Type': 'application/json'}
        self.__server_secret = checkAndReturn(self.__server_secret, config_bootstrap.get('server_secret', None))
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
            self.__servers = set(config_bootstrap.get('servers', ()))  # bootstrap to have _isServer resolving correctly
            self.__log(1, 'server list loaded from bootstrap config: %s' % repr(self.__servers))
        else:
            self.__servers = set()
            self.__log(1, 'bootstrap server list ignored')
        #self.__log(1, 'server list:', self.__servers)

        configFoldPath = os.path.join(self.get_config_folder().path(), 'configuration')
        self.__log(1, 'configuration path: %s' % configFoldPath)
        os.makedirs(configFoldPath, exist_ok=True)
        if not os.path.exists(os.path.join(configFoldPath, 'config.cfg')):  # it cannot be deleted by anything, so if it doesn't exist - means we haven't initialized it at all
            with open(os.path.join(configFoldPath, 'config.cfg'), 'w') as f:
                json.dump({'devices': [],
                           'servers': [],
                           'folders': [],
                           'ignoredevices': []}, f, indent=4)

        try:
            with open(os.path.join(configFoldPath, 'config.cfg'), 'r') as f:
                configdict = json.load(f)
            self.__servers.update(configdict['servers'])
            # self.__servers.update(set(listdir(os.path.join(configFoldPath, 'servers'))))  # either update with bootstrapped, or with empty
            self.__log(1, 'final server list:', self.__servers)
            self.__devices = {}
            for devdict in configdict['devices']:
                newdevice = Device.deserialize(self, devdict)
                self.__devices[newdevice.id()] = newdevice

            # for dev in listdir(os.path.join(configFoldPath, 'devices')):
            #     with open(os.path.join(configFoldPath, 'devices', dev), 'r') as f:
            #         fdata = json.load(f)
            #     newdevice = Device.deserialize(self, fdata)
            #         # Device(self, dev, fdata.get('name', None))
            #     if newdevice.id() != dev:
            #         raise RuntimeError('config inconsistent!!')  # TODO: add special inconsistent config error
            #
            #     if dev in olddevices:
            #         olddevice = olddevices[dev]
            #         olddevices[dev] = copy.copy(olddevice)  # we will modify old device, so we need to keep old copy for future comparison
            #         olddevice.__dict__.update(newdevice.__dict__)
            #         newdevice = olddevice
            #     self.__devices[dev] = newdevice
            #     #if _isServer:
            #     #    self.__devices[dev]['controlfolder'] = {'fid': 'control-%s' % hashlib.sha1((':'.join([self.__server_secret, dev])).encode('UTF-8')).hexdigest(),
            #     #                                            'path': os.path.join(self.data_root, 'control', dev)
            #     #                                            }  # ensure controlfolder attr exist
            self.__log(1, 'final device list:', self.__devices.keys())

            # check server-dev list consistency
            for srv in set(self.__servers):
                if srv not in self.__devices:
                    self.__log(4, 'server %s not in device list! adding... though this is quite EXCEPTIONal' % srv)
                    self.__devices[srv] = Device(self, srv, None)

            self.__folders = {}
            for foldict in configdict['folders']:
                newfolder = Folder.deserialize(self, foldict)
                newfolder._setPath(config_bootstrap.get('folders', {}).get(newfolder.id(), {}).get('attribs', {}).get('path', None), move_contents=False)
                if newfolder.path() is None:
                    # TODO: add an option to control this, allow folders to stay without path
                    # TODO: ensure path does not exist already
                    newfolder._setPath(os.path.join(self.data_root, newfolder.label()))  #TODO: convert label to a valid filesystem filename !!!
                self.__folders[newfolder.id()] = newfolder

            # for fid in listdir(os.path.join(configFoldPath, 'folders')):
            #     with open(os.path.join(configFoldPath, 'folders', fid, 'attribs'), 'r') as f:
            #         fattrs = json.load(f)
            #     newfolder = Folder(self, fid, fattrs['label'], config.get('folders', {}).get(fid, {}).get('attribs', {}).get('path', None))  # TODO: if path changed suddenly and st noticed it - it will give error about missing .stfolder. We have to deal with this one way or another
            #     for dev in listdir(os.path.join(configFoldPath, 'folders', fid, 'devices')):
            #         if dev not in self.__devices:
            #             self.__log(4, 'folder device %s is not part of device list. skipping...' % dev)
            #             continue
            #         newfolder.add_device(dev)
            #     with open(os.path.join(configFoldPath, 'folders', fid, 'metadata'), 'r') as f:
            #         fmeta = json.load(f)
            #     newfolder._setMetadata(fmeta)
            #
            #     if newfolder.path() is None:
            #         # TODO: add an option to control this, allow folders to stay without path
            #         # TODO: ensure path does not exist already
            #         newfolder._setPath(os.path.join(self.data_root, newfolder.label()))  #TODO: convert label to a valid filesystem filename !!!
            #     self.__folders[fid] = newfolder

            self.__log(1, 'final folder list:', self.__folders.keys())
            self.__ignoreDevices = set()
            self.__ignoreDevices.update(configdict['ignoredevices'])
            # for dev in listdir(os.path.join(configFoldPath, 'ignoredevices')):
            #     self.__ignoreDevices.add(dev)

            for did, newdevice in self.__devices.items():  # make sure to use old objects if exist
                if did in olddevices:
                    olddevice = olddevices[did]
                    olddevices[did] = copy.copy(olddevice)  # we will modify old device, so we need to keep old copy for future comparison
                    olddevice.replace_with(newdevice)
                    newdevice = olddevice
                    self.__devices[did] = newdevice

            for fid, newfolder in self.__folders.items():  # make sure to use old objects if exist
                if fid in oldfolders:
                    oldfolder = oldfolders[fid]
                    oldfolders[fid] = copy.copy(oldfolder)
                    oldfolder.replace_with(newfolder)
                    #oldfolder.__dict__.update(newfolder.__dict__)
                    newfolder = oldfolder
                    self.__folders[fid] = newfolder

        except:  # config folder is malformed. try load cached servers and wait for config sync
            self.__servers = oldservers
            self.__devices = olddevices
            self.__folders = oldfolders  # WARNING: if exception came from within folder loop - we will not get exact folder configuration back, though it really shouldn't
            self.__ignoreDevices = oldignoredevices
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
            # generate config hash for server to confirm
            serverhash = 0
            for did in self.__servers:
                serverhash ^= hash(did)
            ignhash = 0
            for did in self.__ignoreDevices:
                ignhash ^= hash(did)
            devhash = 0
            for dev in self.__devices.values():
                devhash ^= dev.configuration_hash()
            fldhash = 0
            for fld in self.__folders.values():
                fldhash ^= fld.configuration_hash()
            os.makedirs(os.path.join(self.get_config_folder().path(), 'config_sync'), exist_ok=True)
            self.__log(1, 'saving config hash for server: %d:%d:%d:%d' % (serverhash, devhash, fldhash, ignhash))
            with open(os.path.join(self.get_config_folder().path(), 'config_sync', 'hash'), 'w') as f:
                f.write("%d:%d:%d:%d" % (serverhash, devhash, fldhash, ignhash))

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
            foldersadded = [copy.deepcopy(y) for x, y in self.__folders.items() if x not in oldfolders]
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

    def __save_device_configuration(self, deviceid: str):
        assert self._isServer(), "must be server to save config for devices"
        assert deviceid in self.__devices, "unknown device"
        self.__log(1, 'saving configuratiob for device %s' % deviceid)

        devfids = [fid for fid in self.__folders if deviceid in self.__folders[fid].devices()]
        devfiddevs = {deviceid: self.__devices[deviceid]}  # all devices that share allowed folders
        for fid in devfids:
            devfiddevs.update({did: self.__devices[did] for did in self.__folders[fid].devices()})

        for srvid in self.__servers:  # add servers to all devices list
            devfiddevs[srvid] = self.__devices[srvid]

        configFoldPath = os.path.join(self.get_config_folder(deviceid).path(), 'configuration')
        os.makedirs(configFoldPath, exist_ok=True)
        # _devpath = os.path.join(configFoldPath, 'devices')
        # #shutil.rmtree(_devpath, ignore_errors=True)  # clear existing
        #
        # _srvpath = os.path.join(configFoldPath, 'servers')
        # #shutil.rmtree(_srvpath, ignore_errors=True)  # clear existing
        #
        # _fldpath = os.path.join(configFoldPath, 'folders')
        # #shutil.rmtree(_fldpath, ignore_errors=True)  # clear existing
        #
        # _ignpath = os.path.join(configFoldPath, 'ignoredevices')
        # #shutil.rmtree(_ignpath, ignore_errors=True)  # clear existing

        #with SyncthingHandler.SyncthingPauseLock(self, self.__servers.union((deviceid,))):
        # os.makedirs(_devpath, exist_ok=True)
        # os.makedirs(_srvpath, exist_ok=True)
        # os.makedirs(_fldpath, exist_ok=True)
        # os.makedirs(_ignpath, exist_ok=True)
        configdict = {'devices': [],
                      'servers': [],
                      'folders': [],
                      'ignoredevices': []}

        # save servers
        self.__log(1, 'saving servers for device %s' % deviceid)
        configdict['servers'] = list(self.__servers)
        # for server in self.__servers:
        #     mknod(os.path.join(_srvpath, server))
        # for fname in listdir(_srvpath):
        #     if fname not in self.__servers:
        #         remove(os.path.join(_srvpath, fname))

        # save devices
        self.__log(1, 'saving devices for device %s' % deviceid)
        configdict['devices'] = [x.serialize_to_dict() for x in devfiddevs.values()]
        # for did in devfiddevs:
        #     with open(os.path.join(_devpath, did), 'w') as f:
        #         f.write(devfiddevs[did].serialize())
        # for fname in listdir(_devpath):
        #     if fname not in devfiddevs:
        #         remove(os.path.join(_devpath, fname))

        # save folders
        self.__log(1, 'saving folders for device %s' % deviceid)
        configdict['folders'] = [self.__folders[x].serialize_to_dict() for x in devfids]
        for foldict in configdict['folders']:  # make sure not to pass our local path to client
            foldict['attribs']['path'] = None
        # for fid in devfids:
        #     fiddevpath = os.path.join(_fldpath, fid, 'devices')
        #     #shutil.rmtree(fiddevpath, ignore_errors=True)
        #     os.makedirs(fiddevpath, exist_ok=True)
        #     for did in self.__folders[fid].devices():
        #         mknod(os.path.join(fiddevpath, did))
        #     for fname in listdir(fiddevpath):
        #         if fname not in self.__folders[fid].devices():
        #             remove(os.path.join(fiddevpath, fname))
        #     with open(os.path.join(_fldpath, fid, 'attribs'), 'w') as f:
        #         json.dump({'fid': fid, 'label': self.__folders[fid].label()}, f)
        #     with open(os.path.join(_fldpath, fid, 'metadata'), 'w') as f:
        #         json.dump(self.__folders[fid].metadata(), f)
        # for fname in listdir(_fldpath):
        #     if fname not in devfids:
        #         shutil.rmtree(os.path.join(_fldpath, fname), ignore_errors=True)

        # save ign dev
        self.__log(1, 'saving igndevs for device %s' % deviceid)
        configdict['ignoredevices'] = list(self.__ignoreDevices)
        # for did in self.__ignoreDevices:
        #     mknod(os.path.join(_ignpath, did))
        # for fname in listdir(_ignpath):
        #     if fname not in self.__ignoreDevices:
        #         remove(os.path.join(_ignpath, fname))

        with open(os.path.join(configFoldPath, 'config.cfg'), 'w') as f:
            self.__log(1, 'saving config: %s' % json.dumps(configdict))
            json.dump(configdict, f, indent=4)

        # save cache to check sync
        self.__log(1, 'calculating config hash for device %s' % deviceid)
        serverhash = 0
        for did in self.__servers:
            serverhash ^= hash(did)
        ignhash = 0
        for did in self.__ignoreDevices:
            ignhash ^= hash(did)
        devhash = 0
        for dev in devfiddevs.values():
            devhash ^= dev.configuration_hash()
        fldhash = 0
        for fid in devfids:
            fldhash ^= self.__folders[fid].configuration_hash()

        # for syncing purposes
        self.__devices[deviceid]._st_event_synced = False
        self.__devices[deviceid]._st_event_confighash = "%d:%d:%d:%d" % (serverhash, devhash, fldhash, ignhash)

        if self.syncthing_running() and deviceid not in self.__servers:
            self.__log(1, 'requesting control folder rescan')
            try:
                self.__post('/rest/db/scan', folder=self.get_config_folder(deviceid).fid())
            except requester.HTTPError as e:
                self.__log(4, 'rescan control folder for device %s had an error: code=%d: %s. %s' % (deviceid, e.code, e.reason, e.msg))

    def __save_bootstrapConfig(self):  #TODO: use serialize_to_dict !
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
        #with SyncthingHandler.SyncthingPauseLock(self, self.__servers):
        #if self.syncthing_running():
        #    self.__post('/rest/system/pause', {})

        self.__save_bootstrapConfig()

        configFoldPath = os.path.join(self.get_config_folder().path(), 'configuration')

        configdict = {'devices': [],
                      'servers': [],
                      'folders': [],
                      'ignoredevices': []}

        os.makedirs(configFoldPath, exist_ok=True)
        # _devpath = os.path.join(configFoldPath, 'devices')
        # _srvpath = os.path.join(configFoldPath, 'servers')
        # _fldpath = os.path.join(configFoldPath, 'folders')
        # _ignpath = os.path.join(configFoldPath, 'ignoredevices')
        #
        # # not for server - just make sure these folders exists
        # os.makedirs(_devpath, exist_ok=True)
        # os.makedirs(_srvpath, exist_ok=True)
        # os.makedirs(_fldpath, exist_ok=True)
        # os.makedirs(_ignpath, exist_ok=True)

        if self._isServer():
            # save servers
            configdict['servers'] = list(self.__servers)
            # for server in self.__servers:
            #     mknod(os.path.join(_srvpath, server))
            # for fname in listdir(_srvpath):
            #     if fname not in self.__servers:
            #         remove(os.path.join(_srvpath, fname))

            # save devices
            configdict['devices'] = [x.serialize_to_dict() for x in self.__devices.values()]
            # for dev in self.__devices:
            #     with open(os.path.join(_devpath, dev), 'w') as f:
            #         f.write(self.__devices[dev].serialize())
            # for fname in listdir(_devpath):
            #     if fname not in self.__devices:
            #         remove(os.path.join(_devpath, fname))

            # save folders
            configdict['folders'] = [x.serialize_to_dict() for x in self.__folders.values()]
            for fd in configdict['folders']:  # do not sync local path
                fd['attribs']['path'] = None
            # for fid in self.__folders:
            #     fiddevpath = os.path.join(_fldpath, fid, 'devices')
            #     #shutil.rmtree(fiddevpath, ignore_errors=True)
            #     os.makedirs(fiddevpath, exist_ok=True)
            #     for dev in self.__folders[fid].devices():
            #         mknod(os.path.join(fiddevpath, dev))
            #     for fname in listdir(fiddevpath):
            #         if fname not in self.__folders[fid].devices():
            #             remove(os.path.join(fiddevpath, fname))
            #     with open(os.path.join(_fldpath, fid, 'attribs'), 'w') as f:
            #         json.dump({'fid': fid, 'label': self.__folders[fid].label()}, f)
            #     with open(os.path.join(_fldpath, fid, 'metadata'), 'w') as f:
            #         json.dump(self.__folders[fid].metadata(), f)
            # for fname in listdir(_fldpath):
            #     if fname not in self.__folders:
            #         shutil.rmtree(os.path.join(_fldpath, fname), ignore_errors=True)

            # save ign dev
            configdict['ignoredevices'] = list(self.__ignoreDevices)
            # for dev in self.__ignoreDevices:
            #     mknod(os.path.join(_ignpath, dev))
            # for fname in listdir(_ignpath):
            #     if fname not in self.__ignoreDevices:
            #         remove(os.path.join(_ignpath, fname))
            self.__log(1, 'saving config: %s' % json.dumps(configdict))
            with open(os.path.join(configFoldPath, 'config.cfg'), 'w') as f:
                json.dump(configdict, f, indent=4)

        if save_st_config:
            self.__save_st_config()

        if self._isServer() and self.syncthing_running():
            try:
                self.__post('/rest/db/scan', folder=self.get_config_folder().fid())
            except requester.HTTPError as e:
                self.__log(4, 'rescan server config folder had an error: code=%d: %s. %s' % (e.code, e.reason, e.msg))
        #finally:
        #    if self.syncthing_running():
        #        self.__post('/rest/system/resume', {})

    def __save_st_config_fast(self):
        """
        candidate to replace __save_st_config
        :return:
        """
        self.__log(1, 'saving st configuration with http request')
        config = self.__get('/rest/system/config')

        self.__log(1, json.dumps(config))
        folders_dict = {x['id']: x for x in config.get('folders', [])}
        all_folders = set()
        devices_dict = {x['deviceID']: x for x in config.get('devices', [])}
        all_devices = set()
        ignoreddevs = config.get('ignoredDevices', [])

        if self._isServer():
            serverConfigFolder = self.get_config_folder()
            all_folders.add(serverConfigFolder.fid())
            if serverConfigFolder.fid() not in folders_dict:
                folders_dict[serverConfigFolder.fid()] = {}
            folders_dict[serverConfigFolder.fid()].update({'id': serverConfigFolder.fid(),
                                                           'label': 'server configuration',
                                                           'path': serverConfigFolder.path(),
                                                           'type': 'sendreceive',
                                                           'rescanIntervalS': 3600,
                                                           'fsWatcherEnabled': True,
                                                           'fsWatcherDelayS': 5,
                                                           'ignorePerms': True,
                                                           'autoNormalize': True,
                                                           'maxConflicts': 0,
                                                           'devices': [{'deviceID': x} for x in self.__servers]
                                                           })

            os.makedirs(serverConfigFolder.path(), exist_ok=True)


            for dev in self.__devices:
                all_devices.add(dev)
                if dev not in devices_dict:
                    devices_dict[dev] = {}
                devices_dict[dev].update({'deviceID': dev, 'name': self.__devices[dev].name(), 'compression': 'metadata', 'introducer': False})
                if 'addresses' not in devices_dict[dev]:
                    devices_dict[dev]['addresses'] = []
                if 'dynamic' not in devices_dict[dev]['addresses']:
                    devices_dict[dev]['addresses'].append('dynamic')

                if dev in self.__servers:
                    continue  # dont create control folders for servers

                # now create control folder
                controlfolder = self.get_config_folder(dev)
                all_folders.add(controlfolder.fid())
                if controlfolder.fid() not in folders_dict:
                    folders_dict[controlfolder.fid()] = {}

                folders_dict[controlfolder.fid()].update({'id': controlfolder.fid(),
                                                          'label': 'control for %s' % dev,
                                                          'path': controlfolder.path(),
                                                          'type': 'sendreceive',
                                                          'rescanIntervalS': 3600,
                                                          'fsWatcherEnabled': True,
                                                          'fsWatcherDelayS': 5,
                                                          'ignorePerms': True,
                                                          'autoNormalize': True,
                                                          'maxConflicts': 0,
                                                          'devices': [{'deviceID': x} for x in self.__servers.union((dev,))]
                                                          })

                os.makedirs(controlfolder.path(), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'active'), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'archive'), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'config_sync'), exist_ok=True)
                os.makedirs(os.path.join(controlfolder.path(), 'configuration'), exist_ok=True)

        else:  # not server
            for dev in self.__devices:
                all_devices.add(dev)
                if dev not in devices_dict:
                    devices_dict[dev] = {}
                devices_dict[dev].update({'deviceID': dev, 'name': self.__devices[dev].name(), 'compression': 'metadata', 'introducer': False})
                if 'addresses' not in devices_dict[dev]:
                    devices_dict[dev]['addresses'] = []
                if 'dynamic' not in devices_dict[dev]['addresses']:
                    devices_dict[dev]['addresses'].append('dynamic')

            controlfolder = self.get_config_folder()
            all_folders.add(controlfolder.fid())
            if controlfolder.fid() not in folders_dict:
                folders_dict[controlfolder.fid()] = {}

            folders_dict[controlfolder.fid()].update({'id': controlfolder.fid(),
                                                      'label': 'control for %s' % self.__myid,
                                                      'path': controlfolder.path(),
                                                      'type': 'sendreceive',
                                                      'rescanIntervalS': 3600,
                                                      'fsWatcherEnabled': True,
                                                      'fsWatcherDelayS': 5,
                                                      'ignorePerms': True,
                                                      'autoNormalize': True,
                                                      'maxConflicts': 0,
                                                      'devices': [{'deviceID': x} for x in self.__servers.union((self.__myid,))]
                                                      })

            os.makedirs(controlfolder.path(), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'active'), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'archive'), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'config_sync'), exist_ok=True)
            os.makedirs(os.path.join(controlfolder.path(), 'configuration'), exist_ok=True)

        ignoreddevs = [{'deviceID': x} for x in self.__ignoreDevices]

        for fol in self.__folders:
            all_folders.add(fol)
            if fol not in folders_dict:
                folders_dict[fol] = {}
            folders_dict[fol].update({'id': fol,
                                      'label': self.__folders[fol].label(),
                                      'path': self.__folders[fol].path(),
                                      'type': 'sendreceive',
                                      'rescanIntervalS': 3600,
                                      'fsWatcherEnabled': True,
                                      'fsWatcherDelayS': 10,
                                      'ignorePerms': True,
                                      'autoNormalize': True,
                                      'devices': [{'deviceID': x} for x in self.__servers.union(self.__folders[fol].devices())]
                                      })

            os.makedirs(self.__folders[fol].path(), exist_ok=True)

        for x in tuple(devices_dict.keys()):
            if x not in all_devices:
                del devices_dict[x]
        for x in tuple(folders_dict.keys()):
            if x not in all_folders:
                del folders_dict[x]

        config['devices'] = list(devices_dict.values())
        config['folders'] = list(folders_dict.values())

        config.get('gui', {}).update({'enabled': True,
                                      'tls': False,
                                      'debugging': True,
                                      'address': '%s:%d' % (self.syncthing_gui_ip, self.syncthing_gui_port),
                                      'apikey': self.__apikey
                                      })

        config.get('options', {}).update({'listenAddress': self.syncthing_listenaddr})

        self.__log(1, 'sending config:')
        self.__log(1, json.dumps(config))
        try:
            self.__post('/rest/system/config', config)
            if not self.__get('/rest/system/config/insync').get('configInSync', False):
                self.__post('/rest/system/restart')
        except requester.HTTPError as e:
            self.__log(4, 'ERROR SUBMITTING CONFIG! code=%d: %s. %s' % (e.code, e.reason, e.msg))

    def __save_st_config(self):
        if self.__defer_stupdate:
            self.__log(1, 'st configuration will be updated when methodbatch finishes')
            self.__defer_stupdate_writerequired = True
            return
        if self.syncthing_running():
            try:
                return self.__save_st_config_fast()
            except Exception as e:
                self.__log(1, repr(e))
                raise
        else:
            return self.__save_st_config_old()

    def __save_st_config_old(self):
        """
        saves current configuration to syncthing config and restarts syncthing if needed
        :return:
        """
        self.__log(1, 'saving st configuration with config file replacement')
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
                os.makedirs(os.path.join(controlfolder.path(), 'config_sync'), exist_ok=True)
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
            os.makedirs(os.path.join(controlfolder.path(), 'config_sync'), exist_ok=True)
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

    def __pause_syncthing(self, devids: Optional[Iterable[str]] = None):
        self.__log(1, 'pausing syncthing...')
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            self.__log(1, 'syncthing is not running, throwing')
            raise RuntimeError('syncthing is not running')
        if devids is None:
            self.__post('/rest/system/pause')
        else:
            for devid in devids:
                try:
                    self.__post('/rest/system/pause', device=devid)
                except requester.HTTPError as e:
                    if e.code != 404:
                        raise

    def __resume_syncthing(self, devids: Optional[Iterable[str]] = None):
        self.__log(1, 'resuming syncthing...')
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            self.__log(1, 'syncthing is not running, throwing')
            raise RuntimeError('syncthing is not running')
        if devids is None:
            self.__post('/rest/system/resume')
        else:
            for devid in devids:
                try:
                    self.__post('/rest/system/resume', device=devid)
                except requester.HTTPError as e:
                    if e.code != 404:
                        raise

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

    def __post(self, path, data=None, **kwargs):
        if self.syncthing_proc is None or self.syncthing_proc.poll() is not None:
            raise SyncthingNotReadyError()
        url = "http://%s:%d%s" % (self.syncthing_gui_ip, self.syncthing_gui_port, path)
        if len(kwargs) > 0:
            url += '?' + '&'.join(['%s=%s' % (k, str(kwargs[k])) for k in kwargs.keys()])
        self.__log(0, "posting %s with data %s" % (url, repr(data)))
        req = requester.Request(url, None if data is None else json.dumps(data).encode('utf-8'), headers=self.httpheaders)
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

import os

from .servercomponent import ServerComponent
from .lance_utils import async_method
from .logger import get_logger

from . import syncthinghandler

from typing import Dict, Set, Optional, Iterable


class ConfigurationInconsistentError(RuntimeError):
    pass

log = get_logger('ProjectManager')

class ShotPart:
    def __init__(self, stfolder: syncthinghandler.Folder):
        self.__usersids = set()
        self.__usersids_tuple = None
        self.__project = None  # type: Optional[str]
        self.__shot = None  # type: Optional[str]
        self.__stfolder = None  # type: Optional[syncthinghandler.Folder]
        self.update_folder(stfolder)

    def _parseFolder(self):
        prjmeta = self.__stfolder.metadata()['__ProjectManager_data__']
        self.__project = prjmeta['project']
        self.__shot = prjmeta['shot']

    def project(self):
        return self.__project

    def shot(self):
        return self.__shot

    def users(self):
        if self.__usersids_tuple is None:
            self.__usersids_tuple = tuple(self.__usersids)
        return self.__usersids_tuple

    def set_users(self, user_ids: Iterable):
        if self.__usersids == user_ids:
            return
        self.__usersids_tuple = None
        self.__usersids = set(user_ids)

    def update_folder(self, stfolder: syncthinghandler.Folder):
        if self.__stfolder is not None and self.__stfolder.id() != stfolder.id():
            log(2, 'ShotPart::update_folder: new folder id differs from existing one')

        self.__stfolder = stfolder  # Note - this is not a LIVE version of the Folder - just a copy with latest reported changes
        assert '__ProjectManager_data__' in self.__stfolder.metadata() and 'type' in self.__stfolder.metadata()['__ProjectManager_data__'] and self.__stfolder.metadata()['__ProjectManager_data__']['type'] == 'shot', 'bad folder metadata'
        self._parseFolder()

    def __eq__(self, other: 'ShotPart'):
        return self.__usersids == other.__usersids and \
               self.__project == other.__project and \
               self.__shot == other.__shot and \
               self.__stfolder.id() == other.__stfolder.id()


class User:
    def __init__(self, mdata):
        self.__id = mdata['id']
        self.__readableName = mdata['name']
        self.__deviceids = set(mdata['deviceids'])
        self.__deviceids_tuple = None

    def id(self):
        return self.__id

    def name(self):
        return self.__readableName

    def device_ids(self):
        if self.__deviceids_tuple is None:
            self.__deviceids_tuple = tuple(self.__deviceids)
        return self.__deviceids_tuple

    def add_device(self, did: str):
        self.__deviceids.add(did)
        self.__deviceids_tuple = None

    def remove_device(self, did: str):
        if did not in self.__deviceids:
            return
        self.__deviceids.remove(did)
        self.__deviceids_tuple = None


class ProjectManager(ServerComponent):
    """
    this component uses SyncthingHandler folders' metadata to keep project configuration
    there might be more than one project manager, each managing it's own project
    """
    @classmethod
    def get_project_names(cls, server) -> Set[str]:
        sth = server.syncthingHandler  # type: syncthinghandler.SyncthingHandler
        folders = sth.get_folders().result()
        projectnames = set()
        for fid, folder in folders.items():
            fmeta = folder.metadata()
            if '__ProjectManager_data__' not in fmeta:
                continue
            pm_metadata = fmeta['__ProjectManager_data__']
            projectnames.add(pm_metadata['project'])
        return projectnames

    def __init__(self, server, project):
        super(ProjectManager, self).__init__(server)
        self.__sthandler = server.syncthingHandler  # type: syncthinghandler.SyncthingHandler
        # TODO: detect required components in a more dynamic way
        self.__project = project
        self.__projectSettingsFolder = None
        self.__shots = {}
        self.__users = None

    def __rescanConfiguration(self):
        try:
            folders = self.__sthandler.get_folders().result()  # type: Dict[str, syncthinghandler.Folder]
        except Exception as e:
            raise ConfigurationInconsistentError('syncthing returned %s' % repr(e))

        oldshots = self.__shots
        oldusers = self.__users
        self.__shots = {}
        self.__users = {}

        # load project folders
        for fid, folder in folders.items():
            fmeta = folder.metadata()
            if '__ProjectManager_data__' not in fmeta:
                continue
            pm_metadata = fmeta['__ProjectManager_data__']
            if pm_metadata['project'] != self.__project:
                continue

            if pm_metadata['type'] == 'server.configuration':
                self.__projectSettingsFolder = folder
            elif pm_metadata['type'] == 'shotpart':
                shotpart = ShotPart(folder)
                shotid = pm_metadata['shot']
                if shotid not in self.__shots:
                    self.__shots[shotid] = set()
                self.__shots[shotid].add(shotpart)

        # load users
        configpath = self.__projectSettingsFolder.path()
        users = os.listdir(os.path.join(configpath, 'users'))
        for username in users:
            devids = os.listdir(os.path.join(configpath, 'users', username, 'devices'))
            with open(os.path.join(configpath, 'users', username, 'attribs'), 'r') as f:
                userdata = f.read()
            userdata['deviceids'] = devids
            user = User(userdata)
            self.__users[user.id()] = user

        # load access

        for shotid, shot in self.__shots:
            pass

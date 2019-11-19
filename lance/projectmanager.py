
from .servercomponent import ServerComponent
from .lance_utils import async_method

from . import syncthinghandler

from typing import Dict


class ConfigurationInconsistentError(RuntimeError):
    pass


class ShotPart:
    def __init__(self, stfolder: syncthinghandler.Folder):
        self.__stfolder = stfolder  # Note - this is not a LIVE version of the Folder - just a copy with latest reported changes
        assert '__ProjectManager_data__' in self.__stfolder.metadata() and 'type' in self.__stfolder.metadata()['__ProjectManager_data__']  and self.__stfolder.metadata()['__ProjectManager_data__']['type'] == 'shot', 'bad folder metadata'
        self.__usersids = set()
        self.__project = None
        self._parseFolder()

    def _parseFolder(self):
        prjmeta = self.__stfolder.metadata()['__ProjectManager_data__']
        self.__usersids = set(prjmeta['users'])
        self.__project = prjmeta['project']


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
    """
    def __init__(self, server):
        super(ProjectManager, self).__init__(server)
        self.__sthandler = server.syncthingHandler  # type: syncthinghandler.SyncthingHandler
        # TODO: detect required components in a more dynamic way

        self.__projectSettingsFolder = None
        self.__shots = set()

    def __rescanConfiguration(self):
        try:
            folders = self.__sthandler.get_folders().result()  # type: Dict[str, syncthinghandler.Folder]
        except Exception as e:
            raise ConfigurationInconsistentError('syncthing returned %s' % repr(e))

        #find project folder
        for fid, folder in folders.items():
            fmeta = folder.metadata()
            if '__ProjectManager_data__' not in fmeta:
                continue
            pm_metadata = fmeta['__ProjectManager_data__']

            if pm_metadata['type'] == 'server.configuration':
                self.__projectSettingsFolder = folder
            elif pm_metadata['type'] == 'shot':


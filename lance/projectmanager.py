import os

try:
    import simplejson as json
except ImportError:
    import json

import string
import random
import copy

from .servercomponent import ServerComponent
from .lance_utils import async_method, async_method_queueonly
from .logger import get_logger

from . import syncthinghandler
from . import eventprocessor

from typing import Union, List, Set, Dict, Set, Optional, Iterable, Tuple


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
        self.__fid = None  # type: Optional[str]
        self.__id = None  # type: Optional[str]
        self.update_folder(stfolder)

    def _parseFolder(self):
        prjmeta = self.__stfolder.metadata()['__ProjectManager_data__']
        self.__project = prjmeta['project']
        self.__shot = prjmeta['shot']
        self.__id = prjmeta['shotpart']
        self.__fid = self.__stfolder.id()

    def stfolder_id(self):
        return self.__stfolder.id()

    def id(self):
        return self.__id

    def project(self):
        return self.__project

    def shot(self):
        return self.__shot

    def users(self):
        if self.__usersids_tuple is None:
            self.__usersids_tuple = tuple(self.__usersids)
        return self.__usersids_tuple

    def add_user(self, user_id: str):
        if user_id in self.__usersids:
            return
        self.__usersids.add(user_id)
        self.__usersids_tuple = None

    def remove_user(self, user_id: str):
        if user_id not in self.__usersids:
            return
        self.__usersids.remove(user_id)
        self.__usersids_tuple = None

    def set_users(self, user_ids: Iterable):
        if self.__usersids == user_ids:
            return
        self.__usersids_tuple = None
        self.__usersids = set(user_ids)

    def update_folder(self, stfolder: syncthinghandler.Folder):
        if self.__stfolder is not None and self.__stfolder.id() != stfolder.id():
            log(2, 'ShotPart::update_folder: new folder id differs from existing one')

        self.__stfolder = stfolder  # Note - this is not a LIVE version of the Folder - just a copy with latest reported changes
        assert '__ProjectManager_data__' in self.__stfolder.metadata() and 'type' in self.__stfolder.metadata()['__ProjectManager_data__'] and self.__stfolder.metadata()['__ProjectManager_data__']['type'] == 'shotpart', 'bad folder metadata'
        self._parseFolder()

    def __eq__(self, other: 'ShotPart'):
        return self.__usersids == other.__usersids and \
               self.__project == other.__project and \
               self.__shot == other.__shot and \
               self.__id == other.__id and \
               self.__stfolder.id() == other.__stfolder.id()


class User:
    def __init__(self, mdata):
        self.__id = mdata['id']
        self.__readableName = mdata['name']
        self.__deviceids = set(mdata['deviceids'])
        self.__deviceids_tuple = None
        self.__access_shotids = set()  # type: Set[Tuple[str, str]]
        for shotid, partid in mdata['access']:
            self.__access_shotids.add((shotid, partid))
        self.__access_shotids_tuple = None

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

    def available_shotparts(self):
        if self.__access_shotids_tuple is None:
            self.__access_shotids_tuple = tuple(self.__access_shotids)
        return self.__access_shotids_tuple

    def add_shotpart(self, shotid, shotpartid):
        self.__access_shotids.add((shotid, shotpartid))
        self.__access_shotids_tuple = None

    def remove_shotpart(self, shotid, shotpartid):
        self.__access_shotids.remove((shotid, shotpartid))
        self.__access_shotids_tuple = None


class ProjectManager(ServerComponent, eventprocessor.BaseEventProcessorInterface):
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

    def __init__(self, server, project: str):
        super(ProjectManager, self).__init__(server)
        self.__sthandler = server.syncthingHandler  # type: syncthinghandler.SyncthingHandler
        # TODO: detect required components in a more dynamic way
        self.__project = project
        self.__projectSettingsFolder = None
        self.__shots = {}  # type: Dict[str, Dict[str, ShotPart]]
        self.__users = {}  # type: Dict[str, User]
        self.__configInSync = False
        self.__log = get_logger('%s %s' % (self.__sthandler.myId()[:5], self.__class__.__name__))
        self._server.eventQueueEater.add_event_processor(self)

    def run(self):
        super(ProjectManager, self).run()
        # just add a cleanup event after default run has exited
        self._server.eventQueueEater.remove_event_provessor(self)

    # Event processor methods
    @async_method_queueonly(raise_while_invoking=True)
    def add_event(self, event):
        self.__log(1, 'PManager: event received: %s, config sync: %s' % (repr(event), self.__configInSync))
        if not self.__configInSync:
            if isinstance(event, syncthinghandler.ConfigSyncChangedEvent):
                self.__log(1, 'new config sync status = %s' % repr(event.in_sync()))
                self.__configInSync = event.in_sync()
                if self.__configInSync:
                    self.__rescanConfiguration()
                return
        else:  # main syncthinghandler config is in sync
            if isinstance(event, syncthinghandler.FoldersSyncedEvent):
                for folder in event.folders():
                    if '__ProjectManager_data__' not in folder.metadata():
                        continue
                    metadata = folder.metadata()['__ProjectManager_data__']
                    if metadata['project'] != self.__project:
                        continue

                    if metadata['type'] == 'shotpart':
                        if metadata['shot'] not in self.__shots:
                            self.__log(2, "unknown shot %s" % metadata['shot'])
                            continue
                        if folder.id() not in self.__shots[metadata['shot']]:
                            self.__log(1, 'FoldersSyncedEvent: shot part synced is unknown: %s  adding to configuration' % folder.id())
                            self.__shots[metadata['shot']][folder.id()] = ShotPart(folder)

                    elif metadata['type'] == 'server.configuration':
                        self.__log(1, 'server.configuration changed, rescanning config')
                        self.__rescanConfiguration()

            elif isinstance(event, syncthinghandler.FoldersAddedEvent):  # This folders may be duplicated if rescanConfiguration
                for folder in event.folders():
                    if '__ProjectManager_data__' not in folder.metadata():
                        continue
                    metadata = folder.metadata()['__ProjectManager_data__']
                    if metadata['project'] != self.__project:
                        continue
                    break
                else:
                    self.__rescanConfiguration()

                    # if folder.id() in (x.id() for x in self.__shots[metadata['shot']]):
                    #     self.__log(1, 'addFolderEvent: shot part is present: %s' % folder.id())
                    #     continue
                    #
                    # self.__shots[metadata['shot']].add(ShotPart(folder))
            elif isinstance(event, syncthinghandler.FoldersConfigurationChangedEvent):  # this change includes metadata
                for folder in event.folders():
                    if '__ProjectManager_data__' not in folder.metadata():
                        continue
                    metadata = folder.metadata()['__ProjectManager_data__']
                    if metadata['project'] != self.__project:
                        continue
                    break
                else:
                    self.__rescanConfiguration()

                    # for shotpart in self.__shots[metadata['shot']]:
                    #     if shotpart.id() == folder.id():
                    #         shotpart.update_folder(folder)
                    #         break
                    # else:
                    #     self.__log(3, 'shotpart update received, shotpart does not exist %s' % folder.id())
            elif isinstance(event, syncthinghandler.FoldersVolatileDataChangedEvent):  # TODO: treat this event better
                for folder in event.folders():
                    if '__ProjectManager_data__' not in folder.metadata():
                        continue
                    metadata = folder.metadata()['__ProjectManager_data__']
                    if metadata['project'] != self.__project:
                        continue

                    if metadata['type'] != 'shotpart':
                        continue
                    if metadata['shot'] not in self.__shots:
                        self.__log(2, "unknown shot %s" % metadata['shot'])
                        continue
                    for shotpartid, shotpart in self.__shots[metadata['shot']].items():
                        if shotpart.stfolder_id() == folder.id():
                            shotpart.update_folder(folder)
                            break
                    else:
                        self.__log(3, 'shotpart volatile update received, shotpart does not exist %s' % folder.id())
            elif isinstance(event, syncthinghandler.FoldersRemovedEvent):
                for folder in event.folders():
                    if '__ProjectManager_data__' not in folder.metadata():
                        continue
                    metadata = folder.metadata()['__ProjectManager_data__']
                    if metadata['project'] != self.__project:
                        continue
                    break
                else:
                    self.__rescanConfiguration()

    @classmethod
    def is_init_event(cls, event):
        raise RuntimeError("this should not be called! don't add this handler as autohandler to queueeater!")

    def is_expected_event(self, event):
        return isinstance(event, syncthinghandler.FoldersConfigurationEvent) or isinstance(event, syncthinghandler.ConfigSyncChangedEvent)
    # End Event processor methods

    def __rescanConfiguration(self):
        try:
            folders = self.__sthandler.get_folders().result()  # type: Dict[str, syncthinghandler.Folder]
        except Exception as e:
            raise ConfigurationInconsistentError('syncthing returned %s' % repr(e))
        self.__log(1, 'rescanning configuration...')

        oldprojectconfigfolder = self.__projectSettingsFolder
        oldshots = self.__shots
        oldusers = self.__users
        self.__shots = {}
        self.__users = {}
        #allshotparts = {}  # type: Dict[str, ShotPart]
        self.__projectSettingsFolder = None

        if self.__sthandler._isServer():  # we are the server
            # load project folders
            self.__log(1, 'i am server, so i load main config')
            self.__log(1, 'scanning folders...')
            for fid, folder in folders.items():
                fmeta = folder.metadata()
                if '__ProjectManager_data__' not in fmeta:
                    continue
                pm_metadata = fmeta['__ProjectManager_data__']

                if 'project' not in pm_metadata:
                    self.__log(2, 'project metadata is corrupt: no project key')
                    continue
                if pm_metadata['project'] != self.__project:
                    continue

                if 'type' not in pm_metadata:
                    self.__log(2, 'project metadata is corrupt: no type key')
                    continue
                if pm_metadata['type'] == 'server.configuration':
                    self.__projectSettingsFolder = folder
                elif pm_metadata['type'] == 'shotpart':
                    shotpart = ShotPart(folder)
                    #allshotparts[shotpart.id()] = shotpart
                    shotid = pm_metadata['shot']
                    if shotid not in self.__shots:
                        self.__shots[shotid] = dict()
                    self.__shots[shotid][shotpart.id()] = shotpart

            if self.__projectSettingsFolder is None:
                # maybe syncthing handler is still in sync, so we will wait for it
                # so we wait for flodersaddedevent
                self.__log(2, 'project settings folder was not found! unsynced config?')
                pass
            else:
                # load users
                self.__log(1, 'project config found, loading users')
                configpath = self.__projectSettingsFolder.path()
                try:
                    with open(os.path.join(configpath, 'config.cfg'), 'r') as f:
                        config = json.load(f)
                except:
                    self.__log(3, 'config loading error - might be not in sync, might be corrupted')
                else:
                    users = config.get('users', {})
                    for userid, userdata in users.items():
                        try:
                            user = User(userdata)
                        except:  # malformed user data, skipping
                            continue
                        if userid != user.id():  # sanity check
                            continue
                        self.__users[user.id()] = user

                        for shotid, shotpartid in user.available_shotparts():
                            if shotid not in self.__shots:
                                self.__log(1, 'shot %s is not part of this server' % shotid)
                                continue
                            if shotpartid not in self.__shots[shotid]:
                                self.__log(1, 'shotpart %s is not part of this server' % shotpartid)
                                continue
                            self.__shots[shotid][shotpartid].add_user(userid)

            configchanged = self.__shots != oldshots or self.__users != oldusers or oldprojectconfigfolder != self.__projectSettingsFolder

            self.__log(1, 'shots: %s' % ", ".join(self.__shots.keys()))
            self.__log(1, '\n'.join(('%s:\n%s' % (shk, '\n\t'.join((x for x in self.__shots[shk]))) for shk in self.__shots)))
            self.__log(1, 'users: %s' % ", ".join(self.__users.keys()))

            folderidToDevidset = {}  # type: Dict[str, Set[str]]
            for shotid, shotpartdict in self.__shots.items():
                for shotpart in shotpartdict.values():
                    allShotpartDevids = set()
                    for userid in shotpart.users():
                        allShotpartDevids.update(self.__users[userid].device_ids())
                    folderidToDevidset[shotpart.stfolder_id()] = allShotpartDevids

            allDevids = set()
            for devidset in folderidToDevidset.values():
                allDevids.update(devidset)

            self.__sthandler.set_devices(allDevids)  # note that sthandler will only make changes if devlists do not match

            for folderid, allShotpartDevids in folderidToDevidset.items():
                self.__sthandler.set_folder_devices(folderid, allShotpartDevids)

        else:  # not server
            configchanged = self.__shots != oldshots

        # if configchanged:
        #     if self.__shots != oldshots:
        #         alloldshotparts = {}
        #         for shpdict in self.__shots.values():
        #             for shp in shpdict.values():
        #                 alloldshotparts[shp.id()] = shp
        #
        #         #newshotparts = [copy.deepcopy(x) for xid, x in allshotparts.items() if xid not in alloldshotparts]
        #         #removedshotparts = [copy.deepcopy(x) for xid, x in alloldshotparts.items() if xid not in allshotparts]
        #         #updatedshotparts = [copy.deepcopy(x) for xid, x in allshotparts.items() if xid in alloldshotparts and x != alloldshotparts[xid]]
        #
        #         #self._enqueueEvent()  # TODO: emit events

        return configchanged

    # interface methods
    def __interface_addShotPart(self, shotname: str, shotid: str, shotpartid: str, path: str):
        """
        add a shot with one shotpart to this project
        :shotname: human readable shot name for display
        :shotid: less human readable unique shot identificator
        :shotpartid: name of the shotpart
        :path: path on file system to the shot folder, local for the server, will not be sent to or from clients
        :return:
        """
        meta = dict()
        meta['__ProjectManager_data__'] = {'type': 'shotpart',
                                           'project': self.__project,
                                           'shot': shotid,
                                           'shotpart': shotpartid}
        fid = "folder-{project}-{shotid}-{shotpartid}-{randstr}".format(project=self.__project, shotid=shotid, shotpartid=shotpartid, randstr=''.join(random.choice(string.ascii_lowercase) for _ in range(16)))

        self.__sthandler.add_folder(path, '%s :%s' % (shotname, shotpartid), devList=None, metadata=meta, overrideFid=fid)
        # now we should wait for sthandler to send addfolder event for our fid

    # Interface
    @async_method()
    def add_shot(self, shotname: str, shotid: str, path: str):  #TODO: make shotid autogeneratable
        """
        only server can do that
        exception will be raised if u no server
        :param shotname:
        :param shotid:
        :param path:
        :return:
        """
        return self.__interface_addShotPart(shotname, shotid, 'main', path)

    @async_method()
    def remove_shot(self, shotid: str):
        """
        only server can do that
        exception will be raised if u no server
        :param shotid:
        :return:
        """
        self.__log(1, 'removing shotid %s' % shotid)
        if shotid not in self.__shots:
            self.__log(2, 'shotid %s is not part of project %s' % (shotid, self.__project))
        for shotpart in self.__shots[shotid].values():
            self.__sthandler.remove_folder(shotpart.stfolder_id())

    @async_method()
    def remove_shotpart(self, shotpart: Union[str, ShotPart]):
        if isinstance(shotpart, ShotPart):
            return self.__sthandler.remove_folder(shotpart.id())
        elif isinstance(shotpart, str):
            return self.__sthandler.remove_folder(shotpart)
        raise AttributeError('attrib shotpart of wrong type: %s' % repr(type(shotpart)))

    @async_method()
    def get_shots(self):
        return copy.deepcopy(self.__shots)

    def project_name(self):
        return self.__project

    @async_method()
    def rescan_configuration(self):
        return self.__rescanConfiguration()

    @async_method()
    def get_users(self):
        return copy.deepcopy(self.__users)

    @async_method()
    def add_user(self, userid: str, username: str, dev_list: Optional[Iterable[str]], shotid_partname_pair_list: Optional[List[Tuple[str, str]]] = None):
        if self.__projectSettingsFolder is None:
            raise RuntimeError('only server can add users')
        configpath = self.__projectSettingsFolder.path()
        with open(os.path.join(configpath, 'config.cfg'), 'r') as f:
            config = json.load(f)

        if userid in config.get('users', {}):
            self.__log(2, 'add_user: userid already exists')
            return

        if dev_list is None:
            def_list = []
        if shotid_partname_pair_list is None:
            shot_partid_pair_list = []

        if 'users' not in config:
            config['users'] = {}
        config['users'][userid] = {'id': userid,
                                   'name': username,
                                   'deviceids': dev_list,
                                   'access': shotid_partname_pair_list}

        with open(os.path.join(configpath, 'config.cfg'), 'w') as f:
            json.dump(config, f)
        # after this syncthing should sync folders and emit foldersync event

    @async_method()
    def remove_user(self, userid: str):
        if self.__projectSettingsFolder is None:
            raise RuntimeError('only server can add users')
        configpath = self.__projectSettingsFolder.path()
        with open(os.path.join(configpath, 'config.cfg'), 'r') as f:
            config = json.load(f)

        if userid not in config.get('users', {}):
            self.__log(2, 'add_user: userid does not exists')
            return

        del config['users'][userid]

        with open(os.path.join(configpath, 'config.cfg'), 'w') as f:
            json.dump(config, f)
        # after this syncthing should sync folders and emit foldersync event

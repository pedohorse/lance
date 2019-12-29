import os

try:
    import simplejson as json
except ImportError:
    import json

import string
import random
import copy
import time

from .servercomponent import ServerComponent
from .lance_utils import async_method
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
        self.__shot = prjmeta['shotid']
        self.__id = prjmeta['shotpartid']
        self.__fid = self.__stfolder.id()

    def stfolder_id(self):
        return self.__stfolder.id()

    def id(self):
        return self.__id

    def syncthing_folderid(self):
        return self.__fid

    def project(self):
        return self.__project

    def shotid(self):
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
            log(4, 'ShotPart::update_folder: new folder id differs from existing one')

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
        self.__deviceids = set(mdata['deviceids'])  # type: Set[str]
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

    def __init__(self, server, project: str):  #, config_sync_status=False):
        super(ProjectManager, self).__init__(server)
        self.__sthandler = server.syncthingHandler  # type: syncthinghandler.SyncthingHandler
        # TODO: detect required components in a more dynamic way
        self.__project = project
        self.__projectSettingsFolder = None
        self.__shots = {}  # type: Dict[str, Dict[str, ShotPart]]
        self.__users = {}  # type: Dict[str, User]
        #self.__configInSync = config_sync_status
        self.__log = get_logger('%s %s' % (self.__sthandler.myId()[:5], self.__class__.__name__))
        self._server.eventQueueEater.add_event_processor(self)

    def run(self):
        super(ProjectManager, self).run()
        # just add a cleanup event after default run has exited
        self._server.eventQueueEater.remove_event_provessor(self)

    # Event processor methods
    @async_method(raise_while_invoking=True, queue_only=True)
    def add_event(self, event):
        self.__log(1, 'PManager: event received: %s' % (repr(event),))
        # if not self.__configInSync:
        #     if isinstance(event, syncthinghandler.ConfigSyncChangedEvent):
        #         self.__log(1, 'new config sync status = %s' % repr(event.in_sync()))
        #         self.__configInSync = event.in_sync()
        #         if self.__configInSync:
        #             self.__rescanConfiguration(rescan_project_settings=False)
        #         return
        # else:  # main syncthinghandler config is in sync

        # a couple of shortcuts:
        def event_add_shotpart(folder: syncthinghandler.Folder):
            if '__ProjectManager_data__' not in folder.metadata():
                self.__log(4, 'given folder does not have project metadata: %s' % folder.id())
                return False
            metadata = folder.metadata()['__ProjectManager_data__']
            if metadata['project'] != self.__project:
                self.__log(4, 'given folder %s belongs to project %s' % (folder.id(), metadata['project']))
                return False
            if metadata['type'] != 'shotpart':
                self.__log(4, 'given folder %s is not a shotpart: %s' % (folder.id(), metadata['type']))
                return False
            if self.__shots.get(metadata['shotid'], {}).get(metadata['shotpartid'], None) is not None:
                self.__log(4, 'added folder shotid shotpart already exist! %s %s' % (metadata['shotid'], metadata['shotpartid']))
                return False

            if metadata['shotid'] not in self.__shots:
                self.__shots[metadata['shotid']] = {}
            newshotpart = ShotPart(folder)
            self.__shots[metadata['shotid']][metadata['shotpartid']] = newshotpart
            for userid, user in self.__users.items():
                if (newshotpart.shotid(), newshotpart.id()) in user.available_shotparts():
                    newshotpart.add_user(userid)
            return True

        def event_remove_shotpart(shotid: str, shotpartid: str):
            if shotid not in self.__shots:
                self.__log(4, 'shotid %s does not exist' % shotid)
                return False
            if shotpartid not in self.__shots[shotid]:
                self.__log(4, 'shotpartid %s does not exist' % shotpartid)
                return False
            del self.__shots[shotid][shotpartid]
            if len(self.__shots[shotid]) == 0:
                del self.__shots[shotid]
            return True

        if isinstance(event, syncthinghandler.FoldersSyncedEvent):
            for folder in event.folders():
                if '__ProjectManager_data__' not in folder.metadata():
                    continue
                metadata = folder.metadata()['__ProjectManager_data__']
                if metadata['project'] != self.__project:
                    continue

                if metadata['type'] == 'shotpart':  # synced data
                    if metadata['shotid'] not in self.__shots:
                        self.__log(2, "unknown shotid %s" % metadata['shotid'])
                        continue
                    if metadata['shotpartid'] not in self.__shots[metadata['shotid']]:
                        self.__log(1, 'shotid part synced is unknown: %s  adding to configuration' % metadata['shotpartid'])
                        self.__shots[metadata['shotid']][metadata['shotpartid']] = ShotPart(folder)

                elif metadata['type'] == 'server.configuration':  # configuration changed
                    self.__log(1, 'server.configuration changed, rescanning config')
                    self.__rescanConfiguration(rescan_project_settings=True)

        elif isinstance(event, syncthinghandler.FoldersAddedEvent):
            for folder in event.folders():
                if '__ProjectManager_data__' not in folder.metadata():
                    continue
                metadata = folder.metadata()['__ProjectManager_data__']
                if metadata['project'] != self.__project:
                    continue

                if metadata['type'] == 'shotpart':
                    event_add_shotpart(folder)
                    # no need to rescan config

        elif isinstance(event, syncthinghandler.FoldersConfigurationChangedEvent):  # this change includes metadata
            for folder in event.folders():
                if '__ProjectManager_data__' not in folder.metadata():
                    continue
                metadata = folder.metadata()['__ProjectManager_data__']

                if metadata['type'] != 'shotpart':  # so shotpart changed settings
                    fid_to_shotpart = {}
                    for shotid, shotpartdict in self.__shots.items():
                        for shotpartid, shotpart in shotpartdict.items():
                            fid_to_shotpart[shotpart.stfolder_id()] = shotpart
                    if folder.id() in fid_to_shotpart:
                        shotpart = fid_to_shotpart[folder.id()]
                        changed_project = metadata['project'] != self.__project
                        changed_shot = metadata['shotid'] != shotpart.shotid()
                        changed_shotpart = metadata['shotpartid'] != shotpart.id()
                        if changed_project or changed_shot or changed_shotpart:
                            # removing shotpart from the list
                            del self.__shots[shotpart.shotid()][shotpart.id()]
                            if len(self.__shots[shotpart.shotid()]) == 0:
                                del self.__shots[shotpart.shotid()]
                            # now deciding what to do with it
                            if changed_project:
                                self.__log(1, 'folder %s changed project from %s to %s. removing it from our shotlist' % (folder.label(), self.__project, metadata['project']))
                            else:
                                if changed_shot:
                                    self.__log(1, 'folder %s changed shot id from %s to %s.' % (folder.label(), shotpart.shotid(), metadata['shotid']))
                                else:  # changed_shotpart
                                    self.__log(1, 'folder %s changed shotpart id from %s to %s.' % (folder.label(), shotpart.shotid(), metadata['shotid']))
                                event_add_shotpart(folder)
                    elif metadata['project'] == self.__project:  # means folder already existed to syncthing, but was not known to this ProjectManager
                        # basically we just do the same as in folder added event
                        event_add_shotpart(folder)

                else:
                    self.__log(5, 'folder config/metadata change for folder type %s is NOT IMPLEMENTED YET' % metadata['type'])
                    #TODO: implement this. more types to come

        elif isinstance(event, syncthinghandler.FoldersVolatileDataChangedEvent):  # TODO: treat this event better
            for folder in event.folders():
                if '__ProjectManager_data__' not in folder.metadata():
                    continue
                metadata = folder.metadata()['__ProjectManager_data__']
                if metadata['project'] != self.__project:
                    continue

                if metadata['type'] == 'shotpart':
                    if metadata['shotid'] not in self.__shots:
                        self.__log(4, "unknown shotid %s" % metadata['shotid'])
                        continue
                    for shotpartid, shotpart in self.__shots[metadata['shotid']].items():
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
                if metadata['type'] == 'shotpart':
                    if self.__shots.get(metadata['shotid'], {}).get(metadata['shotpartid'], None) is None:
                        self.__log(4, 'removed folder shotid shotpart does not exist! %s %s' % (metadata['shotid'], metadata['shotpartid']))
                        continue

                    if metadata['shotid'] not in self.__shots:
                        self.__shots[metadata['shotid']] = {}
                    del self.__shots[metadata['shotid']][metadata['shotpartid']]
                    if len(self.__shots[metadata['shotid']]) == 0:
                        del self.__shots[metadata['shotid']]
                    # no need to rescan config


    @classmethod
    def is_init_event(cls, event):
        raise RuntimeError("this should not be called! don't add this handler as autohandler to queueeater!")

    def is_expected_event(self, event):
        return isinstance(event, syncthinghandler.FoldersConfigurationEvent) or isinstance(event, syncthinghandler.ConfigSyncChangedEvent)
    # End Event processor methods

    def __rescanConfiguration(self, rescan_project_settings=True):
        try:
            folders = self.__sthandler.get_folders().result()  # type: Dict[str, syncthinghandler.Folder]
        except Exception as e:
            raise ConfigurationInconsistentError('syncthing returned %s' % repr(e))
        self.__log(1, 'rescanning configuration...')

        oldshots = self.__shots
        oldusers = self.__users
        oldprojectconfigfolder = self.__projectSettingsFolder
        self.__shots = {}
        if rescan_project_settings:
            self.__users = {}
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
                if rescan_project_settings and pm_metadata['type'] == 'server.configuration':
                    self.__projectSettingsFolder = folder
                elif pm_metadata['type'] == 'shotpart':
                    shotpart = ShotPart(folder)
                    #allshotparts[shotpart.id()] = shotpart
                    shotid = pm_metadata['shotid']
                    if shotid not in self.__shots:
                        self.__shots[shotid] = dict()
                    self.__shots[shotid][shotpart.id()] = shotpart

            if self.__projectSettingsFolder is None:
                # maybe syncthing handler is still in sync, so we will wait for it
                # so we wait for flodersaddedevent
                self.__log(2, 'project settings folder was not found! unsynced config?')
                pass
            else:
                if rescan_project_settings:
                    # load users
                    self.__log(1, 'project config found, loading users')
                    configpath = self.__projectSettingsFolder.path()
                    try:
                        with open(os.path.join(configpath, 'config.cfg'), 'r') as f:
                            config = json.load(f)
                    except:
                        self.__log(3, 'config loading error - might be not in sync, might be corrupted')
                        #self.__configInSync = False
                        return

                    users = config.get('users', {})
                    for userid, userdata in users.items():
                        try:
                            user = User(userdata)
                        except:  # malformed user data, skipping
                            continue
                        if userid != user.id():  # sanity check
                            continue
                        self.__users[user.id()] = user

                #assign users to shots
                for userid, user in self.__users.items():
                    for shotid, shotpartid in user.available_shotparts():
                        if shotid not in self.__shots:
                            self.__log(1, 'shotid %s is not part of this server' % shotid)
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

            with syncthinghandler.SyncthingHandler.ConfigMethodsBatch(self.__sthandler) as batch:
                self.__log(1, 'queing set devices')
                batch.set_devices(allDevids).set_retry_exception_types((syncthinghandler.ConfigNotInSyncError,))  # note that sthandler will only make changes if devlists do not match
                self.__log(1, ' set devices queued')

                for folderid, allShotpartDevids in folderidToDevidset.items():
                    batch.set_folder_devices(folderid, allShotpartDevids).set_retry_exception_types((syncthinghandler.ConfigNotInSyncError,))

        else:  # not server
            configchanged = self.__shots != oldshots

        self.__log(1, 'all done, config rescanned')
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
        add a shotid with one shotpart to this project
        :shotname: human readable shotid name for display
        :shotid: less human readable unique shotid identificator
        :shotpartid: name of the shotpart
        :path: path on file system to the shotid folder, local for the server, will not be sent to or from clients
        :return:
        """
        meta = dict()
        meta['__ProjectManager_data__'] = {'type': 'shotpart',
                                           'project': self.__project,
                                           'shotid': shotid,
                                           'shotpartid': shotpartid}
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

        with syncthinghandler.SyncthingHandler.ConfigMethodsBatch(self.__sthandler) as batch:
            for shotpart in self.__shots[shotid].values():
                batch.remove_folder(shotpart.stfolder_id())

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
    def rescan_configuration(self, rescan_project_settings=True):
        return self.__rescanConfiguration(rescan_project_settings)

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

        self.__log(1, 'adding user %s' % userid)
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
        # after this syncthing will not emit foldersync event, cuz changes are local
        self.__rescanConfiguration(rescan_project_settings=True)

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
        # after this syncthing will not emit foldersync event, cuz changes are local
        self.__rescanConfiguration(rescan_project_settings=True)

    @async_method()
    def add_devices_to_user(self, uiserid, devices: Union[str, Iterable[str]]):
        if isinstance(devices, str):
            devices = (devices,)
        if uiserid not in self.__users:
            raise RuntimeError('user %s does not exist' % uiserid)
        self.__log(1, 'adding devices %s to user %s' % (repr(devices), uiserid))

        for devid in devices:
            self.__users[uiserid].add_device(devid)

        configpath = self.__projectSettingsFolder.path()
        with open(os.path.join(configpath, 'config.cfg'), 'r') as f:
            config = json.load(f)

        config['users'][uiserid]['deviceids'] = list(self.__users[uiserid].device_ids())

        with open(os.path.join(configpath, 'config.cfg'), 'w') as f:
            json.dump(config, f)
        # after this syncthing will not emit foldersync event, cuz changes are local
        self.__rescanConfiguration(rescan_project_settings=True)

    @async_method()
    def remove_devices_from_user(self, uiserid, devices: Union[str, Iterable[str]]):
        if isinstance(devices, str):
            devices = (devices,)
        if uiserid not in self.__users:
            raise RuntimeError('user %s does not exist' % uiserid)
        self.__log(1, 'removing devices %s from user %s' % (repr(devices), uiserid))

        for devid in devices:
            self.__users[uiserid].remove_device(devid)

        configpath = self.__projectSettingsFolder.path()
        with open(os.path.join(configpath, 'config.cfg'), 'r') as f:
            config = json.load(f)

        config['users'][uiserid]['deviceids'] = list(self.__users[uiserid].device_ids())

        with open(os.path.join(configpath, 'config.cfg'), 'w') as f:
            json.dump(config, f)
        # after this syncthing will not emit foldersync event, cuz changes are local
        self.__rescanConfiguration(rescan_project_settings=True)

import os
import queue
import random
import string
from . import lance_utils
from . import logger

from .syncthinghandler import SyncthingHandler, ConfigSyncChangedEvent, FoldersSyncedEvent
from .eventqueueeater import EventQueueEater
from .eventprocessor import BaseEventProcessor
from .projectmanager import ProjectManager

from typing import Optional, List, Set, Dict

class Server(object):
    """
    The point of this class is to serve as a container for all the internals of the server.
    To connect and kickstart the routine
    """

    class ProjectManagerHandler(BaseEventProcessor):
        def __init__(self, server):
            super(Server.ProjectManagerHandler, self).__init__()
            self.__server = server
            self.__log = logger.get_logger('Project Manager Handler')

        @classmethod
        def is_init_event(cls, event):
            raise RuntimeError('this should never be called')

        def is_expected_event(self, event):
            """
            Should given event be
            :return: True/False
            Note that though you can dynamically change expected event types, since event processor and event supplier work in separate threads - you may miss events while changing states here
            So better enum here all the eveens types required for all the sates of your processor, unless you do not care to miss some events
            """
            return isinstance(event, FoldersSyncedEvent) or isinstance(event, ConfigSyncChangedEvent)

        def _processEvent(self, event):
            """
            this function will be invoked by THIS thread from the main loop to process new events
            this will be invoked every time expected event arrives
            override as u need
            :param event:
            :return:
            """
            if isinstance(event, FoldersSyncedEvent):
                # need to check if new projects appeared, and if so - create new project managers
                self.__log(0, 'folder synced event received')
                for folder in event.folders():
                    if folder.metadata().get('__ProjectManager_data__', {}).get('type', '') != 'server.configuration':
                        continue
                    possibleproject = folder.metadata().get('__ProjectManager_data__', {}).get('project', None)
                    if possibleproject is None or possibleproject in self.__server.projectManagers:
                        continue
                    self.__log(1, 'new project discovered! %s' % possibleproject)
                    newpm = ProjectManager(self.__server, possibleproject)  #, config_sync_status=self.__server.syncthingHandler.config_synced())
                    self.__server.projectManagers[possibleproject] = newpm
                    self.__log(1, 'starting new project manager')
                    newpm.start()
                    newpm.rescan_configuration().set_raise_on_invoke(True)

            elif isinstance(event, ConfigSyncChangedEvent):
                # need to check if new projects appeared, and if so - create new project managers
                self.__log(0, 'config synced event received')
                possibleprojects = ProjectManager.get_project_names(self.__server)
                for possibleproject in possibleprojects:
                    if possibleproject in self.__server.projectManagers:
                        continue
                    self.__log(1, 'new project discovered! %s' % possibleproject)
                    newpm = ProjectManager(self.__server, possibleproject)  #, config_sync_status=self.__server.syncthingHandler.config_synced())
                    self.__server.projectManagers[possibleproject] = newpm
                    self.__log(1, 'starting new project manager')
                    newpm.start()
                    newpm.rescan_configuration().set_raise_on_invoke(True)

    def __init__(self, config_root_path=None, data_root_path=None):
        super(Server, self).__init__()

        if config_root_path is None:
            config_root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), r'config')

        if data_root_path is None:
            data_root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), r'data')

        self.config = {'config_root': config_root_path, 'data_root': data_root_path}

        lance_utils.makedirs(self.config['data_root'])
        lance_utils.makedirs(self.config['config_root'])

        self.eventQueue = queue.Queue()
        self.syncthingHandler = SyncthingHandler(self)
        self.eventQueueEater = EventQueueEater(self)
        self._projectManagerHandler = Server.ProjectManagerHandler(self)
        self.eventQueueEater.add_event_processor(self._projectManagerHandler)
        self.projectManagers = {}  # type: Dict[str, ProjectManager]

    def start(self):
        self.eventQueueEater.start()
        self.syncthingHandler.start()

    def stop(self):
        self.eventQueueEater.stop()
        self._projectManagerHandler.stop()
        for pm in self.projectManagers.values():
            pm.stop()
        self.syncthingHandler.stop()

    def add_project(self, projectname):
        if projectname in self.projectManagers:
            raise ValueError('project with name %s already exists' % projectname)
        safename = "".join(c for c in projectname if c.isalnum() or c in ('.', '_'))
        fid = "folder-{project}-configuration-{randstr}".format(project=safename,  randstr=''.join(random.choice(string.ascii_lowercase) for _ in range(16)))
        prjmeta = {'__ProjectManager_data__': {'type': 'server.configuration',
                                               'project': projectname}}
        fold_path = os.path.join(self.config['data_root'], 'project_%s_configuration' % safename)
        os.makedirs(fold_path, exist_ok=True)
        with open(os.path.join(fold_path, 'config.cfg'), 'w') as f:
            f.write('{}')
        return self.syncthingHandler.add_folder(fold_path, 'project %s configuration' % projectname, devList=None, metadata=prjmeta, overrideFid=fid)


if __name__ == '__main__':
    srv = Server()

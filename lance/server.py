import os
import queue
from . import lance_utils
from . import logger

from .syncthinghandler import SyncthingHandler, ConfigSyncChangedEvent
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
            self.__knownProjects = set()
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
            return isinstance(event, ConfigSyncChangedEvent)

        def _processEvent(self, event):
            """
            this function will be invoked by THIS thread from the main loop to process new events
            this will be invoked every time expected event arrives
            override as u need
            :param event:
            :return:
            """
            if isinstance(event, ConfigSyncChangedEvent):
                # need to check if new projects appeared, and if so - create new project managers
                self.__log(0, 'config synced event received')
                possibleprojects = ProjectManager.get_project_names(self.__server)
                for possibleproject in possibleprojects:
                    if possibleproject in self.__knownProjects:
                        continue
                    self.__log(1, 'new project discovered! %s' % possibleproject)
                    newpm = ProjectManager(self.__server, possibleproject)
                    self.__server.projectManagers.append(newpm)
                    self.__log(1, 'starting new project manager')
                    newpm.start()
                    newpm.rescan_configuration()

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
        self.projectManagers = []  # type: List[ProjectManager]

    def start(self):
        self.eventQueueEater.start()
        self.syncthingHandler.start()
        self._projectManagerHandler.start()

    def stop(self):
        self._projectManagerHandler.stop()
        for pm in self.projectManagers:
            pm.stop()
        self.syncthingHandler.stop()
        self.eventQueueEater.stop()


if __name__ == '__main__':
    srv = Server()

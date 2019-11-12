import queue
from .servercomponent import ServerComponent
from . import lance_utils
from . import eventprocessor


class EventQueueEater(ServerComponent, lance_utils.EventQueueReader):
    def __init__(self, server):
        super(EventQueueEater, self).__init__(server)
        self.__eventProcessors = []
        self.__eventProcessorsRemoveQueue = queue.Queue()
        self.__eventProcessorsAddQueue = queue.Queue()

    def run(self):
        # note that there's no _processAsyncMethods cuz we do not have async methods
        # if this class gets any async methods - we'll have to implement double queue waiting...
        while not self._stopped_set():
            yield
            try:
                event = self._dequeueEvent(block=True, timeout=10)
            except queue.Empty:
                continue
            # process remove pending queue
            while self.__eventProcessorsRemoveQueue.qsize() > 0:
                try:
                    ep = self.__eventProcessorsRemoveQueue.get_nowait()
                    try:
                        self.__eventProcessors.remove(ep)
                    except ValueError:  # can happen that autocleaner already removed stopped thread
                        pass
                    self.__eventProcessorsRemoveQueue.task_done()
                except queue.Empty:  # can happen due to threading
                    pass

            # update existing processors
            for ep in self.__eventProcessors:
                if not ep.is_alive():
                    self.__eventProcessors.remove(ep)
                    continue
                if ep.is_expected_event(event):
                    ep.add_event(event)
            # create new processors
            while self.__eventProcessorsAddQueue.qsize() > 0:
                try:
                    #eventproc_type, event, data = self.__eventProcessorsAddQueue.get_nowait()
                    #newEventProcessor = eventproc_type(self, event, data)
                    newEventProcessor = self.__eventProcessorsAddQueue.get_nowait()
                    self.__eventProcessors.append(newEventProcessor)
                    self.__eventProcessorsAddQueue.task_done()
                    if not newEventProcessor.is_alive():
                        newEventProcessor.start()
                except queue.Empty:
                    pass
            try:
                nepr = eventprocessor.get_event_processor(self, event)
                for newEventProcessor in nepr:
                    self.__eventProcessors.append(newEventProcessor)
                    newEventProcessor.start()
            except Exception as e:
                print('something went wrong when creating event processor: %s' % repr(e))

            self._eventProcessed()

    def add_event_processor(self, eventprocessor):
        self.__eventProcessorsAddQueue.put(eventprocessor)

    def remove_event_provessor(self, eventprocessor):
        self.__eventProcessorsRemoveQueue.put(eventprocessor)

    def _event_processing_completed(self, eventprocessor): # TODO: get rid of this? or do we need it?
        self.__eventProcessorsRemoveQueue.put(eventprocessor)

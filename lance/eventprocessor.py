import queue
from . import lance_utils
from .lance_utils import async_method


class BadEventProcessorEvent(RuntimeError):
    def __init__(self, event=None):
        super(BadEventProcessorEvent, self).__init__(repr(event))
        self.event = event


class BaseEventProcessor(lance_utils.StoppableThread):
    """
    Base class for all event processors
    payload should be done in run
    """

    def __init__(self, invoker, event=None):
        super(BaseEventProcessor, self).__init__()
        # BaseEventProcessor.__processors[type(self).__name__] = type(self)
        self._invoker = invoker

    @async_method
    def add_event(self, event):
        """
        note that there is no dedicated event queue - allevents go into async methods queue
        :param event:
        :return:
        """
        if not self.is_expected_event(event):
            raise BadEventProcessorEvent(event)
        self._processEvent(event)

    def _report_done(self):
        """
        you must call this when your handler finishes.
        actually there's a failsafe measures that autoremove finished threads, but hey, why not still being polite?
        :return:
        """
        self.stop()
        self._invoker._event_processing_completed(self)

    # Override this!
    @classmethod
    def is_init_event(cls, event):
        """
        check passed event, return if it should cause new event of this class generation
        :return: True/False
        """
        return False

    # Override this!
    def is_expected_event(self, event):
        """
        Should given event be
        :return: True/False
        Note that though you can dynamically change expected event types, since event processor and event supplier work in separate threads - you may miss events while changing states here
        So better enum here all the eveens types required for all the sates of your processor, unless you do not care to miss some events
        """
        return False

    # Override this!
    def _processEvent(self, event):
        """
        this function will be invoked by THIS thread from the main loop to process new events
        this will be invoked every time expected event arrives
        override as u need
        dont forget self._report_done()
        :param event:
        :return:
        """
        raise NotImplementedError()

    # Override this!
    def _runLoopLoad(self):
        """
        this will be executed in the loop of this event's thread
        call self._report_done() if done, it will also stop the running thread
        """
        while True:
            yield


# TODO: this must be tied to a server, not dangling in a module!
__processors = {}


def register(eptype, *creationArgs, **creationKwargs):
    """
    use this function to regiester your class as event processor
    :param eptype: EventProcessor subclass
    :return:
    """
    __processors[eptype] = (creationArgs, creationKwargs)


def get_event_processor(invoker, event):
    return [x(invoker, event, *__processors[x][0], **__processors[x][1]) for x in __processors if x.is_init_event(event, *__processors[x][0], **__processors[x][1])]

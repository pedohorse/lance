import lance_utils
from lance_utils import async

class BaseEventProcessor(lance_utils.StoppableThread):
	'''
	Base class for all event processors
	'''
	@classmethod
	def init_event_types(cls):
		'''
		:return: list of string names of the events that this class processes
		'''
		return []

	def expected_event_types(self):
		'''
		:return: a list of event types to be passed to this processor after it's creation
		Note that though you can dynamically change expected event types, since event processor and event supplier work in separate threads - you may miss events while changing states here
		So better enum here all the eveens types required for all the sates of your processor, unless you do not care to miss some events
		'''
		return []

	def _processEvent(self, event):
		'''
		this function will be invoked by THIS thread from the main loop to process events
		override as u need
		:param event:
		:return:
		'''
		raise NotImplementedError()

	def __init__(self, invoker, event=None, data=None):
		super(BaseEventProcessor, self).__init__()
		BaseEventProcessor.__processors[type(self).__name__] = type(self)
		self._invoker = invoker

	@async
	def add_event(self, event):
		self._processEvent(event)

	def _report_done(self):
		'''
		you must call this when your handler finishes.
		actually there's a failsafe measures that autoremove finished threads, but hey, why not still being polite?
		:return:
		'''
		self._invoker._event_processing_completed(self)


__processors = {}


def register(eptype):
	for ttype in eptype.init_event_types():
		if ttype not in __processors:
			__processors[ttype] = []
		__processors[ttype].append(eptype)


def get_event_processor(invoker, event, data=None):
	return [x(invoker, event, data) for x in __processors.get(event['type'], [])]


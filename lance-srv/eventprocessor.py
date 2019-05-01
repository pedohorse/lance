import Queue
import lance_utils
from lance_utils import async

class BaseEventProcessor(lance_utils.StoppableThread):
	'''
	Base class for all event processors
	'''

	def __init__(self, invoker, event=None, data=None):
		super(BaseEventProcessor, self).__init__()
		BaseEventProcessor.__processors[type(self).__name__] = type(self)
		self._invoker = invoker

	@async
	def add_event(self, event):
		'''
		note that there is no dedicated event queue - allevents go into async methods queue
		:param event:
		:return:
		'''
		self._processEvent(event)

	def _report_done(self):
		'''
		you must call this when your handler finishes.
		actually there's a failsafe measures that autoremove finished threads, but hey, why not still being polite?
		:return:
		'''
		self._invoker._event_processing_completed(self)

	# Override this!
	@classmethod
	def is_init_event(cls, event, data=None):
		'''
		check passed event, return if it should cause new event of this class generation
		:return: True/False
		'''
		return False

	# Override this!
	def is_expected_event(self, event):
		'''
		Should given event be 
		:return: True/False
		Note that though you can dynamically change expected event types, since event processor and event supplier work in separate threads - you may miss events while changing states here
		So better enum here all the eveens types required for all the sates of your processor, unless you do not care to miss some events
		'''
		return False

	# Override this!
	def _processEvent(self, event):
		'''
		this function will be invoked by THIS thread from the main loop to process events
		override as u need
		dont forget self._report_done()
		:param event:
		:return:
		'''
		raise NotImplementedError()


__processors = set()


def register(eptype):
	__processors.add(eptype)


def get_event_processor(invoker, event, data=None):
	return [x(invoker, event, data) for x in __processors if x.is_init_event(event, data)]


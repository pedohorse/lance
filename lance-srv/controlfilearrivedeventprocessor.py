from eventprocessor import BaseEventProcessor,register

class ControlFileArrivedEventProcessor(BaseEventProcessor):
	@classmethod
	def is_init_event(cls, event, data=None):
		'''
		check passed event, return if it should cause new event of this class generation
		:return: True/False
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
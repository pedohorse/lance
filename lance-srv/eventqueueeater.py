import Queue
from servercomponent import ServerComponent
import lance_utils
import eventprocessor

class EventQueueEater(ServerComponent, lance_utils.EventQueueReader):
	def __init__(self, server):
		super(EventQueueEater, self).__init__(server)
		self.__eventProcessors = []
		self.__eventProcessorsRemoveQueue = Queue.Queue()
		self.__eventProcessorsAddQueue = Queue.Queue()
		self.__eventDefaultData = None

	def set_default_event_data(self, data):
		'''
		this data will be passed to all events created by init_event_types match condition
		:return:
		'''
		self.__eventDefaultData = data

	def run(self):
		# note that there's no _processAsyncMethods cuz we do not have async methods
		# if this class gets any async methods - we'll have to implement double queue waiting...
		while not self._stopped_set():
			try:
				event = self._dequeueEvent(block=True,timeout=10)
			except Queue.Empty:
				continue
			#process remove pending queue
			while self.__eventProcessorsRemoveQueue.qsize() > 0:
				try:
					ep = self.__eventProcessorsRemoveQueue.get_nowait()
					try:
						self.__eventProcessors.remove(ep)
					except ValueError: # can happen that autocleaner already removed stopped thread
						pass
					self.__eventProcessorsRemoveQueue.task_done()
				except Queue.Empty: # can happen due to threading
					pass

			#update existing processors
			for ep in self.__eventProcessors:
				if not ep.is_alive():
					self.__eventProcessors.remove(ep)
					continue
				if ep.is_expected_event(event):
					ep.add_event(event)
			#create new processors
			while self.__eventProcessorsAddQueue.qsize() > 0:
				try:
					eventproc_type, event, data = self.__eventProcessorsAddQueue.get_nowait()
					self.__eventProcessors.append(eventproc_type(self, event, data))
					self.__eventProcessorsAddQueue.task_done()
				except Queue.Empty:
					pass
			try:
				nepr = eventprocessor.get_event_processor(self, event, self.__eventDefaultData)
			except Exception as e:
				print('something went wrong when creating event processor: %s' % repr(e))
			self.__eventProcessors += nepr
			try:
				nepr.start()  # start event processing thread
			except Exception as e:
				print('something went wrong when starting new event processor: %s' % repr(e))

			self._eventProcessed()

	def add_event_processor(self, eventprocessor_type, event=None, data=None):
		self.__eventProcessorsAddQueue.put((eventprocessor_type, event, data))

	def remove_event_provessor(self, eventprocessor):
		self.__eventProcessorsRemoveQueue.put(eventprocessor)

	def _event_processing_completed(self, eventprocessor):
		self.__eventProcessorsRemoveQueue.put(eventprocessor)
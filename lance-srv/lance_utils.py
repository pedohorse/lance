import threading
import Queue
import os
import errno


def makedirs(path, mode=0o777):
	try:
		return os.makedirs(path, mode)
	except OSError as e:
		if e.errno != errno.EEXIST:
			raise


class EventQueueUser(object):
	def __init__(self, queue=None):
		super(EventQueueUser, self).__init__()
		self._eventQueue = queue

	def setEventQueue(self, queue):
		self._eventQueue = queue

	def eventQueue(self):
		return self._eventQueue


class EventQueueReader(EventQueueUser):
	def __init__(self, queue=None):
		super(EventQueueReader, self).__init__(queue)

	def _dequeueEvent(self, block=False, timeout=None):
		return self._eventQueue.get(block, timeout)

	def _eventProcessed(self):
		self._eventQueue.task_done()


class EventQueueWriter(EventQueueUser):
	def __init__(self, queue=None):
		super(EventQueueWriter, self).__init__(queue)

	def _enqueueEvent(self, event):
		self._eventQueue.put(event)


class SyncthingEventWatcher(EventQueueWriter):
	def __init__(self, queue, sthandler):
		super(SyncthingEventWatcher, self).__init__(queue)
		self._syncthing_handler = sthandler


class SyncthingError(RuntimeError):
	pass


def async(func):
	def wrapper(self, *args, **kwargs):
		asyncres = StoppableThread.AsyncResult()
		self._method_invoke_Queue.put((func, asyncres, args, kwargs))
		return asyncres
	return wrapper

class StoppableThread(threading.Thread):
	class ResultNotReady(RuntimeError):
		pass

	class AsyncResult(object):
		def __init__(self):
			self.__done = threading.Event()
			self.__result = None
			self.__exception = None

		# these are called from the worker thread
		def _setException(self, ex):
			self.__exception = ex
			self.__result = None  # should not be needed, but hey
			self.__done.set()

		def _setDone(self, result=None):
			self.__result = result
			self.__done.set()

		# these are called from invoker thread
		def wait(self, timeout=None):
			return self.__done.wait(timeout)

		def check(self):
			return self.__done.isSet()

		def result(self, timeout=None):
			if not self.wait(timeout):
				raise StoppableThread.ResultNotReady()
			if self.__exception is not None:
				raise self.__exception  # so this will be raised in invoker thread, so it can deal with exception. Exception in worker thread will be suppressed (though it means worker thread will still die)
			return self.__result

	def __init__(self):
		super(StoppableThread, self).__init__()
		self._method_invoke_Queue = Queue.Queue()
		self.__stopped_event = threading.Event()

	def stop(self):
		self.__stopped_event.set()

	def _stopped_set(self):
		return self.__stopped_event.is_set()

	def _processAsyncMethods(self, time_to_wait=0.25, max_events_to_invoke=None):
		i = 1 if max_events_to_invoke is None else max_events_to_invoke
		while self._method_invoke_Queue.qsize() and i > 0:
			try:
				cmd = self._method_invoke_Queue.get(True, time_to_wait)
				if max_events_to_invoke is not None:
					i -= 1
			except Queue.Empty:
				break
			try:
				cmd[1]._setDone(cmd[0](self, *cmd[2], **cmd[3]))
			except Exception as e:
				cmd[1]._setException(e)

	def run(self):
		'''
		default implementation that just processes async methods all the time
		do NOT call it from your child class!
		instead include self._processAsyncMethods in your own loop
		though generally you should not require to override this
		:return:
		'''
		while not self._stopped_set():
			try:
				cmd = self._method_invoke_Queue.get(True, 5)
			except Queue.Empty:
				continue
			try:
				cmd[1]._setDone(cmd[0](self, *cmd[2], **cmd[3]))
			except Exception as e:
				cmd[1]._setException(e)
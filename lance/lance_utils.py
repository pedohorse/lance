import threading
import queue
import os
import errno
import re
import datetime


def makedirs(path, mode=0o777):
    try:
        return os.makedirs(path, mode)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


class BaseEvent(object):
    def __init__(self):
        pass


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


def async_method(func):
    def wrapper(self, *args, **kwargs):
        asyncres = StoppableThread.AsyncResult()
        if self.isAlive():  # if self is a running thread - enqueue method for execution
            self._method_invoke_Queue.put((func, asyncres, args, kwargs))
        else:  # if self is not running - execute now
            try:
                asyncres._setDone(func(self, *args, **kwargs))
            except Exception as e:
                asyncres._setException(e)
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
            self.__callbackLock = threading.Lock()
            self.__callback = None

        # these are called from the worker thread
        def _setException(self, ex):
            self.__exception = ex
            self.__result = None  # should not be needed, but hey
            self.__done.set()

        def _setDone(self, result=None):
            with self.__callbackLock:
                self.__result = result
                self.__done.set()
                try:
                    if (self.__callback is not None):
                        self.__callback(self.__result)
                except:
                    pass

        # these are called from invoker thread
        def set_callback(self, callback):
            """
            note that callback will be called by worker thread!
            :param callback:
            :return:
            """
            with self.__callbackLock:
                self.__callback = callback
                # either we are before _setDone was called, or possibly after
                # if we are after - call the callback immediately
                if self.__callback is not None and self.__done.is_set():
                    self.__callback(self.__result)

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
        self._method_invoke_Queue = queue.Queue()
        self.__stopped_event = threading.Event()
        self._methodQueueBlockTime = 0.1

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
            except queue.Empty:
                break
            try:
                cmd[1]._setDone(cmd[0](self, *cmd[2], **cmd[3]))
            except Exception as e:
                cmd[1]._setException(e)

    def _runLoopLoad(self):
        """
        this function will be called by default implementation of run
        if your process is not complicated - you can only reimplement this function instead of run itself
        :return:
        """
        while True:
            yield

    def run(self):
        """
        default implementation that just processes async methods all the time
        do NOT call it from your child class!
        instead include self._processAsyncMethods in your own loop
        though generally you should not require to override this
        :return:
        """
        gen = self._runLoopLoad()
        while not self._stopped_set():
            try:
                next(gen)
            except StopIteration:
                break
            try:
                cmd = self._method_invoke_Queue.get(True, self._methodQueueBlockTime)
            except queue.Empty:
                continue
            try:
                cmd[1]._setDone(cmd[0](self, *cmd[2], **cmd[3]))
            except Exception as e:
                cmd[1]._setException(e)

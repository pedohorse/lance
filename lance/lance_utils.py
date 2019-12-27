import threading
import queue
import os
import errno
import re
import time

from typing import Iterable, Optional

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


def async_method(raise_while_invoking=False, queue_only=False):
    def inner_decor(func):
        def wrapper(self, *args, **kwargs):
            asyncres = StoppableThread.AsyncResult(raise_while_invoking)
            if queue_only or self.isAlive():  # if self is a running thread - enqueue method for execution
                with self._method_invoke_Queue_lock:
                    self._method_invoke_Queue.put((func, asyncres, args, kwargs))
            else:  # if self is not running - execute now
                try:    #TODO: make sure this can never be executed while object's constructor is being executed!
                    asyncres._setDone(func(self, *args, **kwargs))
                except Exception as e:
                    asyncres._setException(e)
            return asyncres
        wrapper._is_async_method = True
        return wrapper
    return inner_decor


class AsyncMethodBatch:
    """
    Instances of this class should NOT be accessed from different threads at the same time!
    """
    def __init__(self, thread: 'StoppableThread'):
        self._method_invoke_Queue = SimpleThreadsafeQueue()
        self._method_invoke_Queue_lock = threading.Lock()
        self.__thread = thread
        self.__doubleEnterPreventor = threading.Lock()

    def isAlive(self):
        # to force everyone into queue
        return True

    def __getattr__(self, item):
        """
        calling async methods of self.__thread with self as self instead
        so what we gather queue and them merge queues within a data lock
        :param item:
        :return:
        """
        meth = getattr(type(self.__thread), item)
        if not hasattr(meth, '_is_async_method'):
            raise AttributeError('cannot batch queue non async methods!')
        return meth.__get__(self, type(self))  # we replace self for other class'es async methods ONLY cuz we know exactly how they behave, and that this class implements enough to be a valid self-replacement for them

    def __enter__(self):
        self.__doubleEnterPreventor.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with self.__thread._method_invoke_Queue_lock:
            while self._method_invoke_Queue.qsize() > 0:  # we dont check for Empty expection in crap below only cuz we know WE are the only accessor to this queue due to the extra lock
                cmd = self._method_invoke_Queue.get()
                cmd[1].set_raise_on_invoke(True)
                self.__thread._method_invoke_Queue.put(cmd)
        self.__doubleEnterPreventor.release()


class SimpleThreadsafeQueue:
    class Empty(Exception):
        pass

    class Blocked(Exception):
        pass

    def __init__(self, baselist: Optional[Iterable] = None):
        if baselist is None:
            self.__baselist = []
        else:
            self.__baselist = list(baselist)
        self.__accesslock = threading.Lock()

    def __len__(self):
        with self.__accesslock:
            return len(self.__baselist)

    def qsize(self):
        """
        alias for __len__
        :return:
        """
        return len(self)

    def put(self, elem, block=True, timeout=None):
        if timeout is None:
            timeout = -1
        if self.__accesslock.acquire(block, timeout):
            try:
                self.__baselist.append(elem)
            finally:
                self.__accesslock.release()
        else:
            raise SimpleThreadsafeQueue.Blocked()

    def get(self, block=True, timeout=None):
        if timeout is None:
            timeout = -1
        if self.__accesslock.acquire(block, timeout):
            try:
                if len(self.__baselist) == 0:
                    raise SimpleThreadsafeQueue.Empty()
                return self.__baselist.pop(0)
            finally:
                self.__accesslock.release()
        else:
            raise SimpleThreadsafeQueue.Blocked()

    def put_back(self, elem, block=True, timeout=None):
        if timeout is None:
            timeout = -1
        if self.__accesslock.acquire(block, timeout):
            try:
                self.__baselist.insert(0, elem)
            finally:
                self.__accesslock.release()
        else:
            raise SimpleThreadsafeQueue.Blocked()


class StoppableThread(threading.Thread):
    class ResultNotReady(RuntimeError):
        pass

    class AsyncResult(object):
        def __init__(self, raise_straightaway=False, retry_exception_types=(), retry_wait_time=10):
            self.__done = threading.Event()
            self.__result = None
            self.__exception = None
            self.__callbackLock = threading.Lock()
            self.__callback = None
            self.__raiseImmediately = raise_straightaway
            self.__retry_exception_types = tuple(retry_exception_types)
            self.__retry_wait_time = retry_wait_time
            self.__first_retry_time = None

        # these are called from the worker thread
        def _setException(self, ex):
            """
            :param ex:
            :return: True if exception was set, False if this exception  shoud instead be considered a retry
            """
            if type(ex) in self.__retry_exception_types:
                if self.__first_retry_time is None:
                    self.__first_retry_time = time.time()
                if time.time() - self.__first_retry_time < self.__retry_wait_time:
                    return False
            self.__exception = ex
            self.__result = None  # should not be needed, but hey
            self.__done.set()
            if self.__raiseImmediately:
                raise ex
            return True

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
            :return: self (for chaining)
            """
            with self.__callbackLock:
                self.__callback = callback
                # either we are before _setDone was called, or possibly after
                # if we are after - call the callback immediately
                if self.__callback is not None and self.__done.is_set():
                    self.__callback(self.__result)
            return self

        def wait(self, timeout=None):
            return self.__done.wait(timeout)

        def check(self):
            return self.__done.isSet()

        def set_raise_on_invoke(self, do_raise):
            self.__raiseImmediately = do_raise
            return self

        def set_retry_exception_types(self, types):
            self.__retry_exception_types = tuple(types)
            return self

        def set_retry_timeout(self, timeout):
            self.__retry_wait_time = timeout
            return self

        def result(self, timeout=None):
            if not self.wait(timeout):
                raise StoppableThread.ResultNotReady()
            if self.__exception is not None:
                raise self.__exception  # so this will be raised in invoker thread, so it can deal with exception. Exception in worker thread will be suppressed (though it means worker thread will still die)
            return self.__result

    def __init__(self):
        super(StoppableThread, self).__init__()
        self._method_invoke_Queue = SimpleThreadsafeQueue()
        self._method_invoke_Queue_lock = threading.Lock()
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
            except SimpleThreadsafeQueue.Empty:
                break
            except SimpleThreadsafeQueue.Blocked:
                continue  # is this reasonable?
            try:
                cmd[1]._setDone(cmd[0](self, *cmd[2], **cmd[3]))
            except Exception as e:
                if not cmd[1]._setException(e):  # means we should retry
                    self._method_invoke_Queue.put_back(cmd)
                    return

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

            self._processAsyncMethods(time_to_wait=0.25, max_events_to_invoke=10)

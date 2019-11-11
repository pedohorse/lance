import datetime
import re
from .lance_utils import BaseEvent


class SyncthingEvent(BaseEvent):
    """
    basic syncthing event wrapper
    native event is accessible, but always present top level keys are accessible with dedicated methods.
    see: https://docs.syncthing.net/dev/events.html#event-structure
    """
    __compiled_time_regex = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{6})\d{3}([+-]\d+):(\d+)')
    __fixedkeys = ('id', 'globalID', 'time', 'data', 'type')

    def __init__(self, nativeEvent):
        super(BaseEvent, self).__init__()
        self.__nativeEvent = nativeEvent
        for fixedkey in SyncthingEvent.__fixedkeys:
            if fixedkey not in self.__nativeEvent:
                raise RuntimeError('native event lacks fixed key %d' % fixedkey)

    def time(self):
        return datetime.datetime.strptime(''.join(SyncthingEvent.__compiled_time_regex.match(self.__nativeEvent['time']).groups()), '%Y-%m-%dT%H:%M:%S.%f%z')

    def __getattr__(self, item):
        if item in SyncthingEvent.__fixedkeys:
            return lambda: self.__nativeEvent[item]  # callable object
        raise AttributeError()

    def getNativeEvent(self):
        return self.__nativeEvent


class ControlEvent(SyncthingEvent):
    def __init__(self, nativeEvent):
        super(ControlEvent, self).__init__(nativeEvent)


class FolderStatusEvent(SyncthingEvent):
    pass



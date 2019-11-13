from .rc import detailViewer_ui

import lance.syncthinghandler as sth
import lance.server as lserver
import lance.eventprocessor as levent
from PySide2.QtCore import QObject, QAbstractTableModel, QModelIndex, Qt, Signal, Slot
from PySide2.QtWidgets import QWidget, QMainWindow
from typing import Dict, Optional, List


class DeviceEventCatcher(levent.BaseEventProcessorInterface, QObject):
    event_arrived = Signal(object)
    _intermediete_event_arrived = Signal(object)

    def __init__(self):
        super(DeviceEventCatcher, self).__init__()
        self._intermediete_event_arrived.connect(self._intermediete_event_arrived_slot, Qt.QueuedConnection)

    @classmethod
    def is_init_event(cls, event):
        """
        WILL BE INVOKED BY QUEUE THREAD
        """
        return False

    def add_event(self, event):
        """
        WILL BE INVOKED BY QUEUE THREAD
        """
        self._intermediete_event_arrived.emit(event)

    def is_expected_event(self, event):
        """
        WILL BE INVOKED BY QUEUE THREAD
        """
        print("WOOOOOOOO %s" % event)
        return isinstance(event, sth.DevicesConfigurationEvent)

    @Slot(object)
    def _intermediete_event_arrived_slot(self, event):
        """
         now this is supposed to be called from main QT thread
        """
        self.event_arrived.emit(event)


# DATA MODEL
class DeviceModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super(DeviceModel, self).__init__(parent)
        self.__devices = {}  # type: Dict[str, sth.Device]
        self.__keyorder = []  # type: List[str]

    def rowCount(self, parentindex=None) -> int:
        if parentindex is None or not parentindex.isValid():
            return len(self.__keyorder)
        data = parentindex.internalPointer()
        if isinstance(data, sth.Device):
            return 2 + len(data.volatile_data())
        return 0

    def columnCount(self, parentindex=None) -> int:
        return 2

    def index(self, row: int, column: int, parentindex=None) -> QModelIndex:
        if parentindex is None or not parentindex.isValid():
            return self.createIndex(row, column, self.__devices[self.__keyorder[row]])
        data = parentindex.internalPointer()
        if isinstance(data, sth.Device):
            if row == 0:
                idxdata = data.id()
            elif row == 1:
                idxdata = data.name()
            else:
                idxdata = data.volatile_data().get(data.volatile_data().keys()[row - 2])
            return self.createIndex(row, column, idxdata)
        return QModelIndex()

    def data(self, index, role=None):
        if index is None or not index.isValid():
            return None
        if role != Qt.DisplayRole:
            return None
        data = index.internalPointer()
        if isinstance(data, sth.Device):
            if index.column() == 0:
                return data.id()
        else:
            return data
        return None


class DetailViewer(QMainWindow):
    def __init__(self, parent=None):
        super(DetailViewer, self).__init__(parent)

        self.ui = detailViewer_ui.Ui_MainWindow()
        self.ui.setupUi(self)

        self.__deviceModel = DeviceModel(self)
        self.ui.deviceTreeView.setModel(self.__deviceModel)

        self.__eventCatched = DeviceEventCatcher()
        self.__eventCatched.event_arrived.connect(self.process_event)

        self.__server = None

    def set_server(self, server: lserver.Server):
        if self.__server is not None:
            self.__server.eventQueueEater.remove_event_provessor(self.__eventCatched)
        self.__server = server
        if self.__server is not None:
            self.__server.eventQueueEater.add_event_processor(self.__eventCatched)

    @Slot(object)
    def process_event(self, event):
        print('all is good', event)

from .rc import detailViewer_ui

import lance.syncthinghandler as sth
import lance.server as lserver
from PySide2.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide2.QtWidgets import QWidget
from typing import Dict, Optional, List


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


class DetailViewer(QWidget):
    def __init__(self, parent=None):
        super(DetailViewer, self).__init__(parent)

        self.ui = detailViewer_ui.Ui_MainWindow()
        self.ui.setupUi(self)

        self.__deviceModel = DeviceModel(self)
        self.ui.deviceTreeView.setModel(self.__deviceModel)

        self.__server = None

    def set_server(self, server: lserver.Server):
        if self.__server is not None:
            pass
        self.__server = server
        if self.__server is not None:
            self.__server.eventQueueEater.add_event_processor()

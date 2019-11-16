from .rc import detailViewer_ui

import lance.syncthinghandler as sth
import lance.server as lserver
import lance.eventprocessor as levent
from PySide2.QtCore import QObject, QAbstractItemModel, QAbstractTableModel, QModelIndex, Qt, Signal, Slot
from PySide2.QtWidgets import QWidget, QMainWindow
from typing import Dict, Optional, List


class ServerEventCatcher(levent.BaseEventProcessorInterface, QObject):
    event_arrived = Signal(object)
    _intermediete_event_arrived = Signal(object)

    def __init__(self, event_type_list: Iterable):
        super(ServerEventCatcher, self).__init__()
        self.__event_types = tuple(event_type_list)
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
        for eventtype in self.__event_types:
            if isinstance(event, eventtype):
                return True
        return False

    def _intermediete_event_arrived_slot(self, event):
        """
         now this is supposed to be called from main QT thread
        """
        self.event_arrived.emit(event)


# DATA MODEL
class DeviceModel(QAbstractItemModel):
    class TreeElement:
        def __init__(self, data, parent=None):
            self.__children = []
            self.__parent = None
            self.setParent(parent)
            self.__data = data

        def data(self):
            return self.__data

        def setData(self, data):
            self.__data = data

        def parent(self):
            return self.__parent

        def children(self):
            """
            warning: mutable! AND we use that, since its a local helper class
            :return:
            """
            return self.__children

        def addChild(self, child):
            child.setParent(self)

        def removeChild(self, child):
            child.setParent(None)

        def clearChildren(self):
            for child in self.__children[:]:
                self.removeChild(child)

        def setParent(self, parent):
            if self.__parent == parent:
                return
            if self.__parent is not None:
                self.__parent.__children.remove(self)
            self.__parent = parent
            if parent is not None:
                parent.__children.append(self)  # it can not be there already cuz we ensure parent-child consistency here

    def __init__(self, parent=None):
        super(DeviceModel, self).__init__(parent)
        self.__rootitem = DeviceModel.TreeElement(None)
        #self.__devices = {}  # type: Dict[str, sth.Device]
        #self.__keyorder = []  # type: List[str]
        #self.__cache = {}  # This exists baiscally purely to prevent GC from freeing objects still tracked by QT

    def add_device(self, device: sth.Device) -> None:
        """
        TODO: is device volatile? for now assume it is, but not rely on it
        TODO: IT MUST NOT BE VOLATILE (CONNECTED TO SERVER), OR WE MEET RACE CONDITIONS!!
        :param device:
        :return:
        """
        if len([x for x in self.__rootitem.children() if x.data() == device]):
            return
        self.beginInsertRows(QModelIndex(), len(self.__rootitem.children()), len(self.__rootitem.children()))
        devitem = DeviceModel.TreeElement(device)
        self.__rootitem.addChild(devitem)
        devitem.addChild(DeviceModel.TreeElement(('id', device.id())))
        devitem.addChild(DeviceModel.TreeElement(('name', device.name())))
        for key in device.volatile_data().keys():
            devitem.addChild(DeviceModel.TreeElement((key, device.volatile_data().get(key))))
        self.endInsertRows()

    def remove_device(self, device):
        todel = []
        for i, x in enumerate(self.__rootitem.children()):
            if x.data() == device:
                todel.insert(0, i)
        for i in todel:
            self.beginRemoveRows(QModelIndex(), i, i)
            del self.__rootitem.children()[i]
            self.endRemoveRows()

    def clear_devices(self):
        self.beginResetModel()
        self.__rootitem = DeviceModel.TreeElement(None)
        self.endResetModel()

    def update_device(self, device: sth.Device):
        """
        :param device: a device with id already in model, all other data except id will be updated
        :return:
        """
        items = [x for x in self.__rootitem.children() if x.data() == device]
        for item in items:
            dmodelindex = self.index(self.__rootitem.children().index(item), 0, QModelIndex())  # yes i could have got that row together with items list, but i'm not in a hurry here

            self.beginRemoveRows(dmodelindex, 0, len(item.children())-1)
            item.setData(device)
            item.clearChildren()
            self.endRemoveRows()

            self.beginInsertRows(dmodelindex, 0, 2 + len(device.volatile_data().keys()))
            item.addChild(DeviceModel.TreeElement(('id', device.id())))
            item.addChild(DeviceModel.TreeElement(('name', device.name())))
            for key in device.volatile_data().keys():
                item.addChild(DeviceModel.TreeElement((key, device.volatile_data().get(key))))
            self.endInsertRows()

    #QAbstractItemMode stuff
    def rowCount(self, parentindex) -> int:
        if not parentindex.isValid():
            item = self.__rootitem
        else:
            item = parentindex.internalPointer()  # type: DeviceModel.TreeElement
        return len(item.children())

    def columnCount(self, parentindex: QModelIndex) -> int:
        return 2

    def index(self, row: int, column: int, parentindex: QModelIndex) -> QModelIndex:
        if not parentindex.isValid():
            item = self.__rootitem
        else:
            item = parentindex.internalPointer()  # type: DeviceModel.TreeElement
        return self.createIndex(row, column, item.children()[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        item = index.internalPointer()  # type: DeviceModel.TreeElement
        if item.parent() == self.__rootitem:
            return QModelIndex()
        return self.createIndex(item.parent().parent().children().index(item.parent()), 0, item.parent())

    def data(self, index, role=None):
        if not index.isValid():
            return None
        if role != Qt.DisplayRole:
            return None
        item = index.internalPointer()  # type: DeviceModel.TreeElement
        data = item.data()
        col = index.column()
        if isinstance(data, sth.Device):
            if col == 0:
                return data.name()
            else:
                return data.id()
        # otherwise data is Tuple[str, SomeShit]
        key, val = data
        if col == 0:
            return key
        return val



class DetailViewer(QMainWindow):
    def __init__(self, parent=None):
        super(DetailViewer, self).__init__(parent)

        self.ui = detailViewer_ui.Ui_MainWindow()
        self.ui.setupUi(self)

        self.__deviceModel = DeviceModel(self)
        self.ui.deviceTreeView.setModel(self.__deviceModel)

        self.__eventCatched = ServerEventCatcher([sth.DevicesConfigurationEvent])
        self.__eventCatched.event_arrived.connect(self.process_event)

        self.__server = None

    def set_server(self, server: lserver.Server):
        if self.__server is not None:
            self.__server.eventQueueEater.remove_event_provessor(self.__eventCatched)
            self.__deviceModel.clear_devices()
        self.__server = server
        if self.__server is not None:
            for devid, device in self.__server.syncthingHandler.get_devices().result().items():
                self.__deviceModel.add_device(device)
            self.__server.eventQueueEater.add_event_processor(self.__eventCatched)

    @Slot(object)
    def process_event(self, event):
        """
        make sure to
        :param event:
        :return:
        """
        print('all is good', event)
        if isinstance(event, sth.DevicesAddedEvent):
            for device in event.devices():
                self.__deviceModel.add_device(device)
        elif isinstance(event, sth.DevicesRemovedEvent):
            for device in event.devices():
                self.__deviceModel.remove_device(device)
        elif isinstance(event, sth.DevicesChangedEvent) or isinstance(event, sth.DevicesVolatileDataChangedEvent):
            for device in event.devices():
                self.__deviceModel.update_device(device)

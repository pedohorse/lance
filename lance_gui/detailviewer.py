from .rc import detailViewer_ui

import lance.syncthinghandler as sth
import lance.server as lserver
import lance.eventprocessor as levent
from PySide2.QtCore import QObject, QAbstractItemModel, QMutex, QModelIndex, Qt, Signal, Slot
from PySide2.QtWidgets import QWidget, QMainWindow
from typing import Dict, Optional, List, Iterable, Union


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
class DeviceFolderModel_Base(QAbstractItemModel):
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

    def __init__(self, elemkeymethod: Optional[str], elemvalmethod: Optional[str] = None, mainkeys: Optional[List[str]] = None, parent=None):
        super(DeviceFolderModel_Base, self).__init__(parent)
        self.__rootitem = DeviceFolderModel_Base.TreeElement(None)
        if mainkeys is None:
            mainkeys = []
        self.__mainkeys = mainkeys
        self.__elemkeymethod = elemkeymethod
        self.__elemvalmethod = elemvalmethod

    def _createItem(self, parent: 'DeviceFolderModel_Base.TreeElement', key: str, val):
        if isinstance(val, set) or isinstance(val, tuple) or isinstance(val, list):
            item = DeviceFolderModel_Base.TreeElement((key, ''))
            parent.addChild(item)
            for subval in val:
                self._createItem(item, '', subval)
        elif isinstance(val, dict):
            item = DeviceFolderModel_Base.TreeElement((key, ''))
            parent.addChild(item)
            for subkey, subval in val.items():
                self._createItem(item, subkey, subval)
        else:
            parent.addChild(DeviceFolderModel_Base.TreeElement((key, val)))

    def add_element(self, element: Union[sth.Device, sth.Folder]) -> None:
        """
        TODO: is device volatile? for now assume it is, but not rely on it
        TODO: IT MUST NOT BE VOLATILE (CONNECTED TO SERVER), OR WE MEET RACE CONDITIONS!!
        :param element:
        :return:
        """
        if len([x for x in self.__rootitem.children() if x.data().id() == element.id()]):
            return
        self.beginInsertRows(QModelIndex(), len(self.__rootitem.children()), len(self.__rootitem.children()))
        devitem = DeviceFolderModel_Base.TreeElement(element)
        self.__rootitem.addChild(devitem)
        for key in self.__mainkeys:
            self._createItem(devitem, key, getattr(element, key)())
            #devitem.addChild(DeviceFolderModel_Base.TreeElement((key, getattr(element, key)())))
        for key in element.volatile_data().keys():
            self._createItem(devitem, key, element.volatile_data().get(key))
            #devitem.addChild(DeviceFolderModel_Base.TreeElement((key, element.volatile_data().get(key))))
        self.endInsertRows()

    def remove_element(self, element: Union[sth.Device, sth.Folder]):
        todel = []
        for i, x in enumerate(self.__rootitem.children()):
            if x.data().id() == element.id():
                todel.insert(0, i)
        for i in todel:
            self.beginRemoveRows(QModelIndex(), i, i)
            del self.__rootitem.children()[i]
            self.endRemoveRows()

    def clear_elements(self):
        self.beginResetModel()
        self.__rootitem = DeviceFolderModel_Base.TreeElement(None)
        self.endResetModel()

    def update_element(self, element: Union[sth.Device, sth.Folder]):
        """
        :param element: a device with id already in model, all other data except id will be updated
        :return:
        """
        items = [x for x in self.__rootitem.children() if x.data().id() == element.id()]
        for item in items:
            dmodelindex = self.index(self.__rootitem.children().index(item), 0, QModelIndex())  # yes i could have got that row together with items list, but i'm not in a hurry here

            self.beginRemoveRows(dmodelindex, 0, len(item.children())-1)
            item.setData(element)
            item.clearChildren()
            self.endRemoveRows()

            self.beginInsertRows(dmodelindex, 0, len(self.__mainkeys) + len(element.volatile_data().keys()))
            for key in self.__mainkeys:
                self._createItem(item, key, getattr(element, key)())
                #item.addChild(DeviceFolderModel_Base.TreeElement((key, getattr(element, key)())))
            for key in element.volatile_data().keys():
                self._createItem(item, key, element.volatile_data().get(key))
                #item.addChild(DeviceFolderModel_Base.TreeElement((key, element.volatile_data().get(key))))
            self.endInsertRows()

    #QAbstractItemMode stuff
    def rowCount(self, parentindex) -> int:
        if not parentindex.isValid():
            item = self.__rootitem
        else:
            item = parentindex.internalPointer()  # type: DeviceFolderModel_Base.TreeElement
        return len(item.children())

    def columnCount(self, parentindex: QModelIndex) -> int:
        return 2

    def index(self, row: int, column: int, parentindex: QModelIndex) -> QModelIndex:
        if not parentindex.isValid():
            item = self.__rootitem
        else:
            item = parentindex.internalPointer()  # type: DeviceFolderModel_Base.TreeElement
        return self.createIndex(row, column, item.children()[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        item = index.internalPointer()  # type: DeviceFolderModel_Base.TreeElement
        if item.parent() == self.__rootitem:
            return QModelIndex()
        return self.createIndex(item.parent().parent().children().index(item.parent()), 0, item.parent())

    def data(self, index, role=None):
        if not index.isValid():
            return None
        if role != Qt.DisplayRole:
            return None
        item = index.internalPointer()  # type: DeviceFolderModel_Base.TreeElement
        data = item.data()
        col = index.column()
        if item.parent() == self.__rootitem:
            if col == 0 and self.__elemkeymethod is not None:
                return getattr(data, self.__elemkeymethod)()
            elif col == 1 and self.__elemvalmethod is not None:
                return getattr(data, self.__elemvalmethod)()
            return None
        # otherwise data is Tuple[str, SomeShit]
        key, val = data
        if col == 0:
            return key
        return val


class DeviceModel(DeviceFolderModel_Base):
    def __init__(self, parent=None):
        super(DeviceModel, self).__init__('name', None, ['name', 'id'], parent=parent)


class FolderModel(DeviceFolderModel_Base):
    def __init__(self, parent=None):
        super(FolderModel, self).__init__('label', None, ['label', 'is_synced', 'path', 'id', 'devices'], parent=parent)


class DetailViewer(QMainWindow):
    def __init__(self, parent=None):
        super(DetailViewer, self).__init__(parent)

        self.ui = detailViewer_ui.Ui_MainWindow()
        self.ui.setupUi(self)

        self.__deviceModel = DeviceModel(self)
        self.__folderModel = FolderModel(self)
        self.ui.deviceTreeView.setModel(self.__deviceModel)
        self.ui.folderTreeView.setModel(self.__folderModel)

        self.__deviceEventCatched = ServerEventCatcher([sth.DevicesConfigurationEvent])
        self.__deviceEventCatched.event_arrived.connect(self.process_event)
        self.__folderEventCatched = ServerEventCatcher([sth.FoldersConfigurationEvent])
        self.__folderEventCatched.event_arrived.connect(self.process_event)

        self.__setterMutex = QMutex()
        self.__server = None

    def set_server(self, server: lserver.Server):
        self.__setterMutex.lock()
        try:
            if self.__server is not None:
                self.__server.eventQueueEater.remove_event_provessor(self.__deviceEventCatched)
                self.__server.eventQueueEater.remove_event_provessor(self.__folderEventCatched)
                self.__deviceModel.clear_elements()
            self.__server = server
            if self.__server is not None:
                for devid, device in self.__server.syncthingHandler.get_devices().result().items():
                    self.__deviceModel.add_element(device)
                for fid, folder in self.__server.syncthingHandler.get_folders().result().items():
                    self.__folderModel.add_element(folder)
                self.__server.eventQueueEater.add_event_processor(self.__deviceEventCatched)
                self.__server.eventQueueEater.add_event_processor(self.__folderEventCatched)
        finally:
            self.__setterMutex.unlock()

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
                self.__deviceModel.add_element(device)
        elif isinstance(event, sth.DevicesRemovedEvent):
            for device in event.devices():
                self.__deviceModel.remove_element(device)
        elif isinstance(event, sth.DevicesChangedEvent) or isinstance(event, sth.DevicesVolatileDataChangedEvent):
            for device in event.devices():
                self.__deviceModel.update_element(device)

        elif isinstance(event, sth.FoldersAddedEvent):
            for folder in event.folders():
                self.__folderModel.add_element(folder)
        elif isinstance(event, sth.FoldersRemovedEvent):
            for folder in event.folders():
                self.__folderModel.remove_element(folder)
        elif isinstance(event, sth.FoldersChangedEvent) or isinstance(event, sth.FoldersVolatileDataChangedEvent):
            for folder in event.folders():
                self.__folderModel.update_element(folder)

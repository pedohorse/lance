# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'rc/detailViewer.ui',
# licensing of 'rc/detailViewer.ui' applies.
#
# Created: Sun Nov 10 20:47:21 2019
#      by: pyside2-uic  running on PySide2 5.13.2
#
# WARNING! All changes made in this file will be lost!

from PySide2 import QtCore, QtGui, QtWidgets

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(800, 600)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")
        self.verticalLayout = QtWidgets.QVBoxLayout(self.centralwidget)
        self.verticalLayout.setObjectName("verticalLayout")
        self.treeViewsLayout = QtWidgets.QHBoxLayout()
        self.treeViewsLayout.setObjectName("treeViewsLayout")
        self.deviceTreeView = QtWidgets.QTreeView(self.centralwidget)
        self.deviceTreeView.setObjectName("deviceTreeView")
        self.treeViewsLayout.addWidget(self.deviceTreeView)
        self.folderTreeView = QtWidgets.QTreeView(self.centralwidget)
        self.folderTreeView.setObjectName("folderTreeView")
        self.treeViewsLayout.addWidget(self.folderTreeView)
        self.verticalLayout.addLayout(self.treeViewsLayout)
        spacerItem = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.verticalLayout.addItem(spacerItem)
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 800, 26))
        self.menubar.setObjectName("menubar")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        self.statusbar.setObjectName("statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QtWidgets.QApplication.translate("MainWindow", "MainWindow", None, -1))


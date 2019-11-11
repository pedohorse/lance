import sys
from PySide2.QtWidgets import QApplication

from lance_gui.detailviewer import DetailViewer

if __name__ == '__main__':
    qapp = QApplication(sys.argv)
    wgt = DetailViewer()
    wgt.show()
    sys.exit(qapp.exec_())

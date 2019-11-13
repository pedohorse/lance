import sys
from PySide2.QtWidgets import QApplication

from lance.server import Server
from lance_gui.detailviewer import DetailViewer

if __name__ == '__main__':
    qapp = QApplication(sys.argv)
    wgt = DetailViewer()
    srv = Server('/tmp/lancetemp/cfg', '/tmp/lancetemp/data')
    wgt.set_server(srv)
    srv.start()
    wgt.show()
    res = qapp.exec_()
    srv.stop()
    sys.exit(res)

import sys
import os
import errno
import time
import random
import string
import threading

from lance.server import Server
from testbase import TestBase
from lance_gui.detailviewer import DetailViewer

from PySide2.QtWidgets import QApplication


class SH_AddRemoveFoldersTest_gui(TestBase):
    def testBody(self, logger):
        self.error = None
        self.qapp = QApplication(sys.argv)
        self.wgt0 = DetailViewer()
        self.wgt0.show()

        testthread = threading.Thread(target=self.runthread)
        testthread.start()
        self.qapp.exec_()
        if self.error is not None:
            raise self.error

    def runthread(self):
        try:

            self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
            self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
            self.cl1 = Server(os.path.join(self.test_root_path(), 'cl1', 'config'), os.path.join(self.test_root_path(), 'cl1', 'data'))
            self.cl2 = Server(os.path.join(self.test_root_path(), 'cl2', 'config'), os.path.join(self.test_root_path(), 'cl2', 'data'))

            srvid = self.srv.syncthingHandler.myId()
            cl0id = self.cl0.syncthingHandler.myId()
            cl1id = self.cl1.syncthingHandler.myId()
            cl2id = self.cl2.syncthingHandler.myId()

            fpath0s = os.path.join(self.test_root_path(), 'folder0_s')
            fpath1s = os.path.join(self.test_root_path(), 'folder1_s')
            fpath2s = os.path.join(self.test_root_path(), 'folder2_s')
            for fpath in (fpath0s, fpath1s, fpath2s):
                try:
                    os.makedirs(fpath)
                except OSError as e:
                    if e.errno != errno.EEXIST:
                        raise

            self.cl0.syncthingHandler.set_server_secret('wowsecret2').result()
            self.cl1.syncthingHandler.set_server_secret('wowsecret2').result()
            self.cl2.syncthingHandler.set_server_secret('wowsecret2').result()
            self.srv.syncthingHandler.set_server_secret('wowsecret2').result()

            self.srv.syncthingHandler.add_server(srvid).result()
            self.srv.syncthingHandler.add_device(cl0id).result()
            self.srv.syncthingHandler.add_device(cl1id).result()
            self.srv.syncthingHandler.add_device(cl2id).result()
            ###
            self.cl0.syncthingHandler.add_server(srvid).result()
            self.cl1.syncthingHandler.add_server(srvid).result()
            self.cl2.syncthingHandler.add_server(srvid).result()

            fidA = self.srv.syncthingHandler.add_folder(fpath0s, 'folder_A', [cl0id, cl1id]).result()
            fidB = self.srv.syncthingHandler.add_folder(fpath1s, 'folder_B', [cl1id, cl2id]).result()
            fidC = self.srv.syncthingHandler.add_folder(fpath2s, 'folder_C', [cl2id, cl0id]).result()

            for i in range(3):
                self.wgt0.set_server(self.cl0)  # This particular part might be not quite threadsafe,
                self.srv.start()
                self.cl0.start()
                self.cl1.start()
                self.cl2.start()

                time.sleep(10)
                s0 = 'f0 random information %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                s1 = 'f1 randomer informationes %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                s2 = 'f2 even randomer informationes %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                with open(os.path.join(fpath0s, 'f0_test.txt'), 'w') as f:
                    f.write(s0)
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'w') as f:
                    f.write(s1)
                with open(os.path.join(fpath2s, 'f2_test.txt'), 'w') as f:
                    f.write(s2)

                time.sleep(15)

                with open(os.path.join(fpath0s, 'f0_test.txt'), 'r') as f:
                    ss0 = f.read()
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'r') as f:
                    ss1 = f.read()
                with open(os.path.join(fpath2s, 'f2_test.txt'), 'r') as f:
                    ss2 = f.read()

                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c1 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c1 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c2 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c2 folder 2 contents mismatch. test failed. \n%s' % s2
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c0 folder 2 contents mismatch. test failed. \n%s' % s2

                self.srv.syncthingHandler.remove_device_from_folder(fidA, cl1id).result()
                self.srv.syncthingHandler.remove_device_from_folder(fidB, cl2id).result()
                self.srv.syncthingHandler.remove_device_from_folder(fidC, cl0id).result()

                self.srv.syncthingHandler.add_device_to_folder(fidA, cl2id).result()
                self.srv.syncthingHandler.add_device_to_folder(fidB, cl0id).result()
                self.srv.syncthingHandler.add_device_to_folder(fidC, cl1id).result()

                time.sleep(15)

                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_A')), 'FolderA not deleted from cl1'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_B')), 'FolderB not deleted from cl2'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_C')), 'FolderC not deleted from cl0'

                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c2 folder 0 contents mismatch. test failed. \n%s' % s0

                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c1 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c0 folder 1 contents mismatch. test failed. \n%s' % s1

                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c2 folder 2 contents mismatch. test failed. \n%s' % s2
                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c1 folder 2 contents mismatch. test failed. \n%s' % s2

                self.srv.syncthingHandler.remove_device_from_folder(fidA, cl0id).result()
                self.srv.syncthingHandler.remove_device_from_folder(fidB, cl1id).result()
                self.srv.syncthingHandler.remove_device_from_folder(fidC, cl2id).result()

                self.srv.syncthingHandler.add_device_to_folder(fidA, cl1id).result()
                self.srv.syncthingHandler.add_device_to_folder(fidB, cl2id).result()
                self.srv.syncthingHandler.add_device_to_folder(fidC, cl0id).result()

                time.sleep(15)

                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_A')), 'FolderA not deleted from cl0'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_B')), 'FolderB not deleted from cl1'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_C')), 'FolderC not deleted from cl2'

                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c1 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c2 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c2 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c0 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c0 folder 2 contents mismatch. test failed. \n%s' % s2
                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c1 folder 2 contents mismatch. test failed. \n%s' % s2

                self.cl0.stop()
                self.cl1.stop()
                self.cl2.stop()
                self.srv.stop()

                print("EVERYTHING STOPPED! ITERATION %d DONE" % i)

                self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl1 = Server(os.path.join(self.test_root_path(), 'cl1', 'config'), os.path.join(self.test_root_path(), 'cl1', 'data'))
                self.cl2 = Server(os.path.join(self.test_root_path(), 'cl2', 'config'), os.path.join(self.test_root_path(), 'cl2', 'data'))

                self.srv.syncthingHandler.remove_device_from_folder(fidA, cl2id).result()
                self.srv.syncthingHandler.remove_device_from_folder(fidB, cl0id).result()
                self.srv.syncthingHandler.remove_device_from_folder(fidC, cl1id).result()

                self.srv.syncthingHandler.add_device_to_folder(fidA, cl0id).result()
                self.srv.syncthingHandler.add_device_to_folder(fidB, cl1id).result()
                self.srv.syncthingHandler.add_device_to_folder(fidC, cl2id).result()
        except Exception as e:
            self.error = e
            self.qapp.quit()
        finally:
            if isinstance(self.wgt0, DetailViewer):
                self.wgt0.close()
            try:
                self.cl0.stop()
            except:
                pass
            try:
                self.cl1.stop()
            except:
                pass
            try:
                self.cl2.stop()
            except:
                pass
            try:
                self.srv.stop()
            except:
                pass

    def __del__(self):
        try:
            self.cl0.stop()
        except:
            pass
        try:
            self.cl1.stop()
        except:
            pass
        try:
            self.cl2.stop()
        except:
            pass
        try:
            self.srv.stop()
        except:
            pass

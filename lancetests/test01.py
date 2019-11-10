import os
import errno
import time
import random
import string

from lance.server import Server
from testbase import TestBase


class AddRemoveFoldersTest(TestBase):
    def run(self):
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

            self.cl0.syncthingHandler.setServerSecret('wowsecret2').result()
            self.cl1.syncthingHandler.setServerSecret('wowsecret2').result()
            self.cl2.syncthingHandler.setServerSecret('wowsecret2').result()
            self.srv.syncthingHandler.setServerSecret('wowsecret2').result()

            self.srv.syncthingHandler.addServer(srvid).result()
            self.srv.syncthingHandler.addDevice(cl0id).result()
            self.srv.syncthingHandler.addDevice(cl1id).result()
            self.srv.syncthingHandler.addDevice(cl2id).result()
            ###
            self.cl0.syncthingHandler.addServer(srvid).result()
            self.cl1.syncthingHandler.addServer(srvid).result()
            self.cl2.syncthingHandler.addServer(srvid).result()

            fidA = self.srv.syncthingHandler.addFolder(fpath0s, 'folder_A', [cl0id]).result()
            fidB = self.srv.syncthingHandler.addFolder(fpath1s, 'folder_B', [cl1id]).result()
            fidC = self.srv.syncthingHandler.addFolder(fpath2s, 'folder_C', [cl2id]).result()

            for i in range(3):
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

                time.sleep(25)

                with open(os.path.join(fpath0s, 'f0_test.txt'), 'r') as f:
                    ss0 = f.read()
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'r') as f:
                    ss1 = f.read()
                with open(os.path.join(fpath2s, 'f2_test.txt'), 'r') as f:
                    ss2 = f.read()

                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c1 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c2 folder 2 contents mismatch. test failed. \n%s' % s2

                self.srv.syncthingHandler.removeDeviceFromFolder(fidA, cl0id).result()
                self.srv.syncthingHandler.removeDeviceFromFolder(fidB, cl1id).result()
                self.srv.syncthingHandler.removeDeviceFromFolder(fidC, cl2id).result()

                self.srv.syncthingHandler.addDeviceToFolder(fidA, cl1id).result()
                self.srv.syncthingHandler.addDeviceToFolder(fidB, cl2id).result()
                self.srv.syncthingHandler.addDeviceToFolder(fidC, cl0id).result()

                time.sleep(35)

                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_A')), 'FolderA not deleted from cl0'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_B')), 'FolderB not deleted from cl1'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_C')), 'FolderC not deleted from cl2'

                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'folder_A', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c1 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl2', 'data', 'folder_B', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c2 folder 1 contents mismatch. test failed. \n%s' % s1
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'folder_C', 'f2_test.txt'), 'r') as f:
                    assert ss2 == f.read(), 'c3 folder 2 contents mismatch. test failed. \n%s' % s2


                self.cl0.stop()
                self.cl1.stop()
                self.cl2.stop()
                self.srv.stop()

                print("EVERYTHING STOPPED! ITERATION %d DONE" % i)

                self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl1 = Server(os.path.join(self.test_root_path(), 'cl1', 'config'), os.path.join(self.test_root_path(), 'cl1', 'data'))
                self.cl2 = Server(os.path.join(self.test_root_path(), 'cl2', 'config'), os.path.join(self.test_root_path(), 'cl2', 'data'))

                self.srv.syncthingHandler.removeDeviceFromFolder(fidA, cl1id).result()
                self.srv.syncthingHandler.removeDeviceFromFolder(fidB, cl2id).result()
                self.srv.syncthingHandler.removeDeviceFromFolder(fidC, cl0id).result()

                self.srv.syncthingHandler.addDeviceToFolder(fidA, cl0id).result()
                self.srv.syncthingHandler.addDeviceToFolder(fidB, cl1id).result()
                self.srv.syncthingHandler.addDeviceToFolder(fidC, cl2id).result()
        finally:
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

import os
import errno
import time
import random
import string

from lance.server import Server
from testbase import TestBase


class BasicTest0(TestBase):
    def run(self):
        try:
            self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
            self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
            self.cl1 = Server(os.path.join(self.test_root_path(), 'cl1', 'config'), os.path.join(self.test_root_path(), 'cl1', 'data'))

            fpath0s = os.path.join(self.test_root_path(), 'folder0_s')
            fpath1s = os.path.join(self.test_root_path(), 'folder1_s')
            fpath0c = os.path.join(self.test_root_path(), 'folder0_c')
            fpath1c = os.path.join(self.test_root_path(), 'folder1_c')
            for fpath in (fpath0s, fpath1s, fpath0c, fpath1c):
                try:
                    os.makedirs(fpath)
                except OSError as e:
                    if e.errno != errno.EEXIST:
                        raise

            self.cl0.syncthingHandler.set_server_secret('wowsecret').result()
            self.cl1.syncthingHandler.set_server_secret('wowsecret').result()
            self.srv.syncthingHandler.set_server_secret('wowsecret').result()

            self.srv.syncthingHandler.add_server(self.srv.syncthingHandler.myId()).result()
            self.srv.syncthingHandler.add_device(self.cl0.syncthingHandler.myId()).result()
            self.srv.syncthingHandler.add_device(self.cl1.syncthingHandler.myId()).result()
            self.cl0.syncthingHandler.add_server(self.srv.syncthingHandler.myId()).result()
            self.cl1.syncthingHandler.add_server(self.srv.syncthingHandler.myId()).result()

            self.srv.syncthingHandler.add_folder(fpath0s, 'le folder 0', [self.cl0.syncthingHandler.myId()]).result()
            self.srv.syncthingHandler.add_folder(fpath1s, 'la foldero 1', [self.cl1.syncthingHandler.myId()]).result()

            for i in range(3):
                self.srv.start()
                self.cl0.start()
                self.cl1.start()

                time.sleep(10)
                s0 = 'f0 random information %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                s1 = 'f1 randomer informationes %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                with open(os.path.join(fpath0s, 'f0_test.txt'), 'w') as f:
                    f.write(s0)
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'w') as f:
                    f.write(s1)

                time.sleep(15) # + i*999999)

                with open(os.path.join(fpath0s, 'f0_test.txt'), 'r') as f:
                    ss0 = f.read()
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'r') as f:
                    ss1 = f.read()

                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'la foldero 1', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c1 folder 1 contents mismatch. test failed. \n%s' % s1

                print('test 0, iter %d.\n    %s\n    %s\n----\n    %s\n    %s\n' % (i, s0, ss0, s1, ss1))

                os.remove(os.path.join(self.test_root_path(), 'cl1', 'data', 'la foldero 1', 'f1_test.txt'))
                os.remove(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0', 'f0_test.txt'))

                time.sleep(15)

                assert not os.path.exists(os.path.join(fpath0s, 'f0_test.txt')), 'cl0-s file removal not synced'
                assert not os.path.exists(os.path.join(fpath1s, 'f1_test.txt')), 'cl1-s file removal not synced'

                self.cl0.stop()
                self.cl1.stop()
                self.srv.stop()

                print("EVERYTHING STOPPED! ITERATION %d DONE" % i)

                self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl1 = Server(os.path.join(self.test_root_path(), 'cl1', 'config'), os.path.join(self.test_root_path(), 'cl1', 'data'))
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
            self.srv.stop()
        except:
            pass
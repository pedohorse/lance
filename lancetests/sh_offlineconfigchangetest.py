import os
import errno
import time
import random
import string

from lance.server import Server
from testbase import TestBase


class SH_OfflineConfigChangeTest(TestBase):
    def testBody(self, logger):
        try:
            self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
            self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))

            fpath0s = os.path.join(self.test_root_path(), 'folder0_s')
            fpath1s = os.path.join(self.test_root_path(), 'folder1_s')
            for fpath in (fpath0s, fpath1s):
                try:
                    os.makedirs(fpath)
                except OSError as e:
                    if e.errno != errno.EEXIST:
                        raise

            self.cl0.syncthingHandler.set_server_secret('wowsecret').result()
            self.srv.syncthingHandler.set_server_secret('wowsecret').result()

            self.srv.syncthingHandler.add_server(self.srv.syncthingHandler.myId()).result()
            self.srv.syncthingHandler.add_device(self.cl0.syncthingHandler.myId()).result()
            self.cl0.syncthingHandler.add_server(self.srv.syncthingHandler.myId()).result()

            fid0 = self.srv.syncthingHandler.add_folder(fpath0s, 'le folder 0', [self.cl0.syncthingHandler.myId()]).result()
            fid1 = self.srv.syncthingHandler.add_folder(fpath1s, 'la foldero 1', [self.cl0.syncthingHandler.myId()]).result()

            for i in range(3):
                logger.print('starting iteration %d' % i)
                logger.print('starting servers...')
                self.srv.start()
                self.cl0.start()

                logger.sleep(10)
                logger.print('filling folders f0 f1 with junk')
                s0 = 'f0 random information %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                s1 = 'f1 randomer informationes %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                with open(os.path.join(fpath0s, 'f0_test.txt'), 'w') as f:
                    f.write(s0)
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'w') as f:
                    f.write(s1)

                logger.sleep(15)

                logger.print('checking folders at cl0 side')
                with open(os.path.join(fpath0s, 'f0_test.txt'), 'r') as f:
                    ss0 = f.read()
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'r') as f:
                    ss1 = f.read()

                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c0 folder 1 contents mismatch. test failed. \n%s' % s1

                logger.print('test 0, iter %d.\n    %s\n    %s\n----\n    %s\n    %s\n' % (i, s0, ss0, s1, ss1))
                logger.print('stopping cl0 and removing then it from folder access')
                self.cl0.stop()
                self.srv.syncthingHandler.remove_device_from_folder(fid0, self.cl0.syncthingHandler.myId())

                logger.sleep(10)

                logger.print('starting cl0...')
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl0.start()

                logger.sleep(15)

                logger.print('checking folder access')
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0')), 'removed f 0 while client was down, but it is still here'
                assert os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1')), 'removed f 0 while client was down, but f 1 is not in place'
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c0 folder 1 contents mismatch. test failed. \n%s' % s1

                logger.print('stopping cl0 and removing then it from folder access')
                self.cl0.stop()
                self.srv.syncthingHandler.remove_device_from_folder(fid1, self.cl0.syncthingHandler.myId())

                logger.sleep(10)

                logger.print('starting cl0...')
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl0.start()

                logger.sleep(15)

                logger.print('checking folder access')
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0')), 'removed f 1 while client was down, but f 0 it is still here'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1')), 'removed f 1 while client was down, but it is still here'

                with open(os.path.join(fpath0s, 'f0_test.txt'), 'r') as f:
                    ss00 = f.read()
                    assert ss00 == ss0, 'after folder deletion servers f0 content changed!\n%s\nvs\n%s\n' % (ss00, ss0)
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'r') as f:
                    ss11 = f.read()
                    assert ss11 == ss1, 'after folder deletion servers f1 content changed!\n%s\nvs\n%s\n' % (ss11, ss1)

                logger.print('stopping cl0 and then adding it to folder access')
                self.cl0.stop()
                self.srv.syncthingHandler.add_device_to_folder(fid0, self.cl0.syncthingHandler.myId())

                logger.sleep(10)

                logger.print('starting cl0...')
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl0.start()

                logger.sleep(15)

                logger.print('checking folder access')
                assert os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0')), 'added f 0 while client was down, but it is not here'
                assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1')), 'added f 0 while client was down, but f 1 is here'
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0

                logger.print('stopping cl0 and then adding it to folder access')
                self.cl0.stop()
                self.srv.syncthingHandler.add_device_to_folder(fid1, self.cl0.syncthingHandler.myId())

                logger.sleep(10)

                logger.print('starting cl0...')
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
                self.cl0.start()

                logger.sleep(15)

                logger.print('checking folder access')
                assert os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0')), 'added f 1 while client was down, but f 0 it is not here'
                assert os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1')), 'added f 1 while client was down, but it is not here'
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'le folder 0', 'f0_test.txt'), 'r') as f:
                    assert ss0 == f.read(), 'c0 folder 0 contents mismatch. test failed. \n%s' % s0
                with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'la foldero 1', 'f1_test.txt'), 'r') as f:
                    assert ss1 == f.read(), 'c0 folder 1 contents mismatch. test failed. \n%s' % s1

                with open(os.path.join(fpath0s, 'f0_test.txt'), 'r') as f:
                    ss00 = f.read()
                    assert ss00 == ss0, 'after folder deletion servers f0 content changed!\n%s\nvs\n%s\n' % (ss00, ss0)
                with open(os.path.join(fpath1s, 'f1_test.txt'), 'r') as f:
                    ss11 = f.read()
                    assert ss11 == ss1, 'after folder deletion servers f1 content changed!\n%s\nvs\n%s\n' % (ss11, ss1)

                logger.print('stopping all')
                self.cl0.stop()
                self.srv.stop()

                logger.print("EVERYTHING STOPPED! ITERATION %d DONE" % i)

                self.srv = Server(os.path.join(self.test_root_path(), 'srv', 'config'), os.path.join(self.test_root_path(), 'srv', 'data'))
                self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
        finally:
            try:
                self.cl0.stop()
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
            self.srv.stop()
        except:
            pass
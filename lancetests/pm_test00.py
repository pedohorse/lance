import os
import errno
import time
import random
import string

from lance.server import Server
from testbase import TestBase


class PM_Test0(TestBase):
    def testBody(self, logger):
        try:
            self.srv0 = Server(os.path.join(self.test_root_path(), 'srv0', 'config'), os.path.join(self.test_root_path(), 'srv0', 'data'))
            self.srv1 = Server(os.path.join(self.test_root_path(), 'srv1', 'config'), os.path.join(self.test_root_path(), 'srv1', 'data'))
            self.cl0 = Server(os.path.join(self.test_root_path(), 'cl0', 'config'), os.path.join(self.test_root_path(), 'cl0', 'data'))
            self.cl1 = Server(os.path.join(self.test_root_path(), 'cl1', 'config'), os.path.join(self.test_root_path(), 'cl1', 'data'))

            fpath0s0 = os.path.join(self.test_root_path(), 'folder0_s0')
            fpath1s0 = os.path.join(self.test_root_path(), 'folder1_s0')
            fpath0s1 = os.path.join(self.test_root_path(), 'folder0_s1')
            fpath1s1 = os.path.join(self.test_root_path(), 'folder1_s1')
            for fpath in (fpath0s0, fpath1s0, fpath0s1, fpath1s1):
                try:
                    os.makedirs(fpath)
                except OSError as e:
                    if e.errno != errno.EEXIST:
                        raise

            self.cl0.syncthingHandler.set_server_secret('wowsecret').result()
            self.cl1.syncthingHandler.set_server_secret('wowsecret').result()
            self.srv0.syncthingHandler.set_server_secret('wowsecret').result()
            self.srv1.syncthingHandler.set_server_secret('wowsecret').result()

            self.srv0.syncthingHandler.add_server(self.srv0.syncthingHandler.myId()).result()
            self.srv0.syncthingHandler.add_server(self.srv1.syncthingHandler.myId()).result()
            self.srv1.syncthingHandler.add_server(self.srv0.syncthingHandler.myId()).result()
            self.srv1.syncthingHandler.add_server(self.srv1.syncthingHandler.myId()).result()

            self.srv0.syncthingHandler.add_device(self.cl0.syncthingHandler.myId()).result()
            self.srv0.syncthingHandler.add_device(self.cl1.syncthingHandler.myId()).result()
            self.cl0.syncthingHandler.add_server(self.srv0.syncthingHandler.myId()).result()
            self.cl1.syncthingHandler.add_server(self.srv0.syncthingHandler.myId()).result()

            self.srv0.add_project('testproject1')

            for i in range(3):
                logger.print('starting iteration %d' % i)
                self.srv0.start()
                self.srv1.start()
                self.cl0.start()
                self.cl1.start()
                logger.print('all servers started')

                logger.print('checking project manager for project')
                def _check0_():
                    assert len(self.srv0.projectManagers) == 1, 'srv0 has %d projects instead of 1' % len(self.srv0.projectManagers)
                    assert len(self.srv1.projectManagers) == 1, 'srv1 has %d projects instead of 1' % len(self.srv1.projectManagers)
                logger.check(_check0_, timeout=35, time_to_hold=10)

                srv0_pm = tuple(self.srv0.projectManagers.values())[0]
                srv1_pm = tuple(self.srv1.projectManagers.values())[0]
                assert srv0_pm.project_name() == 'testproject1', 'srv0 project name mismatch. got %s' % srv0_pm.project_name()
                assert srv1_pm.project_name() == 'testproject1', 'srv1 project name mismatch. got %s' % srv1_pm.project_name()
                srv0_shts = srv0_pm.get_shots().result()
                srv1_shts = srv1_pm.get_shots().result()
                assert len(srv0_shts) == 0, 'srv0 shotcount == %d, expected 0' % len(srv0_shts)
                assert len(srv1_shts) == 0, 'srv1 shotcount == %d, expected 0' % len(srv1_shts)

                logger.print('adding a test shot')
                srv0_pm.add_shot('first shot', 'firstshot_id', fpath0s0).result()
                logger.print('adding a user')
                srv1_pm.add_user('al.bob', 'Alice Bobovich', [self.cl0.syncthingHandler.myId(), self.cl1.syncthingHandler.myId()], [('firstshot_id', 'main')]).result()

                s0 = 'f0 random information %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                with open(os.path.join(fpath0s0, 'f0_test.txt'), 'w') as f:
                    f.write(s0)
                with open(os.path.join(fpath0s0, 'f0_test.txt'), 'r') as f:
                    s0 = f.read()

                logger.print('checking if user exists and checking shot folder')
                def _check1_():
                    assert 'al.bob' in srv0_pm.get_users().result(), 'al.bob not in srv0'
                    assert 'al.bob' in srv1_pm.get_users().result(), 'al.bob not in srv1'

                    with open(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shot :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'srv1 folder 0 contents mismatch. test failed. \n%s' % s0
                    with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shot :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'c0 shot folder contents mismatch. test failed. \n%s' % s0
                    with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shot :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'c1 shot folder contents mismatch. test failed. \n%s' % s0
                logger.check(_check1_, timeout=35, time_to_hold=10)

                logger.print('removing shot')
                srv1_pm.remove_shot('firstshot_id').result()
                logger.print('checking the shot is removed')
                def _check2_():
                    assert os.path.exists(fpath0s0), 'srv0 shotpath does not exists'  # servers do not delete folders from disc
                    assert os.path.exists(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shot :main')), 'srv1 shotpath does not exists' # servers do not delete folders from disc
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shot :main')), 'cl0 shotpath still exists'
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shot :main')), 'cl1 shotpath still exists'
                logger.check(_check2_, timeout=35, time_to_hold=10)

                logger.print('removing user')
                srv1_pm.remove_user('al.bob')

                logger.print('checking if user is gone')
                def _check3_():
                    assert len(srv0_pm.get_users().result()) == 0, 'srv0 has nonzero users'
                    assert len(srv1_pm.get_users().result()) == 0, 'srv1 has nonzero users'
                logger.check(_check3_, timeout=35, time_to_hold=10)

                logger.print('stopping all servers')
                self.cl0.stop()
                self.cl1.stop()
                self.srv0.stop()
                self.srv1.stop()

                logger.print("EVERYTHING STOPPED! ITERATION %d DONE" % i)

                self.srv0 = Server(os.path.join(self.test_root_path(), 'srv0', 'config'), os.path.join(self.test_root_path(), 'srv0', 'data'))
                self.srv1 = Server(os.path.join(self.test_root_path(), 'srv1', 'config'), os.path.join(self.test_root_path(), 'srv1', 'data'))
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
                self.srv0.stop()
            except:
                pass
            try:
                self.srv1.stop()
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
            self.srv0.stop()
        except:
            pass
        try:
            self.srv1.stop()
        except:
            pass
import os
import errno
import time
import random
import string

from lance.server import Server
from testbase import TestBase


class PM_Test_UserAddRemoveDevs(TestBase):
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

            #self.srv0.syncthingHandler.add_device(self.cl0.syncthingHandler.myId()).result()
            #self.srv0.syncthingHandler.add_device(self.cl1.syncthingHandler.myId()).result()
            self.cl0.syncthingHandler.add_server(self.srv0.syncthingHandler.myId()).result()
            self.cl1.syncthingHandler.add_server(self.srv0.syncthingHandler.myId()).result()

            self.srv0.add_project('testproject1')

            for i in range(5):
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
                    assert os.path.exists(os.path.join(self.test_root_path(), 'srv0', 'data', 'project_testproject1_configuration', 'config.cfg')), 'srv0 init project configuration is missing'
                    assert os.path.exists(os.path.join(self.test_root_path(), 'srv1', 'data', 'project testproject1 configuration', 'config.cfg')), 'srv1 init project configuration is missing'
                logger.check(_check0_, timeout=300, time_to_hold=30)

                srv0_pm = tuple(self.srv0.projectManagers.values())[0]
                srv1_pm = tuple(self.srv1.projectManagers.values())[0]
                assert srv0_pm.project_name() == 'testproject1', 'srv0 project name mismatch. got %s' % srv0_pm.project_name()
                assert srv1_pm.project_name() == 'testproject1', 'srv1 project name mismatch. got %s' % srv1_pm.project_name()
                srv0_shts = srv0_pm.get_shots().result()
                srv1_shts = srv1_pm.get_shots().result()
                assert len(srv0_shts) == 0, 'srv0 shotcount == %d, expected 0' % len(srv0_shts)
                assert len(srv1_shts) == 0, 'srv1 shotcount == %d, expected 0' % len(srv1_shts)

                logger.print('adding a test shotid')
                srv0_pm.add_shot('first shotid', 'firstshot_id', fpath0s0).result()
                logger.print('adding a user')
                srv1_pm.add_user('al.bob', 'Alice Bobovich', [], [('firstshot_id', 'main')]).result()

                s0 = 'f0 random information %s' % ''.join([random.choice(string.printable) for _ in range(16)])
                with open(os.path.join(fpath0s0, 'f0_test.txt'), 'w') as f:
                    f.write(s0)
                with open(os.path.join(fpath0s0, 'f0_test.txt'), 'r') as f:
                    s0 = f.read()

                logger.print('checking if user exists and checking shotid folder')
                def _check1_():
                    """
                    checking for user and shotid exist
                    """
                    assert 'al.bob' in srv0_pm.get_users().result(), 'al.bob not in srv0'
                    assert 'al.bob' in srv1_pm.get_users().result(), 'al.bob not in srv1'
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main')), 'cl0 has shotid!'
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shotid :main')), 'cl1 has shotid!'
                    assert 'firstshot_id' in srv0_pm.get_shots().result()
                    assert 'firstshot_id' in srv1_pm.get_shots().result()

                def _check1a_():
                    """
                    checking for shotid data sync
                    """
                    _check1_()
                    with open(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'srv1 folder 0 contents mismatch. test failed. \n%s' % s0
                logger.check(_check1_, timeout=300)
                logger.check(_check1a_, timeout=300, time_to_hold=30)

                logger.print('adding cl0 to user devices')
                srv0_pm.add_devices_to_user('al.bob', self.cl0.syncthingHandler.myId())
                def _check2_():
                    """
                    checking cl0 has the shotid
                    """
                    assert 'al.bob' in srv0_pm.get_users().result(), 'al.bob not in srv0'
                    assert 'al.bob' in srv1_pm.get_users().result(), 'al.bob not in srv1'
                    assert 'firstshot_id' in srv0_pm.get_shots().result()
                    assert 'firstshot_id' in srv1_pm.get_shots().result()
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shotid :main')), 'cl1 has shotid!'
                    assert os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main')), 'cl0 does not have shotid!'

                def _check2a_():
                    """
                    checking for data sync
                    """
                    _check2_()
                    with open(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'srv1 folder 0 contents mismatch. test failed. \n%s' % s0
                    with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'c0 shotid folder contents mismatch. test failed. \n%s' % s0

                logger.check(_check2_, timeout=300)
                logger.check(_check2a_, timeout=300, time_to_hold=30)

                logger.print('adding cl1 to user devices')
                srv1_pm.add_devices_to_user('al.bob', self.cl1.syncthingHandler.myId())
                def _check3_():
                    assert 'al.bob' in srv0_pm.get_users().result(), 'al.bob not in srv0'
                    assert 'al.bob' in srv1_pm.get_users().result(), 'al.bob not in srv1'

                    with open(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'srv1 folder 0 contents mismatch. test failed. \n%s' % s0
                    with open(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'c0 shotid folder contents mismatch. test failed. \n%s' % s0
                    with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'c1 shotid folder contents mismatch. test failed. \n%s' % s0
                    assert 'firstshot_id' in srv0_pm.get_shots().result()
                    assert 'firstshot_id' in srv1_pm.get_shots().result()
                logger.check(_check3_, timeout=300, time_to_hold=30)

                logger.print('removing cl0 from user devices')
                srv1_pm.remove_devices_from_user('al.bob', self.cl0.syncthingHandler.myId())
                def _check4_():
                    srv0users = srv0_pm.get_users().result()
                    srv1users = srv1_pm.get_users().result()
                    cl0id = self.cl0.syncthingHandler.myId()
                    assert 'al.bob' in srv0users, 'al.bob not in srv0'
                    assert 'al.bob' in srv1users, 'al.bob not in srv1'
                    assert cl0id not in srv0users['al.bob'].device_ids()
                    assert cl0id not in srv1users['al.bob'].device_ids()

                    with open(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'srv1 folder 0 contents mismatch. test failed. \n%s' % s0
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main')), 'cl0 has shotid!'
                    with open(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'c1 shotid folder contents mismatch. test failed. \n%s' % s0
                    assert 'firstshot_id' in srv0_pm.get_shots().result()
                    assert 'firstshot_id' in srv1_pm.get_shots().result()
                logger.check(_check4_, timeout=300, time_to_hold=40)

                logger.print('removing cl1 from user devices')
                srv0_pm.remove_devices_from_user('al.bob', self.cl1.syncthingHandler.myId())
                def _check5_():
                    srv0users = srv0_pm.get_users().result()
                    srv1users = srv1_pm.get_users().result()
                    cl1id = self.cl1.syncthingHandler.myId()
                    assert 'al.bob' in srv0users, 'al.bob not in srv0'
                    assert 'al.bob' in srv1users, 'al.bob not in srv1'
                    assert cl1id not in srv0users['al.bob'].device_ids()
                    assert cl1id not in srv1users['al.bob'].device_ids()

                    with open(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shotid :main', 'f0_test.txt'), 'r') as f:
                        assert s0 == f.read(), 'srv1 folder 0 contents mismatch. test failed. \n%s' % s0
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main')), 'cl0 has shotid!'
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shotid :main')), 'cl1 has shotid!'
                    assert 'firstshot_id' in srv0_pm.get_shots().result()
                    assert 'firstshot_id' in srv1_pm.get_shots().result()
                logger.check(_check5_, timeout=300, time_to_hold=40)

                logger.print('removing shotid')
                srv1_pm.remove_shot('firstshot_id').result()
                logger.print('checking the shotid is removed')
                def _check6_():
                    assert os.path.exists(fpath0s0), 'srv0 shotpath does not exists'  # servers do not delete folders from disc
                    assert os.path.exists(os.path.join(self.test_root_path(), 'srv1', 'data', 'first shotid :main')), 'srv1 shotpath does not exists' # servers do not delete folders from disc
                    #assert len([x for x in os.listdir(os.path.join(self.test_root_path(), 'srv0', 'data', 'server', 'configuration', 'folders')) if 'firstshot' in x]) == 0, 'firstshot is still in srv0 config'
                    #assert len([x for x in os.listdir(os.path.join(self.test_root_path(), 'srv1', 'data', 'server', 'configuration', 'folders')) if 'firstshot' in x]) == 0, 'firstshot is still in srv1 config'
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl0', 'data', 'first shotid :main')), 'cl0 shotpath still exists'
                    assert not os.path.exists(os.path.join(self.test_root_path(), 'cl1', 'data', 'first shotid :main')), 'cl1 shotpath still exists'
                    assert 'firstshot_id' not in srv0_pm.get_shots().result()
                    assert 'firstshot_id' not in srv1_pm.get_shots().result()
                logger.check(_check6_, timeout=300, time_to_hold=20)

                logger.print('removing user')
                srv1_pm.remove_user('al.bob')

                logger.print('checking if user is gone')
                def _check7_():
                    assert len(srv0_pm.get_users().result()) == 0, 'srv0 has nonzero users'
                    assert len(srv1_pm.get_users().result()) == 0, 'srv1 has nonzero users'
                logger.check(_check7_, timeout=300, time_to_hold=30)

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
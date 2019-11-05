import os
import shutil

basepath = os.environ['HOME']
testrootpath = os.path.join(basepath, 'lance-tests')

#coderoot = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lance')


class TestBase:
    def __init__(self):
        self.__testrootpath = os.path.join(testrootpath, self.__class__.__name__)

        shutil.rmtree(self.__testrootpath, ignore_errors=True)
        os.makedirs(self.__testrootpath)

    def test_root_path(self):
        return self.__testrootpath

    def run(self):
        raise NotImplementedError()

    def cleanup(self):
        shutil.rmtree(self.__testrootpath, ignore_errors=True)


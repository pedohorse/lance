import sys
import os
import shutil
import time

basepath = os.environ['HOME']
testrootpath = os.path.join(basepath, 'lance-tests')

#coderoot = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lance')


class TestBase:
    class StdoutSwitcher:
        def __init__(self, logpath):
            self.__logpath = logpath
            self.__stashedstdout = None
            self.__stashedstderr = None

        def __enter__(self):
            self.__stashedstdout = sys.stdout
            self.__stashedstderr = sys.stderr
            self.__logfile = open(self.__logpath, 'w')
            sys.stdout = self.__logfile
            sys.stderr = self.__logfile
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            sys.stdout = self.__stashedstdout
            sys.stderr = self.__stashedstderr
            self.__logfile.close()

        def print(self, *args):
            etime = time.time()
            timestamp = '%s-%d: ' % (time.strftime("%H-%M-%S", time.localtime(etime)), (etime % 1) * 1e6)
            print(timestamp, *args, file=self.__stashedstdout)
            print(timestamp, *args)

        def sleep(self, seconds):
            self.print('waiting for %d seconds...' % seconds)
            time.sleep(seconds)

    def __init__(self):
        self.__testrootpath = os.path.join(testrootpath, self.__class__.__name__)

        shutil.rmtree(self.__testrootpath, ignore_errors=True)
        os.makedirs(self.__testrootpath)

        self.__logfilepath = os.path.join(self.__testrootpath, 'output.log')

    def test_root_path(self):
        return self.__testrootpath

    def testBody(self, logger):
        raise NotImplementedError()

    def run(self):
        with TestBase.StdoutSwitcher(self.__logfilepath) as logger:
            self.testBody(logger)


    def cleanup(self):
        shutil.rmtree(self.__testrootpath, ignore_errors=True)

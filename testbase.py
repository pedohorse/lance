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

        def check(self, assertion, timeout: int, time_to_hold: int = 0, interval: int = 1):
            self.print('waiting for assertion to be true. timeout %d seconds...' % timeout)
            last_ex = None
            hold_start_time = None
            start_time = time.time()
            for i in range((timeout + time_to_hold) // interval + 1):
                if time_to_hold > 0 and hold_start_time is None and i * interval > timeout:  # if timeout already reached and we are not waiting for condition to hold yet
                    continue
                try:
                    assertion()
                    if time_to_hold <= 0:
                        break
                    elif hold_start_time is None:
                        hold_start_time = time.time()
                    else:
                        if time.time() - hold_start_time > time_to_hold:
                            break
                        time.sleep(interval)
                        continue
                except Exception as e:
                    last_ex = e
                    hold_start_time = None
                    time.sleep(interval)
            else:
                self.print('assertion did not hold')
                raise last_ex if last_ex is not None else RuntimeError('unknown exception somehow')
            status = 'assertion became true after %.1f sec' % (hold_start_time - start_time)
            if time_to_hold > 0:
                status += ' and was held true for %d sec' % time_to_hold
            self.print(status)


    def __init__(self):
        self.__testrootpath = os.path.join(testrootpath, self.__class__.__name__)

        shutil.rmtree(self.__testrootpath, ignore_errors=True)
        os.makedirs(self.__testrootpath)

        self.__logfilepath = os.path.join(self.__testrootpath, 'output.log')

    def test_root_path(self):
        return self.__testrootpath

    def testBody(self, logger: 'TestBase.StdoutSwitcher'):
        raise NotImplementedError()

    def run(self):
        with TestBase.StdoutSwitcher(self.__logfilepath) as logger:
            self.testBody(logger)


    def cleanup(self):
        shutil.rmtree(self.__testrootpath, ignore_errors=True)

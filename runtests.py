#!/usr/bin/env python3

import sys
import traceback
import lancetests
import time
import re


if __name__ == '__main__':
    results = {}
    filterre = re.compile('.*')
    if len(sys.argv) > 1:
        try:
            filterre = re.compile(sys.argv[1])
        except Exception as e:
            print('bad regexp: %s' % repr(e))
            sys.exit(1)
    filteredClassList = list(filter(lambda x: filterre.match(x.__name__) is not None, lancetests.testClassList))
    print('\n\n\t\tTESTS WILL BE RUN:\n\t\t\t{testnames}\n\n'.format(testnames='\n\t\t\t'.join(map(lambda x: x.__name__, filteredClassList))))
    for TestClass in filteredClassList:
        print('\n\nrunning test: {testname}\n\n'.format(testname=TestClass.__name__))
        #if TestClass.__name__ != 'AddRemoveFoldersTest':continue
        _starttime = time.time()
        try:
            TestClass().run()
            results[TestClass] = {'passed': True, 'time': time.time() - _starttime}
        except Exception as e:
            traceback_string = ''.join(traceback.format_exc())
            results[TestClass] = {'passed': False, 'time': time.time() - _starttime, 'error': {'traceback_string': traceback_string, 'exeption': e}}
            print('test %s:' % TestClass.__name__)
            print(repr(e))
            print(traceback_string)
            print('\n-------------------------------------\n')

    print('\n\nAll tests finished!\n\n')
    failedTests = []
    for TestClass, result in results.items():
        print('{passed} "{classname}" in {time:.3f}'.format(classname=TestClass.__name__, passed='Passed' if result['passed'] else 'Failed', time=result.get('time', -1)))
        if not result['passed']:
            failedTests.append((TestClass, result))

    if len(failedTests) > 0:
        print("\n\nfailed tests:\n")
    for TestClass, result in failedTests:
        print('test %s:' % TestClass.__name__)
        print(result['error']['traceback_string'])
        print('\n-------------------------------------\n')
    print('\n\n')

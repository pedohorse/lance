#!/usr/bin/env python3

import sys
import traceback
import lancetests
import time
import re


if __name__ == '__main__':
    results = {}
    filterre = None
    if len(sys.argv) > 1:
        try:
            filterre = re.compile(sys.argv[1])
        except Exception as e:
            print('bad regexp: %s' % repr(e))
            sys.exit(1)
    for TestClass in lancetests.testClassList:
        if filterre is not None and filterre.match(TestClass.__name__) is None:
            continue
        #if TestClass.__name__ != 'AddRemoveFoldersTest':continue
        _starttime = time.time()
        try:
            TestClass().run()
            results[TestClass] = {'passed': True, 'time': time.time() - _starttime}
        except Exception as e:
            results[TestClass] = {'passed': False, 'time': time.time() - _starttime, 'error': {'traceback_string': ''.join(traceback.format_exc()), 'exeption': e}}

    print('\n\nAll tests finished!\n\n')
    for TestClass, result in results.items():
        print('{passed} "{classname}" in {time:.3f}'.format(classname=TestClass.__name__, passed='Passed' if result['passed'] else 'Failed', time=result.get('time', -1)))
        if not result['passed']:
            print(result['error']['traceback_string'])
            print('\n')

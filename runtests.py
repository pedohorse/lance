#!/usr/bin/env python3

import traceback
import lancetests
import time


if __name__ == '__main__':
    results = {}
    for TestClass in lancetests.testClassList:
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

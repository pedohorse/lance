
import os
import importlib
import inspect
from testbase import TestBase


testClassList = []
testModuleList = []

def rescanTests():
	global testClassList
	global testModuleList
	global __all__
	testClassList = []
	testModuleList = []
	files = [os.path.splitext(x)[0] for x in os.listdir(os.path.dirname(__file__)) if os.path.splitext(x)[1] == ".py" and x != "__init__.py"]
	__all__ = files
	for fn in files:
		try:
			newmodule = importlib.import_module(".".join((__name__, fn)))
			importlib.reload(newmodule)
		except Exception as e:
			print("failed to loade test file %s: %s" % (fn, repr(e)))
			continue
		testModuleList.append(newmodule)
		for name, obj in inspect.getmembers(newmodule):
			if inspect.isclass(obj) and TestBase in inspect.getmro(obj)[1:]:
				testClassList.append(obj)

rescanTests()
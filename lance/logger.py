import time

_loggercache = {}

def get_logger(name):
    global _loggercache
    if name in _loggercache:
        return _loggercache[name]

    def printer(level, *args):
        if level >= printer.min_log_level:
            etime = time.time()
            timestamp = '%s-%d: ' % (time.strftime("%H-%M-%S", time.localtime(etime)), (etime % 1) * 1e6)
            print("%s: %s :: %s" % (timestamp, name.upper(), ', '.join(map(repr, args))))

    printer.min_log_level = 1
    _loggercache[name] = printer
    return printer

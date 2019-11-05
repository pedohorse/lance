
_loggercache = {}

def get_logger(name):
    global _loggercache
    if name in _loggercache:
        return _loggercache[name]

    def printer(level, *args):
        if level >= printer.min_log_level:
            print("%s :: %s" % (name.upper(), ', '.join(map(repr, args))))

    printer.min_log_level = 3
    _loggercache[name] = printer
    return printer


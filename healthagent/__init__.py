def healthcheck(name):
    """Declare a function as a named healthcheck.
    The name is the report key visible in `health -s` output and
    can be targeted by `health -e <name>` / `health -p <name>`.
    """
    def decorator(func):
        func.report_name = name
        return func
    return decorator

def epilog(func):
    func.epilog = True
    return func

def status(func):
    func.status = True
    return func

def prolog(func):
    func.prolog = True
    return func

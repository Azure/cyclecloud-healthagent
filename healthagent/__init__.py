def healthcheck(name, args=None, description=None):
    """Declare a function as a named healthcheck.
    The name is the report key visible in `health -s` output and
    can be targeted by using `-c/--check <name>` with `health -e` / `health -p`.

    Args:
        name: Report key name.
        args: Optional list of user-facing argument names (e.g. ["gpu_id"]).
        description: Optional short description. Falls back to the function's docstring.
    """
    def decorator(func):
        func.report_name = name
        func.healthcheck_args = args or []
        func.healthcheck_description = description
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

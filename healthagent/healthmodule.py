from abc import ABC
from healthagent.reporter import Reporter
from healthagent import status
import inspect
import logging

log = logging.getLogger(__name__)

class HealthModule(ABC):
    """
    Base health module class. Extend this to implement specific health checks.
        - Override `create` for async initialization (e.g. background tasks, connections).
        - Override `status` for custom health status logic. By default, it summarizes the reporter.
        - Decorate methods with `@healthcheck("Name")` to declare their report name.
        - Decorate methods with `@status` to include their output in the health status response
        - Decorate methods with `@epilog` and/or `@prolog` to include their output in the epilog and/or prolog response (if implemented) respectively.

    """

    def __init__(self, reporter: Reporter):
        self.reporter = reporter
        self._handler_cache = {}
        self._checks_registry = None

    async def create(self):
        """Async initialization. Override to register background tasks, open connections, etc."""
        pass

    @status
    def status(self) -> dict:
        """Return current health status. Override if custom status logic is needed."""
        self._prune_stale_reports()
        return self.reporter.summarize()

    def _prune_stale_reports(self):
        """Remove reporter entries whose keys no longer match any registered healthcheck."""
        valid_names = set(self._build_checks_registry().keys())
        stale_keys = [k for k in self.reporter.store if k not in valid_names]
        for key in stale_keys:
            log.warning(f"Removing stale report key '{key}' (no matching healthcheck)")
            del self.reporter.store[key]

    def _get_handlers(self, attribute_flag: str) -> list:
        """
        Return list of bound methods decorated with the given flag.
        De-duplicates by method name so that overridden methods in subclasses
        are only returned once (the most-derived version).
        """
        if attribute_flag not in self._handler_cache:
            seen_names = set()
            handlers = []
            for klass in type(self).__mro__:
                for name, attr in klass.__dict__.items():
                    if name not in seen_names and getattr(attr, attribute_flag, False) is True:
                        seen_names.add(name)
                        handlers.append(getattr(self, name))
            self._handler_cache[attribute_flag] = handlers
        return self._handler_cache[attribute_flag]

    def _build_checks_registry(self) -> dict:
        """Discover all @healthcheck-decorated methods and classify them. Cached after first call.

        Returns:
            {report_name: {"signature": str, "category": [str], "interval": int|str}}
              interval: positive int for periodic checks, -1 for prolog/epilog-only, "async" for on-demand.
        """
        if self._checks_registry is not None:
            return self._checks_registry
        result = {}
        seen_names = set()
        for klass in type(self).__mro__:
            for name, attr in klass.__dict__.items():
                if name in seen_names:
                    continue
                report_name = getattr(attr, 'report_name', None)
                if not report_name:
                    continue
                seen_names.add(name)
                if report_name in result:
                    log.error(f"Duplicate healthcheck report_name '{report_name}' "
                              f"declared by '{name}' in {klass.__name__}; "
                              f"previous entry will be overwritten")
                handler = getattr(self, name)
                sig = str(inspect.signature(handler))
                interval = getattr(attr, 'interval', None)
                categories = []
                if getattr(attr, 'epilog', False):
                    categories.append('epilog')
                if getattr(attr, 'prolog', False):
                    categories.append('prolog')
                if interval is not None and interval > 0:
                    categories.append('background')
                    entry_interval = interval
                elif categories:
                    entry_interval = -1
                else:
                    categories.append('background')
                    entry_interval = "async"
                entry = {"signature": sig, "category": categories, "interval": entry_interval}
                result[report_name] = entry
        self._checks_registry = result
        return self._checks_registry

    def list_checks(self, attribute_flag: str = None) -> dict:
        """List health checks, optionally filtered by phase.

        Args:
            attribute_flag: If provided (epilog/prolog), return only checks for that phase
                            as {report_name: signature_str}.
                            If None, return all checks with full metadata
                            as {report_name: {"signature": str, "category": [str], "interval": int|str}}.
        """
        registry = self._build_checks_registry()
        if attribute_flag is None:
            return registry
        return {name: info["signature"] for name, info in registry.items()
                if attribute_flag in info["category"]}

    async def execute(self, attribute_flag: str, checks: dict = None) -> dict:
        """Execute handlers for the given flag.

        Args:
            attribute_flag: The phase flag (epilog, prolog, status).
            checks: Optional dict of {report_name: {kwarg: value, ...}}.
                    If provided, only handlers whose report_name is in checks
                    will run, and their kwargs will be passed through.
                    Matching is case-insensitive.
                    If None, all handlers for the flag run with no extra args.
        """
        # Normalize check names to lowercase for case-insensitive matching
        checks_lower = {k.lower(): v for k, v in checks.items()} if checks is not None else None
        response = {}
        for handler in self._get_handlers(attribute_flag):
            report_name = getattr(handler, 'report_name', None)
            if checks_lower is not None:
                if report_name is None or report_name.lower() not in checks_lower:
                    continue
                kwargs = checks_lower.get(report_name.lower(), {})
            else:
                kwargs = {}
            try:
                # Pre-validate kwargs to distinguish bad caller args from handler bugs
                if kwargs:
                    try:
                        inspect.signature(handler).bind(**kwargs)
                    except TypeError as e:
                        log.error(f"[{attribute_flag}] Invalid arguments for {handler.__name__} "
                                  f"(report_name={report_name}): {e}")
                        continue
                if inspect.iscoroutinefunction(handler):
                    ans = await handler(**kwargs)
                else:
                    ans = handler(**kwargs)
                if isinstance(ans, dict):
                    response.update(ans)
                else:
                    log.warning(f"[{attribute_flag}] {handler.__name__} did not return a dict. Ignoring.")
            except Exception as e:
                log.exception(f"[{attribute_flag}] Error executing {handler.__name__}: {e}")
        return response
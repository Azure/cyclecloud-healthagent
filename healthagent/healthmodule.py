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
        - Decorate methods with `@status` to include their output in the health status response
        - Decorate methods with `@epilog` and/or `@prolog` to include their output in the epilog and/or prolog response (if implemented) respectively.

    """

    def __init__(self, reporter: Reporter):
        self.reporter = reporter
        self._handler_cache = {}

    async def create(self):
        """Async initialization. Override to register background tasks, open connections, etc."""
        pass

    @status
    def status(self) -> dict:
        """Return current health status. Override if custom status logic is needed."""
        return self.reporter.summarize()

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

    async def execute(self, attribute_flag: str) -> dict:
        """Execute all handlers for the given flag, merge results."""
        response = {}
        for handler in self._get_handlers(attribute_flag):
            try:
                ans = await handler() if inspect.iscoroutinefunction(handler) else handler()
                if isinstance(ans, dict):
                    response.update(ans)
                else:
                    log.warning(f"[{attribute_flag}] {handler.__name__} did not return a dict. Ignoring.")
            except Exception as e:
                log.exception(f"[{attribute_flag}] Error executing {handler.__name__}: {e}")
        return response
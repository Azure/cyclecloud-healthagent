import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

log = logging.getLogger('healthagent')

class Scheduler:

    """
    run events on the event loop
    """
    stop_event = None
    _pool = None

    @staticmethod
    def pool(func):
        func.pool = True
        return func

    @staticmethod
    def periodic(interval):
        def decorator(func):
            # Handle classmethod
            if isinstance(func, classmethod):
                original_func = func.__func__  # Extract the original function
                def wrapper(cls, *args, **kwargs):
                    return original_func(cls, *args, **kwargs)
                wrapper.interval = interval
                return classmethod(wrapper)

            # Handle staticmethod
            elif isinstance(func, staticmethod):
                original_func = func.__func__  # Extract the original function
                def wrapper(*args, **kwargs):
                    return original_func(*args, **kwargs)
                wrapper.interval = interval
                return staticmethod(wrapper)
            else:
                func.interval = interval
                return func
        return decorator

    @classmethod
    async def __task_wrapper(self, interval, function, *args, **kwargs):
        """
        Runs a task, logs exceptions and re-adds it after it completes if interval is a positive integer.

        """
        out = None
        try:
            log.debug(f"interval: {interval}, function: {function}, {args}, {kwargs}")
            if function and callable(function):
                out = await function(*args, **kwargs)
        except Exception as e:
            log.exception(e)

        if self.cancel_event.is_set():
            self.cancel_event.clear()
        # Don't re-schedule periodic task if cancellation event is set
        elif interval > 0:
            loop = asyncio.get_running_loop()
            loop.call_later(interval, self.add_task, function, *args)

        return out

    @classmethod
    def cancel_task(self):
        self.cancel_event.set()

    @classmethod
    def add_task(self, function, *args, **kwargs):
        """
        Add an on-demand task to be run at the time defined by when,
        that need not repeat and only runs once.
        Usually run with a higher priority (lower priority number means high priority).
        """
        if not self.stop_event or self.stop_event.is_set():
            return None
        interval = getattr(function, "interval", -1)
        pool = getattr(function, "pool", False)
        if not pool:
            return asyncio.create_task(self.__task_wrapper(interval, function, *args, **kwargs))
        else:
            loop = asyncio.get_running_loop()
            pool = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn")
            )
            future = loop.run_in_executor(pool, function, *args)

            # Clean up pool once future is done
            def shutdown_pool(_):
                pool.shutdown(wait=True)

            future.add_done_callback(shutdown_pool)
            return future

    def subprocess(*sp_args, **sp_kwargs):
        # Set defaults only if not already specified
        sp_kwargs.setdefault("stdout", asyncio.subprocess.PIPE)
        sp_kwargs.setdefault("stderr", asyncio.subprocess.PIPE)
        class SubprocessWrapper:
            def __init__(self, args, kwargs):
                self.args = args
                self.kwargs = kwargs
                self.interval = -1  # default for on-demand
                self.pool = False

            def __call__(self, *_, **__):
                # make it awaitable
                return asyncio.create_subprocess_exec(*self.args, **self.kwargs)

        return SubprocessWrapper(sp_args, sp_kwargs)

    @classmethod
    def start(self):
        self.stop_event = asyncio.Event()
        self.cancel_event = asyncio.Event()
        self.stop_event.clear()

    @classmethod
    def stop(self):

        self.stop_event.set()

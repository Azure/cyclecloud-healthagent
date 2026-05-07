
import asyncio
import importlib
import json
import logging
import pickle
import os
import signal
from time import perf_counter
from healthagent.scheduler import Scheduler
from healthagent.reporter import Reporter
from healthagent.profiler import Profiler
from healthagent.config import load_config
from importlib.metadata import version, PackageNotFoundError

try:
    VERSION = version("healthagent")
except PackageNotFoundError:
    VERSION = "unknown"

log = logging.getLogger('healthagent')

class Healthagent:
    """
    Base class for running the healthagent.
    Initializes all the health checks.
    Starts a unix socket based server to listen to any requests
    coming from the health client.
    """

    workdir = os.getenv("HEALTHAGENT_DIR") or "/opt/healthagent"
    rundir = f"{workdir}/run"
    socket = f"{rundir}/health.sock"
    server = None
    modules = {}
    debug_mode = 0

    # Module registry: (module_name, import_path, class_name)
    MODULE_REGISTRY = [
        ("gpu",     "healthagent.gpu",          "GpuHealthChecks"),
        ("systemd", "healthagent.async_systemd", "SystemdMonitor"),
        ("kmsg",    "healthagent.kmsg",          "KmsgReader"),
        ("network", "healthagent.network",       "NetworkHealthChecks"),
        ("proc",    "healthagent.process",       "ProcessMonitor")
    ]

    # TODO: TEMPORARY — Replace with config-file-driven service lists.
    # Base services always monitored regardless of hardware.
    SYSTEMD_BASE_SERVICES = [
        "munge.service",
        "slurmd.service",
        "slurmctld.service",
        "slurmdbd.service",
        "slurmrestd.service",
    ]
    # Additional services monitored only when GPUs are present (gpu module loaded).
    SYSTEMD_GPU_SERVICES = [
        "nvidia-imex.service",
        "nvidia-dcgm.service",
        "nvidia-persistenced.service",
    ]

    @classmethod
    def handler(cls, signum, frame):

        if os.getpid() == cls.pid:
            signame = signal.Signals(signum).name
            log.critical(f'Signal Received {signame} ({signum})')
            Scheduler.stop()
        else:
            # Re-raise the signal to allow the default signal handling behavior (process termination)
            if signum == 15:
                log.debug(f"Child process {os.getpid()} re-raising signal {signum}")
                signal.default_int_handler(signum, frame)

    @classmethod
    def get_module_file(cls, module: str):

        return f"{cls.rundir}/{module}.pkl"

    @classmethod
    def get_reporter(cls, module: str):
        filename = cls.get_module_file(module=module)
        if os.path.exists(filename):
            try:
                with open(filename, 'rb') as f:
                    return Reporter.load_reporter_obj(old=pickle.load(f))
            except Exception as e:
                log.error(e)
                log.error(f"Unable to restore previous state for module {module}")
        return Reporter()

    @classmethod
    def save_reporter(cls):
        for module, obj  in cls.modules.items():
            reporter = obj.reporter
            filename = cls.get_module_file(module=module)
            try:
                with open(filename, 'wb') as f:
                    pickle.dump(reporter, f)
            except Exception as e:
                log.exception(e)


    @classmethod
    async def _execute_module_functions(cls, attribute_flag: str, checks: dict = None):
        response = {}
        for name, module in cls.modules.items():
            response[name] = await module.execute(attribute_flag, checks=checks)
        return response

    @classmethod
    def _list_module_checks(cls, attribute_flag: str = None):
        result = {}
        for name, module in cls.modules.items():
            module_checks = module.list_checks(attribute_flag)
            if module_checks:
                result[name] = module_checks
        return result

    @classmethod
    async def handle_client(cls, reader, writer):

        try:
            data = b''
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    # Client closed connection
                    break
                data += chunk
            message = data.decode()
            log.debug("Received: %s", message)

            request = json.loads(message)
            start = perf_counter()
            command = request.get("command", "")
            checks = request.get("checks", None)

            response = {}
            if command == "epilog":
                response = await cls._execute_module_functions(attribute_flag="epilog", checks=checks)
            elif command == "prolog":
                response = await cls._execute_module_functions(attribute_flag="prolog", checks=checks)
            elif command == "status":
                response = await cls._execute_module_functions(attribute_flag="status")
            elif command == "list_checks":
                check_type = request.get("type", "all")
                flag = None if check_type == "all" else check_type
                response = cls._list_module_checks(attribute_flag=flag)
            elif command == "version":
                response = VERSION
            elif command == "show_config":
                response = cls.config
            else:
                raise ValueError("Invalid message received")

            writer.write(json.dumps(response).encode())
            await writer.drain()
            log.debug(f"{command} Response sent successfully in {perf_counter() - start:.4f} sec")
        except Exception as e:
            log.exception(e)
        writer.close()
        await writer.wait_closed()

    @classmethod
    async def run_unix_server(cls):
        if os.path.exists(cls.socket):
            os.remove(cls.socket)

        cls.server = await asyncio.start_unix_server(cls.handle_client, path=cls.socket)
        os.chmod(cls.socket, 0o660)
        log.debug(f"listening on {cls.socket}")

    @classmethod
    async def stop_server(cls):

        log.debug("Stopping the server")
        start = perf_counter()
        if cls.server:
            cls.server.close()
            await cls.server.wait_closed()
        if os.path.exists(cls.socket):
            os.remove(cls.socket)
        end = perf_counter()
        log.debug(f"Finished closing the server, took: {end - start:.4f} sec")

    @classmethod
    async def initialize_modules(cls):
        cls.config = load_config()
        enabled = cls.config.get("modules", [])
        if not isinstance(enabled, list) or not all(isinstance(m, str) for m in enabled):
            raise ValueError(
                f"'modules' in config must be a list of strings, got: {enabled!r}"
            )

        for module_name, import_path, class_name in cls.MODULE_REGISTRY:
            if module_name not in enabled:
                log.info(f"Module {module_name} disabled by config")
                continue
            try:
                mod = importlib.import_module(import_path)
                instance = getattr(mod, class_name)
                reporter = cls.get_reporter(module=module_name)
                module_config = cls.config.get(module_name, {})
                instance_obj = instance(reporter=reporter, config=module_config)
                await instance_obj.create()
                log.info(f"Initialized module: {module_name}")
            except ImportError as e:
                log.error(f"Module {module_name} unavailable: {e}")
            except Exception as e:
                # GpuNotFoundException is expected on non-GPU nodes; log without traceback
                gpu_exc_names = ("GpuNotFoundException", "GpuHealthChecksException")
                if type(e).__name__ in gpu_exc_names:
                    log.info(f"Module {module_name} skipped: {e}")
                else:
                    log.exception(f"Failed to initialize module {module_name}")
            else:
                cls.modules[module_name] = instance_obj
        await cls.configure_systemd_monitor()

    @classmethod
    def get_systemd_services(cls):
        """TODO: TEMPORARY — Returns the list of systemd services to monitor.
        Replace with config-file-based resolution."""
        services = list(cls.SYSTEMD_BASE_SERVICES)
        if "gpu" in cls.modules:
            services.extend(cls.SYSTEMD_GPU_SERVICES)
        return services

    @classmethod
    async def configure_systemd_monitor(cls):
        """Wire the systemd module with the resolved service list.
        Must be called after initialize_modules()."""
        systemd_module = cls.modules.get("systemd")
        if systemd_module is None:
            log.info("Systemd module not initialized; skipping service monitor setup")
            return
        services = cls.get_systemd_services()
        log.info(f"Configuring systemd monitor for services: {services}")
        await systemd_module.add_monitor(services=services)

    @Scheduler.periodic(60)
    @classmethod
    async def reset_systemd_watchdog(cls):
        '''Periodically notify (aka "pet") the systemd watchdog to indicate healthagent service liveness'''
        from systemd.daemon import notify
        notify("WATCHDOG=1")


    @classmethod
    async def run(cls, debug_mode=False):

        cls.pid = os.getpid()
        log.info(f"Healthagent pid: {cls.pid}")
        log.info(f"Healthagent version: {VERSION}")
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, lambda: cls.handler(signal.SIGINT, None))
        loop.add_signal_handler(signal.SIGTERM, lambda: cls.handler(signal.SIGTERM, None))
        #signal.signal(signalnum=signal.SIGTERM, handler=cls.handler)
        #signal.signal(signalnum=signal.SIGINT, handler=cls.handler)
        Scheduler.start()
        if not os.path.isdir(cls.workdir):
            raise ValueError(f"Invalid workdir: {cls.workdir}")
        if not os.access(cls.workdir, os.W_OK):
            raise PermissionError(f"Workdir is not writable: {cls.workdir}")
        os.makedirs(cls.rundir, exist_ok=True)

        if debug_mode:
            log.info("Running Healthagent in DEBUG Mode")
            cls.profiler = Profiler(pid=cls.pid)
            cls.profiler.start()
        # Periodically indicate liveness to systemd watchdog  (service will be restarted if it misses enough checks)
        Scheduler.add_task(cls.reset_systemd_watchdog)

        await cls.initialize_modules()
        await cls.run_unix_server()
        log.info("Initialized HealthAgent")
        await Scheduler.stop_event.wait()
        await cls.stop_server()
        cls.save_reporter()
        log.info("Exiting")
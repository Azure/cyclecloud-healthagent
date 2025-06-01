
import asyncio
import json
import logging
import pickle
import os
import signal
from time import perf_counter
from healthagent.scheduler import Scheduler
from healthagent.reporter import Reporter
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

    @classmethod
    def handler(self, signum, frame):

        if os.getpid() == self.pid:
            signame = signal.Signals(signum).name
            log.critical(f'Signal Received {signame} ({signum})')
            Scheduler.stop()
        else:
            # Re-raise the signal to allow the default signal handling behavior (process termination)
            if signum == 15:
                log.debug(f"Child process {os.getpid()} re-raising signal {signum}")
                signal.default_int_handler(signum, frame)

    @classmethod
    def get_module_file(self, module: str):

        return f"{self.rundir}/{module}.pkl"

    @classmethod
    def get_reporter(self, module: str):
        filename = self.get_module_file(module=module)
        if os.path.exists(filename):
            try:
                with open(filename, 'rb') as f:
                    return pickle.load(f)
            except Exception as e:
                log.error(e)
                log.error(f"Unable to restore previous state for module {module}")
        return Reporter()

    @classmethod
    def save_reporter(self):
        for module, obj  in self.modules.items():
            reporter = obj.reporter
            filename = self.get_module_file(module=module)
            try:
                with open(filename, 'wb') as f:
                    pickle.dump(reporter, f)
            except Exception as e:
                log.exception(e)

    @classmethod
    async def _execute_module_functions(self, attribute_flag: str, is_async: bool = True):
        response = {}
        for module, obj in self.modules.items():
            response[module] = {}
            for attr_name in dir(obj):
                attr = getattr(obj, attr_name)
                if callable(attr) and getattr(attr, attribute_flag, False):
                    try:
                        ans = await attr() if is_async else attr()
                        if isinstance(ans, dict):
                            response[module].update(ans)
                        else:
                            log.warning(f"[{attribute_flag}] {attr_name} did not return a dictionary. Ignoring its result.")
                    except Exception as e:
                        log.exception(f"[{attribute_flag}] Error while executing {attr_name}: {e}")
        return response

    @classmethod
    async def handle_client(self, reader, writer):

        try:
            data = b''
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    # Client closed connection
                    break
                data += chunk
            message = data.decode()
            log.debug("Recieved: %s", message)
            response = {}
            if message == "epilog":
                log.debug("Received epilog request")
                response = await self._execute_module_functions(attribute_flag="epilog", is_async=True)
                log.debug(f"epilog response: {response}")
            elif message == "status":
                log.debug("Received status request")
                response = await self._execute_module_functions(attribute_flag="status", is_async=False)
                log.debug(f"status response: {response}")
            elif message == "version":
                response = VERSION
                log.debug(f"version response: {response}")
            else:
                raise ValueError("Invalid message received")

            writer.write(json.dumps(response).encode())
            await writer.drain()
        except Exception as e:
            log.exception(e)
        writer.close()
        await writer.wait_closed()

    @classmethod
    async def run_unix_server(self):
        if os.path.exists(self.socket):
            os.remove(self.socket)

        self.server = await asyncio.start_unix_server(self.handle_client, path=self.socket)
        os.chmod(self.socket, 0o660)
        log.debug(f"listening on {self.socket}")

    @classmethod
    async def stop_server(self):

        log.debug("Stopping the server")
        start = perf_counter()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if os.path.exists(self.socket):
            os.remove(self.socket)
        end = perf_counter()
        log.debug(f"Finished closing the server, took: {end - start:.4f} sec")

    @classmethod
    async def initialize_modules(self):
        try:
            from healthagent.gpu import GpuHealthChecks,GpuNotFoundException
        except ImportError as e:
            log.error("Unable to find dcgm python binding, is DCGM 4 installed?")
            log.error("Skipping GPU health checks")
        else:
            try:
                module = "gpu"
                reporter = self.get_reporter(module=module)
                gpu = GpuHealthChecks(reporter=reporter)
                self.modules[module] = gpu
                await gpu.create()
            except GpuNotFoundException as e:
                log.debug(e)
            except Exception as e:
                log.exception(e)

        try:
            from healthagent.async_systemd import SystemdMonitor
            module = "systemd"
            reporter = self.get_reporter(module=module)
            systemd = SystemdMonitor(reporter=reporter)
            self.modules[module] = systemd
            await systemd.create()
            #TODO: Future work
            # Right now list of services are hardcoded, and any service not loaded on a node is automatically ignored.
            # But this list eventually needs to come either through the CLI or through the config file.
            await systemd.add_monitor(services=["munge.service", "slurmd.service", "slurmctld.service", "slurmdbd.service", "slurmrestd.service", "nvidia-imex.service", "nvidia-dcgm.service"])
        except Exception as e:
            log.exception(e)

        try: 
            from healthagent.kmsg import KmsgReader
            module = 'kmsg'
            reporter = self.get_reporter(module=module)
            kmsg_reader = KmsgReader(reporter=reporter)
            self.modules[module] = kmsg_reader
        except Exception as e:
            log.exception(e)
            log.error("kmsg module disabled")

    @Scheduler.periodic(60)
    @classmethod
    async def reset_systemd_watchdog(self):
        '''Periodically notify (aka "pet") the systemd watchdog to indicate healthagent service liveness'''
        from systemd.daemon import notify
        notify("WATCHDOG=1")


    @classmethod
    async def run(self):

        self.pid = os.getpid()
        log.info(f"Healthagent pid: {self.pid}")
        log.info(f"Healthagent version: {VERSION}")
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, lambda: self.handler(signal.SIGINT, None))
        loop.add_signal_handler(signal.SIGTERM, lambda: self.handler(signal.SIGTERM, None))
        #signal.signal(signalnum=signal.SIGTERM, handler=self.handler)
        #signal.signal(signalnum=signal.SIGINT, handler=self.handler)
        Scheduler.start()
        if not os.path.isdir(self.workdir):
            raise ValueError(f"Invalid workdir: {self.workdir}")
        if not os.access(self.workdir, os.W_OK):
            raise PermissionError(f"Workdir is not writable: {self.workdir}")
        os.makedirs(self.rundir, exist_ok=True)

        # Periodically indicate liveness to systemd watchdog  (service will be restarted if it misses enough checks)
        Scheduler.add_task(self.reset_systemd_watchdog)

        await self.initialize_modules()
        await self.run_unix_server()
        log.info("Initialized HealthAgent")
        await Scheduler.stop_event.wait()
        await self.stop_server()
        self.save_reporter()
        log.info("Exiting")
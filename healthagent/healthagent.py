
import asyncio
import json
import logging
import os
import socket
from healthagent.AsyncScheduler import AsyncScheduler
from healthagent.gpu import GpuHealthChecks,GpuHealthChecksException,GpuNotFoundException
from healthagent.async_systemd import SystemdMonitor

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
        log.info(f"listening on {self.socket}")

    @classmethod
    async def stop_server(self):

        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if os.path.exists(self.socket):
            os.remove(self.socket)
        log.debug("Finished closing the server")

    @classmethod
    async def initialize_modules(self):
        try:
            gpu = GpuHealthChecks()
            self.modules['gpu'] = gpu
            await gpu.create()
        except GpuNotFoundException as e:
            log.debug(e)
        except GpuHealthChecksException as e:
            log.exception(e)

        try:
            systemd = SystemdMonitor()
            self.modules['systemd'] = systemd
            await systemd.create()
            #TODO: Future work
            # Right now list of services are hardcoded, and any service not loaded on a node is automatically ignored.
            # But this list eventually needs to come either through the CLI or through the config file.
            await systemd.add_monitor(services=["munge.service", "slurmd.service", "slurmctld.service", "slurmdbd.service"])
        except Exception as e:
            log.exception(e)

    @classmethod
    async def run(self):

        if not os.path.isdir(self.workdir):
            raise ValueError(f"Invalid workdir: {self.workdir}")
        if not os.access(self.workdir, os.W_OK):
            raise PermissionError(f"Workdir is not writable: {self.workdir}")
        os.makedirs(self.rundir, exist_ok=True)

        await self.initialize_modules()
        await AsyncScheduler.start()
        await self.run_unix_server()
        log.info("Initialized HealthAgent")
        await AsyncScheduler.stop_event.wait()
        await self.stop_server()
        log.info("Exiting")

    @classmethod
    def stop(self):

        AsyncScheduler.stop()
import asyncio
import logging
import signal
import os
from healthagent.AsyncScheduler import AsyncScheduler
from healthagent.gpu import GpuHealthChecks,GpuHealthChecksException
from healthagent.async_systemd import SystemdMonitor

log = logging.getLogger(__name__)

def handler(signum, frame):
    signame = signal.Signals(signum).name
    log.critical(f'Signal Received {signame} ({signum})')
    AsyncScheduler.stop()

async def run():

    workdir = os.getenv("HEALTHAGENT_DIR") or "/opt/healthagent"
    #TODO: improve
    # logging needs to come through logging.conf with syslog logging enabled.
    # messages >= ERORR should go to both log file and syslog.
    logging.basicConfig(
        filename=f"{workdir}/healthagent.log",
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=logging.DEBUG
        )
    log.info("Initializing")


    # Right now we are only looking for nvidia devices.
    if not os.path.exists("/dev/nvidia0"):
        log.info("GPU devices not found, skipping GPU checks")
    else:
        try :
            gpu_health_checks = GpuHealthChecks()
            await gpu_health_checks.create()
        except GpuHealthChecksException as e:
            log.exception(e)

    try:
        systemd_monitor = SystemdMonitor()
        await systemd_monitor.create()
        #TODO: Future work
        # Right now list of services are hardcoded, and any service not loaded on a node is automatically ignored.
        # But this list eventually needs to come either through the CLI or through the config file.
        await systemd_monitor.add_monitor(services=["munge.service", "slurmd.service", "slurmctld.service", "slurmdbd.service"])
    except Exception as e:
        log.exception(e)

    await AsyncScheduler.start()
    await AsyncScheduler.stop_event.wait()
    log.info("Exiting")

def main():
    #TODO: improve signal handling, it isnt quite working as expected just yet.
    # Potentially add a 30 second timed exit, and perhaps cancellation tokens,
    # Right now we wait for Async Scheduler to really read the stop_event which depending
    # on the current task could take some time.
    signal.signal(signalnum=signal.SIGTERM, handler=handler)
    signal.signal(signalnum=signal.SIGINT, handler=handler)
    asyncio.run(run())

if __name__ == "__main__":
    main()
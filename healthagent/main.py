import asyncio
import importlib.resources
import logging
import logging.config
from healthagent.healthagent import Healthagent

log = logging.getLogger(__name__)

def main():
    #TODO: improve signal handling, it isnt quite working as expected just yet.
    # Potentially add a 30 second timed exit, and perhaps cancellation tokens,
    # Right now we wait for Async Scheduler to really read the stop_event which depending
    # on the current task could take some time.
    #signal.signal(signalnum=signal.SIGTERM, handler=handler)
    #signal.signal(signalnum=signal.SIGINT, handler=handler)
    with importlib.resources.path('healthagent', 'logging.conf') as config_path:
        logging.config.fileConfig(config_path)
    asyncio.run(Healthagent.run())

if __name__ == "__main__":
    main()
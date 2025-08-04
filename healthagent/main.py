import asyncio
import os
import importlib.resources
import logging
import logging.config
import tracemalloc
from healthagent.healthagent import Healthagent

log = logging.getLogger(__name__)

def main():

    with importlib.resources.path('healthagent', 'logging.conf') as config_path:
        logging.config.fileConfig(config_path)
    debug_mode = False
    if os.getenv("DEBUG_MODE") == "1":
        debug_mode = True
    asyncio.run(Healthagent.run(debug_mode=debug_mode))

if __name__ == "__main__":
    main()
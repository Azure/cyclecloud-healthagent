import tracemalloc

from healthagent.scheduler import Scheduler
import logging

log = logging.getLogger('healthagent')


class Profiler:
    """
    Internal profiling and memory diagnostics.
    Not a health check — used for troubleshooting only.
    Used in debug mode to periodically log memory usage and shared library information.
    """

    def __init__(self, pid: int):
        self.pid = pid

    def start(self):
        """Register all profiling tasks with the scheduler."""
        Scheduler.add_task(self.profile_memory)
        Scheduler.add_task(self.monitor_memory_usage)
        Scheduler.add_task(self.monitor_shared_libraries)


    @Scheduler.periodic(120)
    async def profile_memory(self):

        if not tracemalloc.is_tracing():
            # collect 25 frames
            tracemalloc.start(25)

        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics("lineno")

        log.debug(f"[tracemalloc] Top {len(top_stats[:10])} allocations:")
        for stat in top_stats[:10]:
            log.debug(stat)

    @Scheduler.periodic(120)
    async def monitor_memory_usage(self):
        """
        Monitor RSS memory usage using /proc
        """
        try:

            # Read from /proc/PID/status
            with open(f'/proc/{self.pid}/status', 'r') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        # Extract RSS in kB and convert to MB
                        rss_kb = int(line.split()[1])
                        rss_mb = rss_kb / 1024

                        log.debug(f"[Memory Monitor] PID: {self.pid}, RSS: {rss_mb:.2f} MB")


                        break

        except Exception as e:
            log.exception(f"Unexpected error monitoring memory: {e}")

    @Scheduler.periodic(300)
    async def monitor_shared_libraries(self):
        """
        Monitor shared libraries memory usage by reading /proc/PID/smaps.
        Reports top 10 shared libraries sorted by Anonymous memory usage.
        """
        try:
            smaps_path = f'/proc/{self.pid}/smaps'

            library_memory = {}
            current_mapping = None
            current_rss = 0
            current_anonymous = 0

            with open(smaps_path, 'r') as f:
                for line in f:
                    line = line.strip()

                    # Check if this is a new memory mapping (starts with address range)
                    if '-' in line and (line[0].isdigit() or line[0].lower() in 'abcdef'):
                        # Save previous mapping if it was a shared library
                        if current_mapping and (current_mapping.endswith('.so') or '.so.' in current_mapping):
                            if current_mapping not in library_memory:
                                library_memory[current_mapping] = {'rss': 0, 'anonymous': 0}
                            library_memory[current_mapping]['rss'] += current_rss
                            library_memory[current_mapping]['anonymous'] += current_anonymous

                        # Parse new mapping
                        parts = line.split()
                        if len(parts) >= 6:
                            current_mapping = parts[5]  # Path/filename
                            current_rss = 0
                            current_anonymous = 0
                        else:
                            current_mapping = None
                            current_rss = 0
                            current_anonymous = 0

                    # Parse RSS line
                    elif line.startswith('Rss:'):
                        rss_kb = int(line.split()[1])
                        current_rss += rss_kb

                    # Parse Anonymous line
                    elif line.startswith('Anonymous:'):
                        anon_kb = int(line.split()[1])
                        current_anonymous += anon_kb

            # Handle the last mapping
            if current_mapping and (current_mapping.endswith('.so') or '.so.' in current_mapping):
                if current_mapping not in library_memory:
                    library_memory[current_mapping] = {'rss': 0, 'anonymous': 0}
                library_memory[current_mapping]['rss'] += current_rss
                library_memory[current_mapping]['anonymous'] += current_anonymous

            # Sort libraries by Anonymous memory usage and get top 10
            top_libraries = sorted(library_memory.items(), key=lambda x: x[1]['anonymous'], reverse=True)[:10]

            if top_libraries:
                log.debug(f"[Shared Libraries] Top 10 shared libraries by Anonymous memory usage:")
                log.debug(f"[Shared Libraries] {'Library':<40} {'RSS (MB)':<10} {'Anonymous (MB)':<15}")
                log.debug(f"[Shared Libraries] {'-' * 70}")

                total_lib_rss = 0
                total_lib_anonymous = 0
                for i, (lib_path, memory_stats) in enumerate(top_libraries, 1):
                    rss_mb = memory_stats['rss'] / 1024
                    anon_mb = memory_stats['anonymous'] / 1024
                    total_lib_rss += memory_stats['rss']
                    total_lib_anonymous += memory_stats['anonymous']

                    # Extract just the library name from full path
                    lib_name = lib_path.split('/')[-1] if '/' in lib_path else lib_path
                    log.debug(f"[Shared Libraries] {i:2d}. {lib_name:<37} {rss_mb:<10.2f} {anon_mb:<15.2f}")

                total_rss_mb = total_lib_rss / 1024
                total_anon_mb = total_lib_anonymous / 1024
                log.debug(f"[Shared Libraries] {'-' * 70}")
                log.debug(f"[Shared Libraries] Total shared library RSS: {total_rss_mb:.2f} MB")
                log.debug(f"[Shared Libraries] Total shared library Anonymous: {total_anon_mb:.2f} MB")
            else:
                log.debug("[Shared Libraries] No shared libraries found in memory mappings")

        except Exception as e:
            log.exception(f"Error monitoring shared libraries: {e}")
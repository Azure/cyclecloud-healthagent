import os
import asyncio
import time
from enum import Enum
from collections import deque
from datetime import timedelta, datetime, timezone
from healthagent import status
from healthagent.reporter import Reporter, HealthReport, HealthStatus
from healthagent.scheduler import Scheduler
from functools import partial
import logging

log = logging.getLogger(__name__)

class KmsgReader:

    def __init__(self, reporter: Reporter):

        self.reporter = reporter
        try:
            self.fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
        except Exception as e:
            log.exception("Failed to open /dev/kmsg")
            raise
        loop = asyncio.get_running_loop()
        loop.add_reader(self.fd, self.read_callback)
        self.name = "KernelMonitor"
        Scheduler.add_task(self.clear_errors)

    def __del__(self):
        if self.fd >= 0:
            os.close(self.fd)

    def boot_time(self):
        # Seconds since epoch - uptime
        with open('/proc/uptime') as f:
            uptime_seconds = float(f.readline().split()[0])
        now = time.time()
        return datetime.fromtimestamp(now - uptime_seconds)

    @Scheduler.periodic(300)
    async def clear_errors(self):

        # This only clears all errors if in the last hour we have received no other alert
        await self.reporter.clear_all_errors(timedelta(hours=1))


    def parse_kmsg_line(self, line):
        """
        Example format:
        "6,1234,45678901,-;Some message here"
        ^ ^     ^       ^
        | |     |       +-- Message
        | |     +---------- Timestamp
        | +----------------- Sequence #
        +------------------- Log level
        """
        try:
            level, seq, usec_since_boot, flags_msg = line.split(",", 3)
            walltime = self.boot_time() + timedelta(microseconds=int(usec_since_boot))
            level = int(level)
            msg = flags_msg.split(';', 1)[-1]
            return walltime, level, msg
        except Exception:
            return None, None, None

    def get_level(self, level):

        if level == 0 :
            return "KERNEL EMERGENCY"
        elif level == 1:
            return "KERNEL ALERT"
        elif level == 2:
            return "KERNEL CRITICAL"
        elif level == 3:
            return "KERNEL ERROR"
        elif level == 4:
            return "KERNEL WARNING"
        elif level ==  5:
            return "KERNEL NOTICE"
        else:
            return f"LEVEL{level}"

    def read_callback(self):
        """
        Kernel Log levels:

        KERN_EMERG  "0"
        KERN_ALERT  "1"
        KERN_CRIT   "2"
        """
        report = self.reporter.get_report(name=self.name)
        formatted_msg = []
        try:
            while True:
                data = os.read(self.fd, 4096).decode(errors='ignore')
                if not data:
                    break
                for line in data.strip().splitlines():
                    walltime, level, msg = self.parse_kmsg_line(line)
                    # ignore error messages older than an hour
                    if walltime is not None and walltime < datetime.now() - timedelta(hours=1):
                        continue
                    # read level 0 and 1
                    if level is not None and (level <= 2):
                        # Keep it at warning level for now, actually these should be Errors.
                        report.status = HealthStatus.WARNING
                        level_str = self.get_level(level=level)
                        timestamp = walltime.strftime("%Y-%m-%dT%H:%M:%S %Z")
                        formatted_msg.append(f"{timestamp} - {level_str} - {msg}")

        except BlockingIOError:
            pass
        if not formatted_msg:
            return

        if report.details:
            report.details += "\n" + "\n".join(formatted_msg)
        else:
            report.details = "\n".join(formatted_msg)
        report.message = "KernelMonitor Detected Alerts"
        report.description = "Kernel Log Monitor reports Critical/Emergency Alerts"

        Scheduler.add_task(self.reporter.update_report, self.name, report)

    @status
    def show_status(self):
        return self.reporter.summarize()
import os
import re
import asyncio
import time
from dataclasses import dataclass, field
from healthagent import healthcheck
from datetime import timedelta, datetime
from healthagent.reporter import Reporter, HealthStatus, HealthReport
from healthagent.scheduler import Scheduler
from healthagent.healthmodule import HealthModule
from healthagent.config import ModuleConfig, KmsgConfig, KmsgPatternCheck
from healthagent.util import evaluate
import logging

log = logging.getLogger(__name__)

# Raw matching lines retained per entry in the report.
SAMPLES_CAP = 5


@dataclass
class msgbucket:
    # Exact kernel level this bucket matches; -1 marks a regex (pattern) bucket.
    _level: int = -1
    count: int = 0
    first: datetime = None
    samples: list = field(default_factory=list)
    pattern_eval: KmsgPatternCheck = None
    status: HealthStatus = HealthStatus.OK

    def __post_init__(self):
        self._regex = re.compile(self.pattern_eval.pattern) if self.pattern_eval else None

    def display_threshold(self):
        """Threshold matching the current severity, rendered as the stable count."""
        if self.pattern_eval is None:
            return 1
        if self.status == HealthStatus.ERROR:
            return self.pattern_eval.error
        return self.pattern_eval.warning

    def match_criteria(self, walltime, level, msg):
        """Test one kernel line against this bucket; record and score it on a hit."""
        if self.pattern_eval is None:
            if level != self._level:
                return False
        elif not self._regex.search(msg):
            return False

        self.count += 1
        if self.first is None:
            self.first = walltime
        if len(self.samples) < SAMPLES_CAP:
            self.samples.append(f"{walltime.strftime('%Y-%m-%dT%H:%M:%S')} - {msg}")

        if self.pattern_eval is None:
            # Levels 0-2 are auto-flagged as Error.
            self.status = HealthStatus.ERROR
        else:
            for severity, threshold in (
                (HealthStatus.ERROR, self.pattern_eval.error),
                (HealthStatus.WARNING, self.pattern_eval.warning),
            ):
                if threshold is not None and evaluate(self.pattern_eval.eval, self.count, threshold)[0]:
                    if severity > self.status:
                        self.status = severity
                    break
        return True



class KmsgReader(HealthModule):

    RESERVED_LEVELS = {"KERNEL_EMERGENCY", "KERNEL_ALERT", "KERNEL_CRITICAL"}

    def __init__(self, reporter: Reporter, config: 'ModuleConfig | None' = None):

        super().__init__(reporter, config)
        self.config: KmsgConfig = self.config
        self.fd = -1
        self.buckets = self._build_buckets()
        try:
            self.fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
        except Exception as e:
            log.exception("Failed to open /dev/kmsg")
            raise
        loop = asyncio.get_running_loop()
        loop.add_reader(self.fd, self.read_callback)

    def _build_buckets(self):
        """One msgbucket per configured pattern plus the reserved severe-level buckets."""
        buckets = {}
        for name, check in getattr(self.config, "patterns", {}).items():
            buckets[name] = msgbucket(pattern_eval=check)
        # Kernel levels 0-2 are auto-flagged as Error under reserved bucket names.
        for lvl in (0, 1, 2):
            buckets[self.get_level(lvl)] = msgbucket(_level=lvl)
        return buckets

    def get_level(self, level):

        if level == 0 :
            return "KERNEL_EMERGENCY"
        elif level == 1:
            return "KERNEL_ALERT"
        elif level == 2:
            return "KERNEL_CRITICAL"
        elif level == 3:
            return "KERNEL_ERROR"
        elif level == 4:
            return "KERNEL_WARNING"
        elif level ==  5:
            return "KERNEL_NOTICE"
        else:
            return f"LEVEL{level}"

    def __del__(self):
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)


    def boot_time(self):
        # Seconds since epoch - uptime
        with open('/proc/uptime') as f:
            uptime_seconds = float(f.readline().split()[0])
        now = time.time()
        return datetime.fromtimestamp(now - uptime_seconds)

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


    def _ingest(self, walltime, level, msg):
        for bucket in self.buckets.values():
            bucket.match_criteria(walltime, level, msg)

    @healthcheck("KernelLogCheck", description="Monitor kernel log for critical messages")
    def read_callback(self):
        cutoff = datetime.now() - timedelta(hours=1)
        try:
            while True:
                data = os.read(self.fd, 4096).decode(errors='ignore')
                if not data:
                    break
                for line in data.strip().splitlines():
                    walltime, level, msg = self.parse_kmsg_line(line)
                    if walltime is None:
                        continue
                    # ignore messages older than an hour (kmsg replays the ring buffer)
                    if walltime < cutoff:
                        continue
                    self._ingest(walltime, level, msg)
        except BlockingIOError:
            pass

        report = self._build_report()
        Scheduler.add_task(self.reporter.update_report, self.read_callback.report_name, report)

    def _build_report(self):
        """Assemble the aggregate KernelLogCheck report from current state."""
        report = HealthReport()
        custom_fields = {}
        errors = []
        warnings = []

        for name, b in self.buckets.items():
            if b.status == HealthStatus.OK or b.count == 0:
                continue
            report.escalate(b.status)
            entry = {
                "severity": b.status.value,
                "count": f">={b.display_threshold()}",
                "first_seen": b.first,
                "samples": list(b.samples),
            }
            resolved_msg = name
            if b.pattern_eval is not None:
                entry["pattern"] = b.pattern_eval.pattern
                if b.pattern_eval.category:
                    entry["category"] = b.pattern_eval.category
                if b.pattern_eval.msg:
                    resolved_msg = b.pattern_eval.msg
            custom_fields[name] = entry
            (errors if b.status == HealthStatus.ERROR else warnings).append((name, resolved_msg, b))

        if report.status == HealthStatus.OK:
            return report

        report.custom_fields = custom_fields
        report.message = "KernelLogCheck detected alerts"
        report.description = "Critical kernel messages and/or watched patterns matched"
        report.details = self._format_details(errors, warnings)
        return report

    def _format_details(self, errors, warnings):
        """Human-readable block for the CycleCloud UI (first 5 samples per bucket)."""
        lines = []
        for title, items in (("Errors", errors), ("Warnings", warnings)):
            if not items:
                continue
            lines.append(f"=== {title} ===")
            for name, resolved_msg, b in items:
                lines.append(f"  {name}: {resolved_msg}")
                lines.append(f"      Hit count: >={b.display_threshold()}")
                lines.append(f"      first_seen: {b.first}")
                for sample in b.samples:
                    lines.append(f"        {sample}")
        return "\n".join(lines)
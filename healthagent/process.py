import os
import pwd
import logging
from healthagent import epilog, healthcheck
from healthagent.scheduler import Scheduler
from healthagent.healthmodule import HealthModule
from healthagent.reporter import Reporter, HealthStatus, HealthReport

log = logging.getLogger(__name__)


class ProcessMonitor(HealthModule):
    """
    Monitors /proc for zombie and unkillable (hung) processes.

    Zombie detection uses two tiers:
      - WARNING: zombie count exceeds an adaptive threshold scaled by CPU cores
        and pid_max — min(cores * ZOMBIE_PER_CORE, pid_max * PID_MAX_WARN_PCT%).
      - ERROR: zombies consume >= PID_SATURATION_PCT% of PID space.

    Hung task detection:
      - Processes in D-state (uninterruptible sleep) with pending SIGKILL/SIGTERM
        at the process level (ShdPnd) are unkillable by the OS — reported as ERROR.
      - The kernel wait channel (wchan) is included to show what the task is blocked on.
    """

    ZOMBIE_PER_CORE = 50
    PID_MAX_WARN_PCT = 10           # warn at 10% of pid_max
    PID_SATURATION_PCT = 50         # error at 50% of pid_max

    def __init__(self, reporter: Reporter, config: dict | None = None):
        super().__init__(reporter, config)
        self.pid_max = self._read_pid_max()
        self.cpu_count = os.cpu_count() or 1
        self.zombie_warn_threshold = min(
            self.cpu_count * self.ZOMBIE_PER_CORE,
            self.pid_max * self.PID_MAX_WARN_PCT // 100
        )

    @staticmethod
    def _read_pid_max() -> int:
        try:
            with open("/proc/sys/kernel/pid_max", "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return 32768

    def list_pids():
        # filter numeric dirs
        for name in os.listdir("/proc"):
            if name.isdigit():
                yield int(name)

    @staticmethod
    def process_signal_mask(shdpnd: str, sigq: str) -> bool:
        """
        Check process-level pending signals to determine if a process is hung.
        If SIGKILL or SIGTERM is pending at the process level (ShdPnd),
        the process is unkillable — declare it hung.
        Returns True if the process is hung.
        """
        SIGKILL_MASK = 1 << (9 - 1)   # bit 8 = signal 9
        SIGTERM_MASK = 1 << (15 - 1)   # bit 14 = signal 15

        pending = int(shdpnd, 16)
        return bool(pending & (SIGKILL_MASK | SIGTERM_MASK))

    @staticmethod
    def _read_wchan(pid: int) -> str:
        try:
            with open(f"/proc/{pid}/wchan", "r") as f:
                return f.read().strip() or "unknown"
        except (OSError, ValueError):
            return "unknown"

    async def create(self):
        Scheduler.add_task(self.monitor)

    @healthcheck("ProcessStateCheck", description="Detect zombie and unkillable processes")
    @epilog
    @Scheduler.periodic(60)
    async def monitor(self):
        """
        Reads and iterate over the /proc/<pid>/status file for all pids from list_pids.
        The status file format is

        Name:   ksoftirqd/3
        Umask:  0000
        State:  S (sleeping)

        If the State is 'D' or 'Z', stores the pid.
        Also checks the signal bitmasks to identify pending and queued signals.
        """

        zombieproc = []
        name = self.monitor.report_name
        report = HealthReport()
        hungprocs = []
        for pid in ProcessMonitor.list_pids():
            try:
                status_path = f"/proc/{pid}/status"
                state = None
                shdpnd = None
                sigq = None
                proc_name = None
                uid = None
                with open(status_path, 'r') as f:
                    for line in f:
                        if line.startswith('Name:'):
                            proc_name = line.split(':', 1)[1].strip()
                        elif line.startswith('State:'):
                            state = line.split()[1]
                        elif line.startswith('Uid:'):
                            uid = int(line.split()[1])
                        elif line.startswith('ShdPnd:'):
                            shdpnd = line.split()[1]
                        elif line.startswith('SigQ:'):
                            sigq = line.split()[1]
                try:
                    username = pwd.getpwuid(uid).pw_name
                except (KeyError, TypeError):
                    username = str(uid)

                if state == 'D':
                    if shdpnd and sigq and ProcessMonitor.process_signal_mask(shdpnd, sigq):
                        wchan = ProcessMonitor._read_wchan(pid)
                        hungprocs.append((pid, proc_name, username, wchan))
                elif state == 'Z':
                    zombieproc.append((pid, proc_name, username))
            except Exception as e:
                # Process may have exited between listing and reading
                log.error(e)
                continue

        zombie_count = len(zombieproc)
        pid_usage_pct = (zombie_count / self.pid_max) * 100

        msgs = []

        # PID space critically consumed — fork bomb aftermath or runaway leak
        if pid_usage_pct >= self.PID_SATURATION_PCT:
            report.escalate(HealthStatus.ERROR)
            report.description = "Critical PID table saturation by zombie processes"
            report.recommendations = "Node Reboot required"
            msgs.append(f"Zombie processes consuming {pid_usage_pct:.1f}% of PID space "
                        f"({zombie_count}/{self.pid_max})")

        # High zombie count relative to node size
        elif zombie_count >= self.zombie_warn_threshold:
            report.escalate(HealthStatus.WARNING)
            report.description = "Zombie processes found in the pid table"
            msgs.append(f"Zombie Processes found: {zombie_count} "
                        f"(threshold: {self.zombie_warn_threshold}, "
                        f"pid_max: {self.pid_max})")

        if hungprocs:
            report.escalate(HealthStatus.ERROR)
            msgs.append(f"Unkillable Processes found: {len(hungprocs)}")
            for pid, proc, user, wchan in hungprocs:
                msgs.append(f"PID: {pid}\tProcess: {proc}\tUser: {user}\tBlocked on: {wchan}")
            report.description = "Unkillable hung processes found"
            report.recommendations = "Node Reboot required"
        report.details = "\n".join(msgs)
        await self.reporter.update_report(name=name, report=report)
        response = {}
        response[name] = report.view()
        return response
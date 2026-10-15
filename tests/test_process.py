import builtins
import io
from types import SimpleNamespace

from healthagent.process import ProcessMonitor
from healthagent.reporter import HealthStatus, Reporter


async def test_monitor_reports_hung_process_in_custom_fields(monkeypatch):
    pid = 4321
    status = """\
Name:\tmkdir
State:\tD (disk sleep)
Uid:\t0\t0\t0\t0
ShdPnd:\t0000000000004000
SigQ:\t1/12345
"""
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == f"/proc/{pid}/status":
            return io.StringIO(status)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(ProcessMonitor, "list_pids", lambda: iter([pid]))
    monkeypatch.setattr(ProcessMonitor, "_read_wchan", lambda process_id: "wait_on_page_bit")
    monkeypatch.setattr(
        "healthagent.process.pwd.getpwuid",
        lambda uid: SimpleNamespace(pw_name="root"),
    )

    monkeypatch.setenv("PUBLISH_CC", "false")
    reporter = Reporter()
    monitor = ProcessMonitor(reporter)
    await monitor.monitor()

    report = reporter.get_report("ProcessStateCheck")
    expected_process = {
        "pid": pid,
        "process": "mkdir",
        "user": "root",
        "blocked_on": "wait_on_page_bit",
    }
    assert report.status is HealthStatus.ERROR
    assert report.custom_fields["HungProcesses"] == [expected_process]
    assert report.details == (
        "Unkillable Processes found: 1\n"
        f"PID: {pid}\tProcess: mkdir\tUser: root\tBlocked on: wait_on_page_bit"
    )
    assert report.view()["HungProcesses"] == [expected_process]
    assert "details" not in report.view()


def test_format_details_from_zombie_custom_fields():
    custom_fields = {
        "ZombieProcesses": {
            "count": 100,
            "pid_usage_pct": 10.0,
            "pid_max": 1000,
            "warning_threshold": 50,
        }
    }

    assert ProcessMonitor._format_details(custom_fields) == (
        "Zombie Processes found: 100 "
        "(10.0% of PID space, threshold: 50, pid_max: 1000)"
    )
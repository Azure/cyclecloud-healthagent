"""Tests for SystemdMonitor aggregated report logic.

These tests validate the aggregation behavior without requiring dbus or systemd.
We subclass SystemdMonitor to stub out get_journal_entries and skip create().
"""
from unittest.mock import AsyncMock
from healthagent.reporter import Reporter, HealthStatus
from healthagent.async_systemd import SystemdMonitor


class FakeSystemdMonitor(SystemdMonitor):
    """Testable SystemdMonitor that stubs journal access and skips dbus init."""

    def __init__(self, reporter):
        super().__init__(reporter)
        self.journal_entries = {}

    async def create(self):
        # Skip dbus connection
        pass

    def get_journal_entries(self, service_name):
        return self.journal_entries.get(service_name, "")


async def test_single_service_failure():
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)
    mon.journal_entries["slurmd.service"] = "[2026-03-20] error: something broke\n"

    await mon.set_current_state("slurmd.service", "active", "running")
    await mon.set_current_state("slurmd.service", "failed", "failed")

    report = reporter.update_report.call_args[1]["report"]
    assert report.status == HealthStatus.ERROR
    assert "slurmd.service" in report.description
    assert "unhealthy" in report.description
    assert "slurmd.service" in report.details
    assert "something broke" in report.details
    assert reporter.update_report.call_args[1]["name"] == "SystemdServiceCheck"


async def test_multiple_service_failures():
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)
    mon.journal_entries["slurmd.service"] = "[2026-03-20] error: slurmd died\n"
    mon.journal_entries["munge.service"] = "[2026-03-20] error: munge auth fail\n"

    await mon.set_current_state("slurmd.service", "active", "running")
    await mon.set_current_state("munge.service", "active", "running")
    await mon.set_current_state("slurmd.service", "failed", "failed")
    await mon.set_current_state("munge.service", "failed", "failed")

    report = reporter.update_report.call_args[1]["report"]
    assert report.status == HealthStatus.ERROR
    assert "slurmd.service" in report.description
    assert "munge.service" in report.description
    # Both journal sections should appear with separators
    assert "------ slurmd.service ------" in report.details
    assert "------ munge.service ------" in report.details
    assert "slurmd died" in report.details
    assert "munge auth fail" in report.details


async def test_service_recovery():
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)
    mon.journal_entries["slurmd.service"] = "[2026-03-20] error: slurmd died\n"

    # Go through: inactive -> active -> failed -> active (recovery)
    await mon.set_current_state("slurmd.service", "active", "running")
    await mon.set_current_state("slurmd.service", "failed", "failed")
    await mon.set_current_state("slurmd.service", "active", "running")

    report = reporter.update_report.call_args[1]["report"]
    assert report.status == HealthStatus.OK
    assert report.description is None
    assert report.details is None


async def test_custom_fields_contain_per_service_status():
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)
    mon.journal_entries["slurmd.service"] = "[2026-03-20] error: slurmd died\n"

    await mon.set_current_state("slurmd.service", "active", "running")
    await mon.set_current_state("munge.service", "active", "running")
    await mon.set_current_state("slurmd.service", "failed", "failed")

    report = reporter.update_report.call_args[1]["report"]
    assert report.custom_fields["slurmd.service"]["status"] == "Error"
    assert report.custom_fields["munge.service"]["status"] == "OK"
    assert report.custom_fields["error_count"] == 1


async def test_no_report_on_transient_state():
    """Transient states like activating should not trigger an aggregated report update."""
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)

    await mon.set_current_state("slurmd.service", "activating", "start")
    # Transient state should NOT update self.state or call reporter.update_report
    reporter.update_report.assert_not_called()
    assert "slurmd.service" not in mon.state


async def test_same_state_no_update():
    """Duplicate state transitions should not trigger a report update."""
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)

    await mon.set_current_state("slurmd.service", "active", "running")
    reporter.update_report.reset_mock()
    # Same state again
    await mon.set_current_state("slurmd.service", "active", "running")
    reporter.update_report.assert_not_called()


async def test_report_name_matches_healthcheck_decorator():
    """The report name used in update_report must match the @healthcheck decorator name."""
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)

    await mon.set_current_state("slurmd.service", "active", "running")
    await mon.set_current_state("slurmd.service", "failed", "failed")

    name_arg = reporter.update_report.call_args[1]["name"]
    assert name_arg == "SystemdServiceCheck"
    # Verify it matches the @healthcheck decorator
    assert name_arg == mon.update_services.report_name


async def test_partial_recovery_still_error():
    """If one service recovers but another is still failed, aggregate should be ERROR."""
    reporter = Reporter()
    reporter.update_report = AsyncMock()
    mon = FakeSystemdMonitor(reporter)
    mon.journal_entries["munge.service"] = "[2026-03-20] error: munge fail\n"

    await mon.set_current_state("slurmd.service", "active", "running")
    await mon.set_current_state("munge.service", "active", "running")
    await mon.set_current_state("slurmd.service", "failed", "failed")
    await mon.set_current_state("munge.service", "failed", "failed")
    # slurmd recovers, munge still failed
    await mon.set_current_state("slurmd.service", "active", "running")

    report = reporter.update_report.call_args[1]["report"]
    assert report.status == HealthStatus.ERROR
    assert "munge.service" in report.description
    assert "slurmd.service" not in report.description
    assert report.custom_fields["slurmd.service"]["status"] == "OK"
    assert report.custom_fields["munge.service"]["status"] == "Error"
    assert report.custom_fields["error_count"] == 1

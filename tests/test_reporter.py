from healthagent.reporter import *
from healthagent.healthagent import Healthagent
import json
import os
import pytest
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import enum

def test_healthstatus_ordering():
    # Severity order: NA < OK < WARNING < ERROR
    assert HealthStatus.NA < HealthStatus.OK
    assert HealthStatus.OK < HealthStatus.WARNING
    assert HealthStatus.WARNING < HealthStatus.ERROR

    assert HealthStatus.ERROR > HealthStatus.WARNING
    assert HealthStatus.WARNING > HealthStatus.OK
    assert HealthStatus.OK > HealthStatus.NA

    assert HealthStatus.OK >= HealthStatus.OK
    assert HealthStatus.ERROR >= HealthStatus.WARNING
    assert HealthStatus.OK <= HealthStatus.WARNING

    # max() picks the most severe
    assert max(HealthStatus.OK, HealthStatus.ERROR) == HealthStatus.ERROR
    assert max(HealthStatus.WARNING, HealthStatus.OK) == HealthStatus.WARNING
    assert max(HealthStatus.NA, HealthStatus.OK) == HealthStatus.OK


def test_healthstatus_enum_lookups():
    # Reverse value lookup works
    assert HealthStatus('OK') is HealthStatus.OK
    assert HealthStatus('Error') is HealthStatus.ERROR
    # Name-based lookup works
    assert HealthStatus['WARNING'] is HealthStatus.WARNING
    # .value returns the display string, not the tuple
    assert HealthStatus.OK.value == 'OK'
    assert HealthStatus.ERROR.value == 'Error'


def test_healthreport_escalate():
    report = HealthReport()
    assert report.status == HealthStatus.OK
    # escalate to WARNING
    report.escalate(HealthStatus.WARNING)
    assert report.status == HealthStatus.WARNING
    # escalate to ERROR
    report.escalate(HealthStatus.ERROR)
    assert report.status == HealthStatus.ERROR
    # attempting to "downgrade" to WARNING is ignored
    report.escalate(HealthStatus.WARNING)
    assert report.status == HealthStatus.ERROR
    # attempting to "downgrade" to OK is ignored
    report.escalate(HealthStatus.OK)
    assert report.status == HealthStatus.ERROR
    # Test blanket assignments, these should still work
    report.status = HealthStatus.WARNING
    assert report.status == HealthStatus.WARNING
    report.status = HealthStatus.OK
    assert report.status == HealthStatus.OK


def test_healthreport():

    ok_report =  HealthReport()
    ok_report2 = HealthReport()
    # check equality does not compare timestamps
    assert ok_report == ok_report2
    assert ok_report.status == HealthStatus.OK

    custom_fields = {}
    custom_fields['error_count']=10
    custom_fields['test_type'] = "software"
    custom_fields['test_name'] = ['software', 'gpu']
    error_report1 = HealthReport(status=HealthStatus.ERROR, description="failed_test_description", custom_fields=custom_fields)

    assert error_report1.status == HealthStatus.ERROR
    assert error_report1.error_count == 10
    assert error_report1.test_type == "software"
    # create a copy of the previous error report
    error_report2 = HealthReport(status=HealthStatus.ERROR, description="failed_test_description", custom_fields=custom_fields)
    # assert equality does not compare timestamps
    assert error_report1 == error_report2


def test_reporter_state_json_round_trip():

    custom_fields = {}
    custom_fields['error_count']=10
    custom_fields['test_type'] = "software"
    custom_fields['test_name'] = ['software', 'gpu']
    categories = set()
    categories.add('integration')
    categories.add('epilog')
    custom_fields['categories'] = categories
    custom_fields[0] = {"categories": ("hardware", "gpu")}
    error_report = HealthReport(
        status=HealthStatus.ERROR,
        description="failed_test_description",
        custom_fields=custom_fields,
        aux_data={0: {"raw": "diagnostic output"}},
        ghr_category=GHRCategory.GENERIC_FAILURE,
    )
    reporter = Reporter()
    reporter.store["gpu"] = error_report

    serialized = json.dumps(reporter.to_state_dict("2.0.2"), allow_nan=False)
    restored = Reporter.from_state_dict(json.loads(serialized))
    report = restored.store["gpu"]

    assert report.status is HealthStatus.ERROR
    assert report.last_update == error_report.last_update
    assert report.custom_fields == custom_fields
    assert report.aux_data == error_report.aux_data
    assert report.ghr_category is GHRCategory.GENERIC_FAILURE

def test_healthagent_saves_and_loads_reporter_json(tmp_path, monkeypatch):
    monkeypatch.setattr(Healthagent, "rundir", str(tmp_path))
    reporter = Reporter()
    reporter.store["gpu"] = HealthReport(
        status=HealthStatus.WARNING,
        custom_fields={0: {"categories": {"XID", "PCIe"}}},
    )
    monkeypatch.setattr(Healthagent, "modules", {"gpu": SimpleNamespace(reporter=reporter)})

    Healthagent.save_reporter()

    state_file = tmp_path / "gpu.json"
    assert state_file.exists()
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    restored = Healthagent.get_reporter("gpu")
    assert restored.store == reporter.store
    assert not list(tmp_path.glob("*.tmp"))

def test_healthagent_ignores_legacy_pickle_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Healthagent, "rundir", str(tmp_path))
    (tmp_path / "gpu.pkl").write_bytes(b"cos\nsystem\n(S'false'\ntR.")

    restored = Healthagent.get_reporter("gpu")

    assert restored.store == {}

def test_healthagent_rejects_malformed_json_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Healthagent, "rundir", str(tmp_path))
    state_file = tmp_path / "gpu.json"
    state_file.write_text("{not valid json", encoding="utf-8")
    state_file.chmod(0o600)

    restored = Healthagent.get_reporter("gpu")

    assert restored.store == {}

def test_healthagent_rejects_fifo_state_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "gpu.json", mode=0o600)
    script = (
        "import sys; "
        "from healthagent.healthagent import Healthagent; "
        "Healthagent.rundir = sys.argv[1]; "
        "assert Healthagent.get_reporter('gpu').store == {}"
    )

    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=True,
        timeout=2,
    )

def test_healthagent_rejects_insecure_reporter_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Healthagent, "rundir", str(tmp_path))
    state_file = tmp_path / "gpu.json"
    state_file.write_text(json.dumps({"schema_version": 1, "reports": {}}))
    state_file.chmod(0o666)

    restored = Healthagent.get_reporter("gpu")

    assert restored.store == {}

def test_healthagent_prepares_private_runtime_directory(tmp_path, monkeypatch):
    rundir = tmp_path / "run"
    monkeypatch.setattr(Healthagent, "workdir", str(tmp_path))
    monkeypatch.setattr(Healthagent, "rundir", str(rundir))

    Healthagent.prepare_runtime_directory()

    assert stat.S_IMODE(rundir.stat().st_mode) == 0o700

def test_healthagent_rejects_insecure_workdir(tmp_path, monkeypatch):
    tmp_path.chmod(0o777)
    monkeypatch.setattr(Healthagent, "workdir", str(tmp_path))
    monkeypatch.setattr(Healthagent, "rundir", str(tmp_path / "run"))

    with pytest.raises(PermissionError, match="insecure ownership or permissions"):
        Healthagent.prepare_runtime_directory()

def test_make_json_safe_with_function():
    def foo():
        return 42
    try:
        json_safe = make_json_safe(foo)
        # Should not raise, but will likely return a string or None
        assert json_safe == {}
        json.dumps(json_safe)
    except Exception as e:
        assert False, f"make_json_safe failed on function: {e}"

def test_make_json_safe_with_custom_class_enum_datetime_set():
    class MyEnum(enum.Enum):
        RED = 1
        GREEN = 2

    class Custom:
        def __init__(self):
            self.color = MyEnum.RED
            self.timestamp = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            self.tags = {"foo", "bar"}

    obj = Custom()
    try:
        json_safe = make_json_safe(obj)
        json.dumps(json_safe)
    except Exception as e:
        assert False, f"make_json_safe failed on custom class with enum, datetime, set: {e}"


def test_aux_data_excluded_from_view():
    """aux_data should never appear in view() output."""
    report = HealthReport(
        status=HealthStatus.ERROR,
        description="GPU failure",
        aux_data={"raw_stdout": "some debug output", "exit_code": 1},
    )
    view = report.view()
    assert "aux_data" not in view
    assert "raw_stdout" not in view
    assert "exit_code" not in view
    # Normal fields should still be present
    assert view["status"] == "Error"
    assert view["description"] == "GPU failure"


def test_aux_data_excluded_from_equality():
    """Two reports differing only in aux_data should be equal."""
    r1 = HealthReport(status=HealthStatus.WARNING, description="test")
    r2 = HealthReport(
        status=HealthStatus.WARNING,
        description="test",
        aux_data={"debug": "info"},
    )
    assert r1 == r2


def test_aux_data_not_in_summarize():
    """Summarize output must not contain aux_data."""
    reporter = Reporter()
    reporter.publish_cc = False
    name = "gpu_test"
    report = HealthReport(
        status=HealthStatus.ERROR,
        description="fail",
        aux_data={"internal_log": "verbose data"},
    )
    reporter.store[name] = report
    summary = reporter.summarize()
    assert "aux_data" not in summary[name]
    assert "internal_log" not in summary[name]


def test_aux_data_accessible_on_report():
    """Modules should be able to read/write aux_data directly."""
    report = HealthReport(aux_data={"key": "val"})
    assert report.aux_data == {"key": "val"}
    report.aux_data["key2"] = 42
    assert report.aux_data["key2"] == 42


def test_aux_data_defaults_to_none():
    report = HealthReport()
    assert report.aux_data is None


def test_view_cli_excludes_marked_fields():
    """view() should omit fields marked with cli_exclude metadata. cli_exclude is True by default"""
    report = HealthReport(
        status=HealthStatus.ERROR,
        description="GPU failure",
        details="long error trace here",
    )
    view_full = report.view(cli_exclude=False)
    assert "details" in view_full

    view_cli = report.view()
    assert "details" not in view_cli
    assert view_cli["status"] == "Error"
    assert view_cli["description"] == "GPU failure"


def test_summarize_excludes_details():
    """summarize() should not include 'details' in its output."""
    reporter = Reporter()
    reporter.publish_cc = False
    report = HealthReport(
        status=HealthStatus.ERROR,
        description="fail",
        details="verbose error log",
    )
    reporter.store["test"] = report
    summary = reporter.summarize()
    assert "details" not in summary["test"]
    assert summary["test"]["description"] == "fail"


async def test_aux_data_persisted_on_dedup():
    """aux_data should be updated even when visible report fields are unchanged."""
    reporter = Reporter()
    reporter.publish_cc = False
    name = "gpu_test"

    r1 = HealthReport(status=HealthStatus.ERROR, description="fail")
    r1.aux_data = {0: {"errors": ["GPU 0 failed"], "warnings": []}}
    await reporter.update_report(name, r1)
    assert reporter.store[name].aux_data == {0: {"errors": ["GPU 0 failed"], "warnings": []}}

    # Same visible fields, different aux_data — should still persist
    r2 = HealthReport(status=HealthStatus.ERROR, description="fail")
    r2.aux_data = {0: {"errors": ["GPU 0 failed"], "warnings": []}, 1: {"errors": ["GPU 1 failed"], "warnings": []}}
    await reporter.update_report(name, r2)
    assert reporter.store[name].aux_data == r2.aux_data

    # Reset with blank report — aux_data should become None
    r3 = HealthReport()
    await reporter.update_report(name, r3)
    assert reporter.store[name].aux_data is None


async def test_reporter():

    my_reporter = Reporter()
    my_reporter.publish_cc = True
    # fetch epilog_test report.
    name= 'epilog_test'
    report = my_reporter.get_report(name=name)
    # we never created it
    assert report == None

    with patch("healthagent.scheduler.Scheduler.subprocess") as mock_subprocess, \
         patch("healthagent.scheduler.Scheduler.add_task", new_callable=AsyncMock) as mock_add_task:
        mock_subprocess.return_value = "mocked_task"
        mock_add_task.return_value = None  # or an awaitable if needed

        report = HealthReport(status=HealthStatus.ERROR, description="epilog failures", details="GPU not available")
        await my_reporter.update_report(name=name,report=report)
        mock_subprocess.assert_called_once()
        mock_add_task.assert_awaited_once()

        # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()
        # send the same report again, and it should not actually send it since nothing changed.
        await my_reporter.update_report(name=name, report=report)
        mock_subprocess.assert_not_called()
        mock_add_task.assert_not_called()

         # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()
        await my_reporter.clear_all_errors(timedelta(hours=1))
        mock_subprocess.assert_not_called()
        mock_add_task.assert_not_called()

        # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()
        # send an updated report
        ok_report = HealthReport() # defaults to OK
        await my_reporter.update_report(name, report=ok_report)
        mock_subprocess.assert_called_once()
        mock_add_task.assert_awaited_once()

        await my_reporter.clear_all_errors()
        mock_subprocess.assert_called_once()
        mock_add_task.assert_called_once()


    with patch("healthagent.scheduler.Scheduler.subprocess") as mock_subprocess, \
         patch("healthagent.scheduler.Scheduler.add_task", new_callable=AsyncMock) as mock_add_task:
        mock_subprocess.return_value = "mocked_task"
        mock_add_task.return_value = None  # or an awaitable if needed

        report = HealthReport(status=HealthStatus.ERROR, description="prolog failures")
        await my_reporter.update_report("prolog_test",report=report)
        mock_subprocess.assert_called_once()
        mock_add_task.assert_awaited_once()

        # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()

        report = HealthReport(status=HealthStatus.ERROR, description="hardware failures")
        await my_reporter.update_report("hardware_test",report=report)
        mock_subprocess.assert_called_once()
        mock_add_task.assert_awaited_once()

        # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()
        # clear all the errors
        await my_reporter.clear_all_errors()
        assert mock_subprocess.call_count == 2
        assert mock_add_task.call_count == 2

        summary = my_reporter.summarize()
        assert summary != None
        for test,result in summary.items():
            assert result['status'] == 'OK'

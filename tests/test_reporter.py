from healthagent.reporter import *
import pickle
import os
import json
from unittest.mock import patch,AsyncMock
import enum

def test_healthreport():

    ok_report =  HealthReport()
    assert ok_report.status == HealthStatus.OK

    custom_fields = {}
    custom_fields['error_count']=10
    custom_fields['test_type'] = "software"
    custom_fields['test_name'] = ['software', 'gpu']
    error_report1 = HealthReport(status=HealthStatus.ERROR, description="failed_test_description", custom_fields=custom_fields, last_update=datetime.datetime.now(tz=datetime.timezone.utc))

    assert error_report1.status == HealthStatus.ERROR
    assert error_report1.error_count == 10
    assert error_report1.test_type == "software"
    # create a copy of the previous error report
    error_report2 = HealthReport(status=HealthStatus.ERROR, description="failed_test_description", custom_fields=custom_fields)
    # assert equality does not compare timestamps
    assert error_report1 == error_report2

def test_healthreport_obj():

    custom_fields = {}
    custom_fields['error_count']=10
    custom_fields['test_type'] = "software"
    custom_fields['test_name'] = ['software', 'gpu']
    categories = set()
    categories.add('integration')
    categories.add('epilog')
    custom_fields['categories'] = categories
    error_report1 = HealthReport(status=HealthStatus.ERROR, description="failed_test_description", custom_fields=custom_fields, last_update=datetime.datetime.now(tz=datetime.timezone.utc))

    try:
        json_safe = make_json_safe(error_report1)
        obj = json.dumps(json_safe)
    except Exception as e:
        assert False, f'{e}'

    try:
        with open('test.pkl', 'wb') as fp:
            pickle.dump(error_report1, fp)
        with open('test.pkl', 'rb') as fp:
            report = pickle.load(fp)
        assert report.error_count == 10
    except Exception as e:
        assert False, f'pickle dumping/loading raised an exception: {e}'
    os.remove('test.pkl')

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
            self.timestamp = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
            self.tags = {"foo", "bar"}

    obj = Custom()
    try:
        json_safe = make_json_safe(obj)
        json.dumps(json_safe)
    except Exception as e:
        assert False, f"make_json_safe failed on custom class with enum, datetime, set: {e}"


async def test_reporter():

    my_reporter = Reporter()
    # fetch epilog_test report.
    report = my_reporter.get_report('epilog_test')
    # we never created it
    assert report == None

    with patch("healthagent.scheduler.Scheduler.subprocess") as mock_subprocess, \
         patch("healthagent.scheduler.Scheduler.add_task", new_callable=AsyncMock) as mock_add_task:
        mock_subprocess.return_value = "mocked_task"
        mock_add_task.return_value = None  # or an awaitable if needed

        report = HealthReport(status=HealthStatus.ERROR, description="epilog failures", details="GPU not available")
        await my_reporter.update_report("epilog_test",report=report)
        mock_subprocess.assert_called_once()
        mock_add_task.assert_awaited_once()

        # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()
        # send the same report again, and it should not actually send it since nothing changed.
        await my_reporter.update_report("epilog_test", report=report)
        mock_subprocess.assert_not_called()
        mock_add_task.assert_not_called()

        # Reset mocks before the next call
        mock_subprocess.reset_mock()
        mock_add_task.reset_mock()
        # send an updated report
        ok_report = HealthReport() # defaults to OK
        await my_reporter.update_report("epilog_test",report=ok_report)
        mock_subprocess.assert_called_once()
        mock_add_task.assert_awaited_once()

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

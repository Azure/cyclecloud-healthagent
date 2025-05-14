import subprocess
import copy
from enum import Enum
from collections import deque
from dataclasses import dataclass, asdict, is_dataclass
import datetime
from time import time
from typing import Any,Dict
import logging
from healthagent.AsyncScheduler import AsyncScheduler,Priority

log = logging.getLogger('healthagent')

def make_json_safe(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, set):
        return list(obj)
    elif isinstance(obj, dict):
        return {str(k): make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_safe(v) for v in obj]
    elif is_dataclass(obj):
        return make_json_safe(asdict(obj))
    elif hasattr(obj, '__dict__'):
        return make_json_safe(vars(obj))
    else:
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

class HealthStatus(Enum):
    OK = 'OK'
    WARNING = 'Warning'
    ERROR = 'Error'
    NA = 'NA'

@dataclass
class HealthReport:

    "Status of the health"
    status: HealthStatus = HealthStatus.OK
    "Description of status"
    message: str = None
    "One line description describing the details"
    description: str = None
    "Detailed message"
    details: str = None
    "recommended actions"
    recommendations :str = None
    "custom module specific data"
    custom_fields: Dict[str, Any] = None

    def __post_init__(self):
        if self.custom_fields is None:
            self.custom_fields = {}

    def __getattr__(self, item):
        return self.custom_fields.get(item, None)

    def view(self) -> Dict[str, Any]:
        # Convert the dataclass to a dictionary
        base_dict = make_json_safe(asdict(self))
        # Merge custom_fields into the top-level dictionary
        custom_fields = base_dict.pop("custom_fields", {})
        base_dict.update(custom_fields)
        # Filter out keys with None values and convert Enums to their values
        filtered_dict = {
            key: (value.value if isinstance(value, Enum) else value)
            for key, value in base_dict.items()
            if value is not None
        }
        return filtered_dict

class Reporter:

    def __init__(self, name: str = None):
        """
        name: name of the health report (optional)
        """
        self.jetpack = "/opt/cycle/jetpack/bin/jetpack"
        self.store = {}
        if name:
            self.store[name] = HealthReport()

    def get_report(self, name) -> HealthReport:

        data = self.store.get(name)
        if data:
            return copy.deepcopy(data)
        return data

    def summarize(self):

        response = {}
        for name, report in self.store.items():
            response[name] = report.view()
        return response

    async def clear_all_errors(self):

        report = HealthReport()
        for key in self.store:
            log.debug(f"Clearing previous error report for {key}")
            await self.update_report(name=key, report=report)

    async def update_report(self, name: str, report: HealthReport):

        if not name:
            raise ValueError("The 'name' argument cannot be None")
        if not report:
            raise ValueError("The 'report' argument cannot be None")
        if not isinstance(report, HealthReport):
            raise TypeError("The 'report' argument must be an instance of HealthReport.")

        # Prepare default messages for warnings and errors
        default_messages = {
            HealthStatus.WARNING: f"{name} reports warnings",
            HealthStatus.ERROR: f"{name} reports errors",
        }

        # Set the message if it's missing
        if report.status in default_messages and not report.message:
            report.message = default_messages[report.status]


        last_report = self.store.get(name)
        if not last_report or (asdict(last_report) != asdict(report)):
            self.store[name] = report
            await self._report_health_status(name)

    async def _report_health_status(self, name):

        report = self.store.get(name)
        log.debug(f"Setting jetpack node condition, Name: {name}, Status: {report.status}")

        # Build the command arguments
        args = [self.jetpack, 'condition', 'set', '-n', name, '-s', report.status.value]
        if report.status != HealthStatus.OK:
            if report.message is not None:
                args.extend(['-m', report.message])
            if report.description is not None:
                args.extend(['-d', report.description])
            if report.recommendations is not None:
                args.extend(['-r', report.recommendations])
            if report.details is not None:
                args.extend(['--details', report.details])

        # Schedule the subprocess task
        await AsyncScheduler.add_subprocess_task(time(), Priority.GENERAL, *args)
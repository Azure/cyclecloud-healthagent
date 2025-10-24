import copy
from enum import Enum
from dataclasses import dataclass, asdict, is_dataclass, field
from datetime import datetime, timedelta, timezone
from time import time
from typing import Any,Dict
import logging
import os
from healthagent.scheduler import Scheduler

log = logging.getLogger('healthagent')

def make_json_safe(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%dT%H:%M:%S %Z")
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
    "Last time the report was updated"
    last_update: datetime = field(init=False)
    "custom module specific data"
    custom_fields: Dict[str, Any] = None

    def __eq__(self, other):
        if not isinstance(other, HealthReport):
            return NotImplemented
        d1 = asdict(self)
        d2 = asdict(other)
        # remove timestamps before comparing
        d1.pop('last_update', None)
        d2.pop('last_update', None)
        return d1 == d2

    def __post_init__(self):
        if self.custom_fields is None:
            self.custom_fields = {}
        self.last_update = datetime.now(tz=timezone.utc)

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
        self.publish_cc = os.getenv("PUBLISH_CC", 'true').lower() == 'true'

        if not os.path.exists(self.jetpack):
            log.info(f"{self.jetpack} not found")
            self.publish_cc = False

        if not self.publish_cc:
            log.info(f"Disabling publishing reports to CC")
        if name:
            self.store[name] = HealthReport()

    @classmethod
    def load_reporter_obj(cls, old) -> 'Reporter':

        new_reporter = cls()
        # Transfer the health report store if it exists
        if hasattr(old, 'store') and old.store:
            new_reporter.store = old.store

        return new_reporter


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

    async def clear_all_errors(self, delta: timedelta = None):
        """
        Clear all errors or all errors before a given delta.
        """

        now = datetime.now(tz=timezone.utc)
        for name,report in self.store.items():
            if not delta or now - report.last_update > delta:
                log.debug(f"Clearing previous error report for {name}")
                await self.update_report(name=name, report=HealthReport())

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

        # Always update the last_update before comparison
        report.last_update = datetime.now(tz=timezone.utc)
        last_report = self.store.get(name)
        if not last_report or (last_report != report):
            self.store[name] = report
            await self.publish_cc_status(name)
        else:
            # still update the last_update
            self.store[name].last_update = report.last_update

    async def publish_cc_status(self, name):

        if not self.publish_cc:
            return
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
        task = Scheduler.subprocess(*args)
        await Scheduler.add_task(task)
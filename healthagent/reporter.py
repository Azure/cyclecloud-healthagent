import subprocess
from enum import Enum
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import Any,Dict
import logging
from healthagent.AsyncScheduler import AsyncScheduler,Priority

log = logging.getLogger(__name__)
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
    message: str = ""
    "One line description describing the details"
    description: str = ""
    "Detailed message"
    details: str = ""
    "recommended actions"
    recommendations :str = ""
    "custom module specific data"
    custom_fields: Dict[str, Any] = None

    def __post_init__(self):
        if self.custom_fields is None:
            self.custom_fields = {}

    def __getattr__(self, item):
        return self.custom_fields.get(item, None)


class Reporter:

    def __init__(self, name: str = None):

        self.jetpack = "/opt/cycle/jetpack/bin/jetpack"
        self.store = {}
        if name:
            self.store[name] = HealthReport()

    def get_report(self, name) -> HealthReport:

        return self.store.get(name)


    async def update_report(self, name: str, report: HealthReport):

        assert name is not None, "name argument cannot be None"
        assert report is not None, "report argument cannot be None"

        last_report = self.store.get(name)
        if last_report != report:
            self.store[name] = report
            await self._report_health_status(name)

    async def _report_health_status(self, name):

        report = self.store.get(name)
        log.debug(f"Setting jetpack node condition, Name: {name}, Status: {report.status}")
        if report.status == HealthStatus.OK:
            await AsyncScheduler.add_subprocess_task(time(), Priority.GENERAL, self.jetpack, 'condition', 'set', '-n', name, '-s', report.status.value)
        else:
            if not report.message:
                if report.status == HealthStatus.WARNING:
                    report.message = f"{name} reports warnings"
                elif report.status == HealthStatus.ERROR:
                    report.message = f"{name} reports errors"
            await AsyncScheduler.add_subprocess_task(time(), Priority.GENERAL, self.jetpack, 'condition', 'set', '-n', name, '-s', report.status.value, '-m', report.message, '-d', report.description, '--details', report.details)

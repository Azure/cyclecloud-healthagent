import asyncio
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError
from dbus_next.constants import BusType
import logging
import systemd.journal
from time import time
from healthagent import status
from healthagent.scheduler import Scheduler
from healthagent.reporter import Reporter,HealthReport,HealthStatus

log = logging.getLogger('healthagent')
class SystemdMonitor:


    def __init__(self, reporter: Reporter):
        self.state = dict()
        self.bus = None
        self.manager = None
        self.unit_paths = set()
        self.reporter = reporter
        self.services_not_enabled = list()


    async def create(self):

        await self.reporter.clear_all_errors()
        # Get systemd manager
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        systemd_obj = await self.bus.introspect("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
        systemd_iface = self.bus.get_proxy_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1", systemd_obj)
        self.manager = systemd_iface.get_interface("org.freedesktop.systemd1.Manager")
        # Add event listener for new units
        self.manager.on_unit_new(self.handle_unit_new)

    async def __add_handler(self, unit_name, service):

        introspect = await self.bus.introspect("org.freedesktop.systemd1", unit_name)
        unit_obj = self.bus.get_proxy_object("org.freedesktop.systemd1", unit_name, introspect)
        properties_iface = unit_obj.get_interface("org.freedesktop.DBus.Properties")
        # Do an initial check to set the state of the service. Useful if the service to monitor is already in failed state.
        curr_active_state = await properties_iface.call_get('org.freedesktop.systemd1.Unit', 'ActiveState')
        curr_substate = await properties_iface.call_get('org.freedesktop.systemd1.Unit', 'SubState')
        await self.set_current_state(service=service, active_state=curr_active_state.value, substate=curr_substate.value)
        callback = self.create_callback(unit=unit_name, service_name=service)
        # Set on_properties_changed callback to allow dbus to run our callback if there is any change in the state of the service.
        properties_iface.on_properties_changed(callback)
        log.debug(f"Monitoring '{unit_name}'")

    async def handle_unit_new(self, service, unit_name):
        """
        Called when a new unit is loaded by systemd.
        If the unit matches a service in our monitor list, add a monitor for it.
        """
        # id is the unit name, e.g., "myservice.service"
        # unit_path is the object path
        # Check if this unit is in the list of services to monitor
        if service in self.services_not_enabled:
            # Avoid duplicate monitoring
            if unit_name not in self.unit_paths:
                self.unit_paths.add(unit_name)
                try:
                    await self.__add_handler(unit_name=unit_name, service=service)
                except Exception as e:
                    log.error(e)
    def get_journal_entries(self, service_name):
        """Prints the last `num_entries` lines of journal logs for a given systemd service."""
        num_entries = 10
        out = str()
        j = systemd.journal.Reader()

        j.add_match(_SYSTEMD_UNIT=service_name)

        j.seek_tail()

        j.get_previous(num_entries)

        for entry in j:
            timestamp = entry.get('__REALTIME_TIMESTAMP', 'Unknown Time')
            message = entry.get('MESSAGE', 'No Message')
            out += f"[{timestamp}] {message}\n"
        return out


    async def set_current_state(self, service, active_state, substate):
        """
        Record and notify certain state transitions.
        For detecting unhealthy node we are only interested in specific state transitions that
        contain or end up in "failed" state. So active-> failed, inactive->failed are the only valid
        transitions for detecting an unhealthy service.
        Transient states such as "activating", "deactivating" do not need to be
        recorded because we record the initial state "inactive" or "active".

        Similarly for detecting a valid recovery of a service, we are specifically only interested
        in a state transition from "failed" -> "active". All other state transitions are either
        transient or do not represent valid recovery from an unhealthy state.
        """
        log.debug(f"service: {service}, ActiveState: {active_state} SubState: {substate}")
        report = HealthReport()
        if active_state != self.state.get(service):
            if active_state == "failed":
                report.status =  HealthStatus.ERROR
                report.description = f"{service} Service unhealthy"
                report.details = self.get_journal_entries(service_name=service)
                log.error(report.description)
                await self.reporter.update_report(name=service, report=report)
            elif active_state == "active" and substate == "running":
                if self.state.get(service) == "failed":
                    log.info(f"{service} Service Healthy")
                    report.status = HealthStatus.OK
                    await self.reporter.update_report(name=service, report=report)

            self.state[service] = active_state


    def create_callback(self, unit: str = None, service_name: str = None):

        async def handle_properties_changed(interface_name, changed_properties, invalidated_properties):
            active_state = changed_properties.get("ActiveState").value if changed_properties.get("ActiveState") else None
            substate = changed_properties.get('SubState').value if changed_properties.get("SubState") else None


            if active_state in ["failed", "active", "inactive"]:
                Scheduler.add_task(self.set_current_state, service_name, active_state, substate)

        return handle_properties_changed


    async def add_monitor(self, services: list = None):
        """
        Set signals for async monitoring for th given list of services.
        """

        if not services:
            log.debug("No services added")
            return

        for service in services:
            try:
                unit_name = await self.manager.call_get_unit(service)
                if unit_name in self.unit_paths:
                    # we already monitoring it, ignore
                    continue
                self.unit_paths.add(unit_name)
                await self.__add_handler(unit_name=unit_name, service=service)
            except DBusError as e:
                #TODO: Fix this.
                #This can be logged as an exception/error without trapping this specific exception once we remove the hardcoded list of services.
                if e.type == "org.freedesktop.systemd1.NoSuchUnit":
                    log.debug(f"Could not find service '{service}': {e}")
                    log.debug(f"Ignoring service {service} for monitoring")
                    self.services_not_enabled.append(service)
                else:
                    log.exception(e)
                    raise
            except Exception as e:
                log.exception(e)
                raise

    @status
    def show_status(self):
        return self.reporter.summarize()
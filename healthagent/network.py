import os
import logging
from collections import deque, defaultdict
from enum import Enum
from pathlib import Path
from dataclasses import dataclass
from healthagent import status
from healthagent.reporter import Reporter, HealthReport, HealthStatus
from healthagent.scheduler import Scheduler

log = logging.getLogger('healthagent')

class NetDevType(Enum):
    # these are taken from https://github.com/torvalds/linux/blob/master/include/uapi/linux/if_arp.h
    ETHERNET = 1
    INFINIBAND = 32
    UNKNOWN = -1

    @classmethod
    def parse(cls, raw: str) -> "NetDevType":
        s = int(raw.strip().lower())
        try:
            return cls(s)
        except ValueError:
            # Fallback in case a new kernel adds something
            return cls.UNKNOWN

class Carrier(Enum):
    # physical link is UP
    UP = 1
    # physical link is DOWN
    DOWN = 0
    # we don't know
    UNKNOWN = -1

    @classmethod
    def parse(cls, raw: str) -> "Carrier":
        s = int(raw.strip())
        try:
            return cls(s)
        except ValueError:
            # Fallback in case a new kernel adds something
            return cls.UNKNOWN

class OperState(Enum):
    UNKNOWN = "unknown"
    NOTPRESENT = "notpresent"
    DOWN = "down"
    LOWERLAYERDOWN = "lowerlayerdown"
    TESTING = "testing"
    DORMANT = "dormant"
    UP = "up"

    @classmethod
    def parse(cls, raw: str) -> "OperState":
        s = raw.strip().lower()
        try:
            return cls(s)
        except ValueError:
            # Fallback in case a new kernel adds something
            return cls.UNKNOWN

@dataclass
class NetworkInterface:
    # Uses the kernel netdev interface.

    # Name of the network interface
    name: str = None
    # type of interface
    type: NetDevType = NetDevType.UNKNOWN
    # Indicates the interface RFC2863 operational state as a string.
    # Possible values are “unknown”, “notpresent”, “down”, “lowerlayerdown”, “testing”, “dormant”, “up”.
    operstate: OperState = OperState.UNKNOWN
    # 0 if physical link is down 1 if physical link is up. Read as an integer. -1 means we did not read the value. This can occur if the interface was administratively down.
    carrier: Carrier = Carrier.UNKNOWN
    # Carrier changes should generally be 1 in a healthy system if a link never went down. This field detects all state transitions, down to up and vice versa. -1 means we did not read the value
    carrier_changes: int = -1
    # should be 0 if carrier never went down, -1 means we could not read the value.
    carrier_down_count: int = -1
    # kernel devpath of a device but with /sys prefix
    device: str = None

class SlidingStore:

    def __init__(self, window: int = 60):
        self._store = defaultdict(lambda: deque(maxlen=window))
        self.window = window

    def __setitem__(self, key, value):

        self._store[key].append(value)

    def __getitem__(self, key):
        """Return the deque for a key."""
        return self._store[key]

    def get_rate_of_change(self, key):

        dq = self._store[key]
        if len(dq) < 2:
            return 0

        start_value = dq[0]
        end_value = dq[-1]

        value_diff = end_value - start_value

        return value_diff

class NetworkHealthChecks:

    def __init__(self, reporter: Reporter, window: int = 60):

        self.sysfs = "/sys/class/net"
        self.reporter = reporter
        #TODO: Make this and the sampling rate configurable.
        self.timestore = SlidingStore(window=window)

    async def create(self):

        await self.reporter.clear_all_errors()
        Scheduler.add_task(self.run_network_checks)

    def list_interfaces(self, include_virtual=False) -> list:
        """
        List all interfaces present.
        Include virtual interfaces (that do not have a physical link) if include_virtual is set to True.
        Exclude virtual interfaces by default.
        """

        names: list[str] = []
        if not os.path.isdir(self.sysfs):
            return names
        try:
            for entry in os.listdir(self.sysfs):
                iface_path = os.path.join(self.sysfs, entry)
                if not os.path.islink(iface_path):
                    continue
                ## Doing realpath on the symbolic link gives us the location in /sys/devices. Doing a read on the symbolic link and then doing a realpath does not work.
                resolved = os.path.realpath(iface_path)
                is_virtual = "/virtual/" in resolved
                if not include_virtual and is_virtual:
                    continue
                names.append(resolved)
        except OSError:
            pass
        return names

    def get_uptime_hours(self) -> float | None:
        """
        Read system uptime from /proc/uptime and return hours.
        Returns:
            float: System uptime in hours, or None if unable to read
        """
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
            return round(uptime_seconds / 3600.0, 2)
        except (OSError, IOError, ValueError, IndexError):
            return None

    def get_network_state(self) -> list[NetworkInterface]:

        interfaces = []
        for iface in self.list_interfaces():
            ni = NetworkInterface()
            ni.device = iface
            ni.name = Path(iface).name

            try:
                with open(os.path.join(iface, 'operstate'), 'r') as f:
                    val = f.read().strip()
                ni.operstate = OperState.parse(val)
                if ni.operstate == OperState.DOWN:
                    log.info(f"Interface {ni.name} is administratively down")
                else:
                    with open(os.path.join(iface, "carrier"), 'r') as f:
                        val = f.read().strip()
                    ni.carrier = Carrier.parse(val)

            except OSError as e:
                log.error(f"{e}")

            try:
                with open(os.path.join(iface, "type"), 'r') as f:
                    val = f.read().strip()
                ni.type = NetDevType.parse(val)
            except OSError as e:
                log.error(f"Unreadable Type for interface: {ni.name}, {e}")

            try:
                with open(os.path.join(iface, "carrier_changes"), 'r') as f:
                    val = f.read().strip()
                ni.carrier_changes = int(val)
            except OSError as e:
                log.error(f"Unreadable Carrier Changes for interface: {ni.name}, {e}")

            try:
                with open(os.path.join(iface, "carrier_down_count"), 'r') as f:
                    val = f.read().strip()
                ni.carrier_down_count = int(val)
                self.timestore[ni.name] = ni.carrier_down_count
            except OSError as e:
                log.error(f"Unreadable Carrier Down Count for interface: {ni.name}, {e}")


            interfaces.append(ni)

        return interfaces

    def print_interfaces(self):
        interfaces = self.get_network_state()
        for iface in interfaces:
            log.info("###########")
            log.info("Name: %s" % iface.name)
            log.info("operstate: %s" % iface.operstate.value)
            log.info("carrier : %s" % iface.carrier.value)
            log.info("device : %s" % iface.device)
            log.info("carrier_changes : %s" % iface.carrier_changes)
            log.info("carrier_down_count : %s" % iface.carrier_down_count)

    @status
    def show_status(self):
        return self.reporter.summarize()

    @Scheduler.periodic(60)
    async def run_network_checks(self):

        interfaces = self.get_network_state()
        uptime = self.get_uptime_hours()
        report = HealthReport()
        unop = []
        custom_fields = {}
        msgs = []
        for ni in interfaces:
            custom_fields[ni.name] = {}
            link_down_rate_per_hour = self.timestore.get_rate_of_change(key=ni.name)
            custom_fields[ni.name]['link_down_rate_per_hour'] = link_down_rate_per_hour
            custom_fields[ni.name]['link_flap_since_uptime'] = ni.carrier_changes
            if link_down_rate_per_hour >= 1:
                msgs.append(f"Network interface {ni.name} went down {link_down_rate_per_hour} times in the last hour")
                if report.status == HealthStatus.OK:
                    report.status = HealthStatus.WARNING

            if ni.operstate != OperState.UP:
                custom_fields[ni.name]['error_count'] = 1
                unop.append(ni.name)
                msgs.append(f"Network interface {ni.name} is not operational and in state {ni.operstate.value}.")
                custom_fields[ni.name]['carrier'] = ni.carrier.value
                report.status = HealthStatus.ERROR


        if msgs:
            report.details = '\n'.join(msgs)
        report.custom_fields = custom_fields
        if report.status != HealthStatus.OK:
            if unop:
                report.description = f"Network interfaces {','.join(unop)} are not operational"
            else:
                report.description = f"Network Warnings"


        await self.reporter.update_report('Network', report=report)

import logging
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, fields
from healthagent import healthcheck
from healthagent.util import read_kernel_attrs, evaluate
from healthagent.healthmodule import HealthModule
from healthagent.reporter import Reporter, HealthReport, HealthStatus
from healthagent.scheduler import Scheduler

log = logging.getLogger('healthagent')
### References
# https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-class-net
# https://www.kernel.org/doc/Documentation/ABI/stable/sysfs-class-infiniband

class NetDevType(Enum):
    # from include/uapi/linux/if_arp.h
    ETHERNET = 1
    INFINIBAND = 32
    UNKNOWN = -1

_NET_CONFIG = {
    "infiniband": {
        "state":                {"eval": "ne", "error": "4: ACTIVE", "msg": "IB link is not active"},
        "phys_state":           {"eval": "ne", "error": "5: LinkUp", "msg": "IB link not in state LinkUp"},
        "link_downed":          {"eval": "gt", "error": 3, "msg": "IB link down count exceeded the threshold"},
        "link_error_recovery":  {"eval": "gt", "warning": 3, "msg": "IB link error recovery exceeded the threshold"},
        "operstate":            {"eval": "ne", "warning": "up", "msg": "IPoIB not in state up"},
        "carrier_down_count":   {"eval": "gt", "warning": 5, "msg": "IPoIB carrier down count exceeded threshold"},
    },
    "ethernet": {
        "carrier_down_count":   {"eval": "gt", "warning": 3, "msg": "carrier_down_count exceeded threshold"},
        "operstate":            {"eval": "ne", "error": "up", "msg": "interface not in state up"},
    },
}


@dataclass
class IBPort:
    state: str = None           # "4: ACTIVE"
    phys_state: str = None      # "5: LinkUp"
    rate: str = None            # "400 Gb/sec (4X NDR)"
    link_downed: int = 0
    link_error_recovery: int = 0

@dataclass
class IBDevice:
    name: str = None            # "mlx5_ib2"
    ports: dict = None          # {"1": IBPort(...)} — port_num -> IBPort

@dataclass
class NetworkInterface:
    name: str = None
    type: NetDevType = NetDevType.UNKNOWN
    operstate: str = "unknown"
    carrier: int = -1           # 0=down, 1=up
    carrier_changes: int = -1
    carrier_down_count: int = -1
    device: Path = None          # resolved sysfs path
    ib_device: IBDevice = None


class NetworkHealthChecks(HealthModule):

    def __init__(self, reporter: Reporter, config: dict | None = None):
        super().__init__(reporter, config)

    async def create(self):
        await self.reporter.clear_all_errors()
        Scheduler.add_task(self.run_network_checks)

    def get_network_state(self, include_virtual=False) -> list[NetworkInterface]:

        network_interfaces = []
        for name, iface in read_kernel_attrs(root="/sys/class/net").items():
            if not isinstance(iface, Path):
                continue
            if not include_virtual and "/virtual/" in str(iface):
                continue
            ni = NetworkInterface()
            ni.device = iface
            ni.name = name

            v = read_kernel_attrs(root=iface, paths=["carrier", "operstate", "type", "carrier_changes", "carrier_down_count"])
            ni.operstate = v.get("operstate", "unknown")
            ni.carrier = int(v["carrier"]) if "carrier" in v else -1
            ni.carrier_changes = int(v["carrier_changes"]) if "carrier_changes" in v else -1
            ni.carrier_down_count = int(v["carrier_down_count"]) if "carrier_down_count" in v else -1
            try:
                ni.type = NetDevType(int(v["type"])) if "type" in v else NetDevType.UNKNOWN
            except ValueError:
                ni.type = NetDevType.UNKNOWN

            if ni.type == NetDevType.INFINIBAND:
                # Enumerate device/infiniband/ to find the IB device (1:1 with net iface)
                for ib_dev_name, ib_dev_path in read_kernel_attrs(iface / "device" / "infiniband").items():
                    if not isinstance(ib_dev_path, Path):
                        # ignore files here
                        continue
                    ni.ib_device = IBDevice(name=ib_dev_name, ports={})
                    # Enumerate ports/ (1:N, must iterate)
                    for port_num, port_path in read_kernel_attrs(ib_dev_path / "ports").items():
                        if not isinstance(port_path, Path):
                            # Ignore files here
                            continue
                        port_vals = read_kernel_attrs(port_path, ["state", "phys_state", "rate",
                                                                  "counters/link_downed",
                                                                  "counters/link_error_recovery"])
                        counters = port_vals.get("counters", {})
                        ni.ib_device.ports[port_num] = IBPort(
                            state=port_vals.get("state"),
                            phys_state=port_vals.get("phys_state"),
                            rate=port_vals.get("rate"),
                            link_downed=int(counters.get("link_downed", 0)),
                            link_error_recovery=int(counters.get("link_error_recovery", 0))
                        )

            network_interfaces.append(ni)

        return network_interfaces

    @healthcheck("NetworkInterfaceCheck", description="Monitor network interface health")
    @Scheduler.periodic(60)
    async def run_network_checks(self):

        interfaces = self.get_network_state()
        report = HealthReport()
        custom_fields = {}
        details = []
        # Derive port-level field names from the IBPort dataclass
        _ib_port_fields = {f.name for f in fields(IBPort)}

        def check_field(iface_name, field, check, value, port_num=None):
            for level in ("error", "warning"):
                thresh = check.get(level)
                if thresh is None:
                    continue
                triggered, _ = evaluate(check["eval"], value, thresh)
                if triggered:
                    msg = check.get("msg", f"{field}={value} (threshold: {level} {check['eval']} {thresh})")
                    if port_num is not None:
                        msg = f"port {port_num}: {msg}"
                    if level == "error":
                        custom_fields.setdefault(iface_name, {}).setdefault("errors", []).append(msg)
                        details.append(f"ERROR: {iface_name} - {msg}")
                        report.escalate(HealthStatus.ERROR)
                    else:
                        custom_fields.setdefault(iface_name, {}).setdefault("warnings", []).append(msg)
                        details.append(f"WARNING: {iface_name} - {msg}")
                        report.escalate(HealthStatus.WARNING)
                    break  # error takes precedence over warning

        for ni in interfaces:

            custom_fields[ni.name] = {}
            # Select the right config section based on interface type
            config_key = "infiniband" if ni.type == NetDevType.INFINIBAND else "ethernet"
            checks = _NET_CONFIG.get(config_key, {})

            # Evaluate each configured check
            for field, check in checks.items():
                if field in _ib_port_fields:
                    # IB port-level field — evaluate per port
                    if not ni.ib_device:
                        continue
                    for port_num, port in ni.ib_device.ports.items():
                        value = getattr(port, field, None)
                        if value is None:
                            continue
                        check_field(ni.name, field, check, value, port_num=port_num)
                else:
                    # Interface-level field
                    value = getattr(ni, field, None)
                    if value is None:
                        continue
                    check_field(ni.name, field, check, value)

            if ni.ib_device:
                custom_fields[ni.name]["ib_device"] = {
                    ni.ib_device.name: {
                        pn: {"state": p.state, "phys_state": p.phys_state, "rate": p.rate}
                        for pn, p in ni.ib_device.ports.items()
                    }
                }
            custom_fields[ni.name]["operstate"] = ni.operstate

        if details:
            report.details = '\n'.join(details)
        report.custom_fields = custom_fields
        if report.status != HealthStatus.OK:
            report.description = "Network health issues detected"

        await self.reporter.update_report(self.run_network_checks.report_name, report=report)

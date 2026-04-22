from ctypes import *
import os
import sys
import re
import types
import asyncio
from pathlib import Path
from healthagent.reporter import HealthStatus
import logging

DCGM_VERSION = os.getenv("DCGM_VERSION")

try:
    if DCGM_VERSION < '4.0.0':
        print("DCGM version is less than 4.0.0, which is not supported.")
        raise ImportError("Unsupported DCGM version")
    bind_path = "/usr/share/datacenter-gpu-manager-4/bindings/python3"
    sys.path.append(bind_path)
    #TODO: Remove this section when the upstream change is fixed.
    # This is to resolve a bug in DCGM due to a bad import in python bindings.
    # Without this the import of DcgmDiag fails.
    # https://github.com/NVIDIA/DCGM/issues/262
    _m = types.ModuleType("logger")
    _log = logging.getLogger('healthagent')

    # Direct method mapping
    _m.info = _log.info
    _m.debug = _log.debug
    _m.warning = _log.warning
    _m.error = _log.error
    _m.critical = _log.critical

    _m.nvvs_trace_log_filename = None
    sys.modules["logger"] = _m
    import pydcgm
    from dcgm_structs import dcgmExceptionClass
    import dcgm_structs
    import dcgm_fields
    import dcgm_errors
    import dcgm_agent
    import dcgmvalue
    import DcgmFieldGroup
    import DcgmDiag
except:
    raise ImportError("Unable to find or import dcgm python binding, is PYTHONPATH set properly?")

def create_c_callback(func: callable, loop: asyncio.BaseEventLoop):
    @CFUNCTYPE(None, POINTER(dcgm_structs.c_dcgmPolicyCallbackResponse_v2), c_uint64)
    def c_callback(response, userData):
        # copy data into a python struct so that it is the right format and is not lost when "response" var is lost
        callbackResp = dcgm_structs.c_dcgmPolicyCallbackResponse_v2()
        memmove(addressof(callbackResp), response, callbackResp.FieldsSizeof())
        # Schedule our coroutine func to run in the main event loop and dont wait for it here
        # so callback can exit quickly.
        loop.call_soon_threadsafe(asyncio.create_task, func(callbackResp))
    return c_callback

def _resolve_error_codes(*names):
    """Safely resolve DCGM_FR_* names to their integer codes, skipping any
    that don't exist in the installed dcgm_errors module."""
    codes = set()
    for name in names:
        code = getattr(dcgm_errors, name, None)
        if code is not None:
            codes.add(code)
    return codes

def _resolve_error_codes_with_notes(*pairs):
    """Resolve (DCGM_FR_name, note) pairs to {int_code: note} dict,
    skipping any that don't exist in the installed dcgm_errors module."""
    codes = {}
    for name, note in pairs:
        code = getattr(dcgm_errors, name, None)
        if code is not None:
            codes[code] = f"Healthagent Note: {note}"
    return codes

# System/reference fields — needed by non-watch code (XID handling, gpu_count_check,
# display config, detail lookups). Not user-configurable thresholds.
# (alias, dcgm_fields attribute name)
_SYSTEM_FIELDS = [
    # Dev count — used by gpu_count_check
    ("DEVCNT",           "DCGM_FI_DEV_CNT"),
    # Reference temps — used by __display_gpu_config / telemetry
    ("GPUTEMP_SLOWDOWN", "DCGM_FI_DEV_SLOWDOWN_TEMP"),
    ("GPUTEMP_SHUTDOWN",  "DCGM_FI_DEV_SHUTDOWN_TEMP"),
    # Fabric error code — detail field read on fabric status trigger
    ("FABRIC_ERROR",      "DCGM_FI_DEV_FABRIC_MANAGER_ERROR_CODE"),
    # XID — event-driven, handled separately via XID_WARNING/XID_IGNORE
    ("XID_ERRORS",        "DCGM_FI_DEV_XID_ERRORS"),
]

class _Fields:
    """Namespace for DCGM field IDs, populated from _SYSTEM_FIELDS.
    Missing fields on older DCGM versions are set to None."""
    avail_field_ids = []

for _alias, _dcgm_name in _SYSTEM_FIELDS:
    _field_id = getattr(dcgm_fields, _dcgm_name, None)
    setattr(_Fields, _alias, _field_id)
    if _field_id is not None:
        _Fields.avail_field_ids.append(_field_id)

# Default field watches — built-in thresholds for health evaluation.
# Same shape as future YAML config. Each entry is one watch:
#   field:    DCGM field constant name (string)
#   eval:     evaluation type (gt, lt, ge, le, eq, ne, in, bitmask, delta_gt)
#   warning:  threshold for warning severity (number or list for 'in'; omit if not needed)
#   error:    threshold for error severity (number or list for 'in'; omit if not needed)
#   category: reporting category string
#   message:  template with {gpu}, {value}, {threshold} placeholders
#   window:   (optional) rate normalization window in seconds for delta_gt (default: 60, max: 300)
_FIELD_WATCHES = [
    {"field": "DCGM_FI_DEV_GPU_TEMP",               "eval": "gt",       "warning": 83, "error": 90, "category": "Thermal",        "message": "GPU {gpu} temperature {value}\u00b0C exceeds {threshold}\u00b0C"},
    {"field": "DCGM_FI_DEV_CLOCKS_EVENT_REASONS",   "eval": "bitmask",                 "error": 0xE8, "category": "Clocks",       "message": "GPU {gpu} clock throttle reasons active: {value:#x} (mask: {threshold:#x})"},
    {"field": "DCGM_FI_DEV_PERSISTENCE_MODE",        "eval": "ne",                     "error": 1,    "category": "System",        "message": "GPU {gpu} persistence mode not set. Restart nvidia-persistenced or reboot."},
    {"field": "DCGM_FI_DEV_GET_GPU_RECOVERY_ACTION", "eval": "in",  "warning": [2],    "error": [3, 4], "category": "System",      "message": "GPU {gpu} recovery action requested (action: {value})"},
    {"field": "DCGM_FI_DEV_ROW_REMAP_FAILURE",       "eval": "ne",                     "error": 0,    "category": "Memory",        "message": "GPU {gpu} row remap failure detected"},
    {"field": "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL",       "eval": "gt",                     "error": 0,    "category": "Memory",        "message": "GPU {gpu} volatile DBE errors detected: {value}"},
    {"field": "DCGM_FI_DEV_RETIRED_SBE",             "eval": "gt",       "warning": 50, "error": 63, "category": "Memory",        "message": "GPU {gpu} retired SBE pages: {value} (threshold: {threshold})"},
    {"field": "DCGM_FI_DEV_ECC_SBE_AGG_TOTAL",       "eval": "delta_gt", "warning": 100,              "category": "Memory",        "message": "GPU {gpu} SBE rate {value:.0f}/min exceeds {threshold}/min"},
    {"field": "DCGM_FI_DEV_PCIE_REPLAY_COUNTER",     "eval": "delta_gt", "warning": 50, "error": 200, "category": "PCIe",         "message": "GPU {gpu} PCIe replay rate {value:.0f}/min exceeds {threshold}/min"},
]

def resolve_field_watches(watches):
    """Resolve field name strings to DCGM field IDs.
    Skips watches with unknown fields (forward-compatibility)."""
    resolved = []
    for watch in watches:
        field_id = getattr(dcgm_fields, watch["field"], None)
        if field_id is not None:
            resolved.append({**watch, "field_id": field_id})
    return resolved

class Wrap:

    class DcgmConnectionFail(Exception):
        pass

    class DcgmInvalidHandle(Exception):
        pass

    class DcgmGpuNotFound(Exception):
        pass

    fields = _Fields
    default_field_watches = resolve_field_watches(_FIELD_WATCHES)

    ENTITY_GROUP_NAMES = {
        dcgm_fields.DCGM_FE_NONE: "None",
        dcgm_fields.DCGM_FE_GPU: "GPU",
        dcgm_fields.DCGM_FE_VGPU: "vGPU",
        dcgm_fields.DCGM_FE_SWITCH: "Switch",
        dcgm_fields.DCGM_FE_GPU_I: "GPU_I",
        dcgm_fields.DCGM_FE_GPU_CI: "GPU_CI",
        dcgm_fields.DCGM_FE_LINK: "Link",
        dcgm_fields.DCGM_FE_CPU: "CPU",
        dcgm_fields.DCGM_FE_CPU_CORE: "CPU_Core",
        dcgm_fields.DCGM_FE_CONNECTX: "ConnectX",
    }

    @classmethod
    def get_fields(cls):
        return list(_Fields.avail_field_ids)

    HEALTH_ERRORS = _resolve_error_codes(
        "DCGM_FR_NVLINK_ERROR_CRITICAL",
        "DCGM_FR_NVLINK_DOWN",
        "DCGM_FR_NVSWITCH_FATAL_ERROR",
        "DCGM_FR_FAULTY_MEMORY",
        "DCGM_FR_FIELD_VIOLATION",
        "DCGM_FR_FABRIC_PROBE_STATE"
    )

    HEALTH_WARNINGS = _resolve_error_codes(
        "DCGM_FR_PCI_REPLAY_RATE",
        "DCGM_FR_CORRUPT_INFOROM",
        "DCGM_FR_NVSWITCH_NON_FATAL_ERROR",
        "DCGM_FR_NVLINK_SYMBOL_BER_THRESHOLD",
        "DCGM_FR_NVLINK_EFFECTIVE_BER_THRESHOLD",
    )

    DIAG_ERRORS = _resolve_error_codes(
        "DCGM_FR_CUDA_DBE",
        "DCGM_FR_MEMORY_MISMATCH",
        "DCGM_FR_L1TAG_MISCOMPARE",
        "DCGM_FR_BROKEN_P2P_MEMORY_DEVICE",
        "DCGM_FR_BROKEN_P2P_WRITER_DEVICE",
        "DCGM_FR_BROKEN_P2P_NVLINK_WRITER_DEVICE",
        "DCGM_FR_BROKEN_P2P_NVLINK_MEMORY_DEVICE",
        "DCGM_FR_BROKEN_P2P_PCIE_MEMORY_DEVICE",
        "DCGM_FR_BROKEN_P2P_PCIE_WRITER_DEVICE",
        "DCGM_FR_CORRUPT_INFOROM",
        "DCGM_FR_CANNOT_OPEN_LIB",
        "DCGM_FR_DENYLISTED_DRIVER",
        "DCGM_FR_BAD_CUDA_ENV",
        "DCGM_FR_FAULTY_MEMORY",
        "DCGM_FR_GPU_EXPECTED_NVLINKS_UP",
        "DCGM_FR_NVSWITCH_EXPECTED_NVLINKS_UP",
        "DCGM_FR_FABRIC_MANAGER_TRAINING_ERROR",
        "DCGM_FR_UNCORRECTABLE_ROW_REMAP",
        "DCGM_FR_PENDING_ROW_REMAP",
        "DCGM_FR_PENDING_PAGE_RETIREMENTS",
        "DCGM_FR_DBE_PENDING_PAGE_RETIREMENTS"
    )

    DIAG_WARNINGS = _resolve_error_codes_with_notes(
        ("DCGM_FR_FIELD_QUERY", "Transient DCGM field read failure, not indicative of fault"),
        ("DCGM_FR_RETIRED_PAGES_LIMIT", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_PCIE_H_REPLAY_VIOLATION", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_FIELD_THRESHOLD", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_FIELD_THRESHOLD_TS", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_NVLINK_EFFECTIVE_BER_THRESHOLD", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_NVLINK_SYMBOL_BER_THRESHOLD", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_CONCURRENT_GPUS", "Not indicative of failure"),
        ("DCGM_FR_DCGM_API", "Second order effect - Not root cause"),
        ("DCGM_FR_INTERNAL", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_HIGH_LATENCY", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_LOW_BANDWIDTH", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
        ("DCGM_FR_SRAM_THRESHOLD", "Disputed or undefined Threshold. Use field watches to set custom thresholds"),
    )

    DIAG_IGNORE = _resolve_error_codes(
        "DCGM_FR_XID_ERROR"
    )

    DIAG_SUPPRESSED_NOTE = "Healthagent Note: Low confidence. Suppressed to avoid false positives or duplicated errors that do not represent diagnostic test failures."

    @classmethod
    def count_os_gpu_devices(cls) -> int:

        """
        Count the number of NVIDIA GPU devices in /dev/ directory.
        Only counts devices matching the pattern /dev/nvidia[0-9]+

        Returns:
            int: Number of NVIDIA GPU devices found
        """
        dev_dir = "/dev"
        gpu_count = 0

        # Pattern to match nvidia followed by one or more digits
        pattern = re.compile(r'^nvidia\d+$')

        # List all files in /dev/
        for filename in os.listdir(dev_dir):
            # Check if filename matches nvidia[0-9]+
            if pattern.match(filename):
                gpu_count += 1

        return gpu_count

    @classmethod
    def count_pci_gpu_devices(cls) -> int:
        """
        Count NVIDIA GPU devices via PCI bus in /sys/bus/pci/devices.
        Follows sysfs best practices - directly accesses /sys/bus/pci/devices
        without relying on device links.

        Returns:
            int:  Number of NVIDIA GPU devices found on PCI bus

        References:
        Nvidia uses vendor ID: 10de. https://pci-ids.ucw.cz/
        Exhaustive list of PCI device ID's are found here (very long): /usr/share/misc/pci.ids
        Online reference for Class ID's is found here: https://admin.pci-ids.ucw.cz/read/PD
        This is the reference used by "lspci" command.
        """

        pci_devices_path = Path('/sys/bus/pci/devices')

        if not pci_devices_path.exists():
            return 0

        gpu_count = 0

        # Directly iterate through PCI devices - no device links needed
        for device_path in pci_devices_path.iterdir():
            try:
                # Read vendor ID directly from device directory
                vendor_file = device_path / 'vendor'
                if not vendor_file.exists():
                    continue

                vendor_id = vendor_file.read_text().strip()

                # Check if it's NVIDIA (vendor ID 0x10de)
                if vendor_id != '0x10de':
                    continue

                # Read device class directly from device directory
                class_file = device_path / 'class'
                if not class_file.exists():
                    continue

                device_class = class_file.read_text().strip()

                # Check if it's a GPU:
                # 0x0300xx = VGA controller
                # 0x0302xx = 3D controller (common for datacenter GPUs)
                if device_class.startswith('0x0300') or device_class.startswith('0x0302'):
                    gpu_count += 1

            except (OSError, IOError):
                # Skip devices we can't read (permissions, etc.)
                continue

        return gpu_count

    @classmethod
    def get_throttle_reasons(cls, clock_reason, field_values=None):
        reasons = []

        # This is an indicator of:
        #  - temperature being too high
        #  - External Power Brake Assertion is triggered (e.g. by the system power supply)
        #  - Power draw is too high and Fast Trigger protection is reducing the clocks
        if clock_reason & dcgm_fields.DCGM_CLOCKS_EVENT_REASON_HW_SLOWDOWN:
            reasons.append("Hardware Slowdown in effect due to temperature being too high")

        # SW Thermal Slowdown
        #
        # This is an indicator of one or more of the following:
        #  - Current GPU temperature above the GPU Max Operating Temperature
        #  - Current memory temperature above the Memory Max Operating Temperature
        if clock_reason & dcgm_fields.DCGM_CLOCKS_EVENT_REASON_SW_THERMAL:
            reasons.append("Software Thermal slowdown due to temperature being too high")

        # HW Thermal Slowdown (reducing the core clocks by a factor of 2 or more) is engaged
        #
        # This is an indicator of:
        #  - temperature being too high
        if clock_reason & dcgm_fields.DCGM_CLOCKS_EVENT_REASON_HW_THERMAL:
            reasons.append("Hardware Thermal slowdown due to temperature being too high")

        # HW Power Brake Slowdown (reducing the core clocks by a factor of 2 or more) is engaged
        #
        # This is an indicator of:
        #  - External Power Brake Assertion being triggered (e.g. by the system power supply)
        if clock_reason & dcgm_fields.DCGM_CLOCKS_EVENT_REASON_HW_POWER_BRAKE:
            reasons.append("Hardware Power Brake in effect. Clocks may be reduced by a factor of 2 or more due to external power brake assertion being triggered.")

        return reasons

    @classmethod
    def convert_system_enum_to_system_name(cls, system):
        if system == dcgm_structs.DCGM_HEALTH_WATCH_PCIE:
            return "PCIe"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_NVLINK:
            return "NvLink"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_PMU:
            return "PMU"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_MCU:
            return "MCU"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_MEM:
            return "MEM"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_SM:
            return "SM"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_INFOROM:
            return "Inforom"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_THERMAL:
            return "Thermal"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_POWER:
            return "Power"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_DRIVER:
            return "Driver"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_NVSWITCH_FATAL:
            return "Nvswitch"

        if system == dcgm_structs.DCGM_HEALTH_WATCH_NVSWITCH_NONFATAL:
            return "Nvswitch"

        return "System"

    ## Helper method to convert DCGM value to string
    @classmethod
    def convert_value_to_string(cls, value):
        v = dcgmvalue.DcgmValue(value)

        try:
            if (v.IsBlank()):
                return "N/A"
            else:
                return v.__str__()
        except:
            ## Exception is generally thorwn when int32 is
            ## passed as an input. Use additional methods to fix it
            v = dcgmvalue.DcgmValue(0)
            v.SetFromInt32(value)

            if (v.IsBlank()):
                return "N/A"
            else:
                return v.__str__()

    @classmethod
    def convert_health_to_status(cls, health):
        """
        helper method to convert health return to a string for display purpose
        """
        if health == dcgm_structs.DCGM_HEALTH_RESULT_PASS:
            return HealthStatus.OK
        elif health == dcgm_structs.DCGM_HEALTH_RESULT_WARN:
            return HealthStatus.WARNING
        elif  health == dcgm_structs.DCGM_HEALTH_RESULT_FAIL:
            return HealthStatus.ERROR
        else :
            return HealthStatus.NA

    @classmethod
    def dcgm_condition_to_string(cls, condition):

        if condition == dcgm_structs.DCGM_POLICY_COND_DBE:
            return "Double-bit ECC"
        elif condition == dcgm_structs.DCGM_POLICY_COND_PCI:
            return "PCIe Replays"
        elif condition == dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED:
            return "Max Retired Pages"
        elif condition == dcgm_structs.DCGM_POLICY_COND_THERMAL:
            return "Thermal Violation"
        elif condition == dcgm_structs.DCGM_POLICY_COND_POWER:
            return "Power Violation"
        elif condition == dcgm_structs.DCGM_POLICY_COND_NVLINK:
            return "Nvlink Violation"
        elif condition == dcgm_structs.DCGM_POLICY_COND_XID:
            return "XID Violation"
        else:
            raise ValueError("Unknown condition")

    @classmethod
    def should_ignore_error(cls, diagException):
        if diagException.info:
            if diagException.info.find("MIG configuration is incompatible with the diagnostic because it prevents access to the entire GPU."
        ) != -1:
                return True

            if diagException.info.find("Cannot run diagnostic: CUDA does not support enumerating GPUs with MIG mode enabled") == 0:
                return True

        return False

    @classmethod
    def connect(cls, grp_name: str, test_mode=False):
        ## Initialize the DCGM Engine as automatic operation mode. This is required when connecting
        ## to a "standalone" hostengine (one that is running separately) but can also be done on an
        ## embedded hostengine.  In this mode, fields are updated
        ## periodically based on their configured frequency.
        opMode = dcgm_structs.DCGM_OPERATION_MODE_AUTO
        # create a dcgm handle by connecting to host engine process
        try:
            if test_mode:
                # Connect to DCGM host engine, useful for using DCGM error injection framework for injecting errors and
                # validating healthagent actions.
                dcgmHandle = pydcgm.DcgmHandle(ipAddress="127.0.0.1", opMode=opMode)
            else:
                # Load DCGM library in embedded mode. In embedded mode, DCGM library is loaded as a shared library. This
                # is the production mode.
                dcgmHandle = pydcgm.DcgmHandle(opMode=opMode)

            ## Get a handle to the system level object for DCGM
            dcgmSystem = dcgmHandle.GetSystem()
            supportedGPUs = dcgmSystem.discovery.GetAllSupportedGpuIds()
        except Exception as e:
            raise Wrap.DcgmConnectionFail(e)

        ## Create an empty group. Let's call the group as "one_gpus_group".
        ## We will add the first supported GPU in the system to this group.
        dcgmGroup = pydcgm.DcgmGroup(dcgmHandle, groupName=grp_name, groupType=dcgm_structs.DCGM_GROUP_EMPTY)

        #Skip the test if no supported gpus are available
        if len(supportedGPUs) < 1:
            raise Wrap.DcgmGpuNotFound("Unable to find atleast 1 supported GPU on this system")

        for gpu in supportedGPUs:
            dcgmGroup.AddGpu(gpu)

        dcgmSystem.UpdateAllFields(waitForUpdate=True)
        return dcgmGroup,dcgmHandle

    @classmethod
    def disconnect(cls, handle, grp):
        if grp:
            grp.Delete()
            del(grp)
        if handle:
            del(handle)

    @classmethod
    def set_policy(cls):
        """
        Setup policy violations and thresholds.
        Add required fields for tracking.
        """

        policy = dcgm_structs.c_dcgmPolicy_v1()
        policy.version = dcgm_structs.dcgmPolicy_version1

        # XID errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_XID
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].tag = 1
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].val.llval = 1


        return policy
    @classmethod
    def get_health_mask(cls):
        exclude_mask = dcgm_structs.DCGM_HEALTH_WATCH_THERMAL | dcgm_structs.DCGM_HEALTH_WATCH_POWER
        return dcgm_structs.DCGM_HEALTH_WATCH_ALL & ~exclude_mask
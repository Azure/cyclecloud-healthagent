from ctypes import *
import os
import sys
import asyncio
from healthagent.reporter import HealthStatus
DCGM_VERSION = os.getenv("DCGM_VERSION")

try:
    if DCGM_VERSION < '4.0.0':
        print("DCGM version is less than 4.0.0, which is not supported.")
        raise ImportError("Unsupported DCGM version")
    bind_path = "/usr/share/datacenter-gpu-manager-4/bindings/python3"

    sys.path.append(bind_path)
    import pydcgm
    from dcgm_structs import dcgmExceptionClass
    import dcgm_structs
    import dcgm_fields
    import dcgm_agent
    import dcgmvalue
    import DcgmFieldGroup
except:
    raise ImportError("Unable to find dcgm python binding, is PYTHONPATH set properly?")

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


class Wrap:

    class DcgmConnectionFail(Exception):
        pass

    class DcgmInvalidHandle(Exception):
        pass

    class DcgmGpuNotFound(Exception):
        pass


    class fields():

        GPUTEMP = dcgm_fields.DCGM_FI_DEV_GPU_TEMP
        GPUTEMP_SLOWDOWN = dcgm_fields.DCGM_FI_DEV_SLOWDOWN_TEMP
        GPUTEMP_SHUTDOWN = dcgm_fields.DCGM_FI_DEV_SHUTDOWN_TEMP
        CLOCK_REASON = dcgm_fields.DCGM_FI_DEV_CLOCK_THROTTLE_REASONS
        FABRIC_STATUS = dcgm_fields.DCGM_FI_DEV_FABRIC_MANAGER_STATUS
        FABRIC_ERROR = dcgm_fields.DCGM_FI_DEV_FABRIC_MANAGER_ERROR_CODE
        GPUPOWER = dcgm_fields.DCGM_FI_DEV_POWER_USAGE
        PERSISTENCE_MODE = dcgm_fields.DCGM_FI_DEV_PERSISTENCE_MODE

    @classmethod
    def get_throttle_reasons(cls, clock_reason):
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
    def get_fields(cls):
        return [
            Wrap.fields.CLOCK_REASON,
            Wrap.fields.FABRIC_ERROR,
            Wrap.fields.FABRIC_STATUS,
            Wrap.fields.GPUPOWER,
            Wrap.fields.GPUTEMP,
            Wrap.fields.GPUTEMP_SHUTDOWN,
            Wrap.fields.GPUTEMP_SLOWDOWN,
            Wrap.fields.PERSISTENCE_MODE
        ]

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
    def convert_overall_health_to_string(cls, health):
        """
        helper method to convert helath return to a string for display purpose
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

    # Returns true if the error here should be ignored
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
            raise Wrap.DcgmConnectionFail("Unable to get a DCGM Handle")

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
    def set_policy(cls, mpe):
        """
        Setup policy violations and thresholds.
        Add required fields for tracking.
        """

        policy = dcgm_structs.c_dcgmPolicy_v1()
        policy.version = dcgm_structs.dcgmPolicy_version1

        # Double Bit Errors
        policy.condition = dcgm_structs.DCGM_POLICY_COND_DBE
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_DBE].tag = 0
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_DBE].val.boolean = True

        # PCIe replay errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_PCI
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_PCI].tag = 0
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_PCI].val.llval = 1

        # Nvlink Errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_NVLINK
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_NVLINK].tag = 0
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_NVLINK].val.boolean = True

        # XID errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_XID
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].tag = 0
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].val.boolean = True


        # # Thermal errors
        # policy.condition |= dcgm_structs.DCGM_POLICY_COND_THERMAL
        # policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].tag = 1
        # policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].val.llval = temperature

        # Max pages retired errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].tag = 1
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].val.llval = mpe


        # # Power draw HW errors
        # policy.condition |= dcgm_structs.DCGM_POLICY_COND_POWER
        # policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_POWER].tag = 1
        # policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_POWER].val.llval = power

        return policy

    @classmethod
    def get_health_mask(cls):
        exclude_mask = dcgm_structs.DCGM_HEALTH_WATCH_THERMAL | dcgm_structs.DCGM_HEALTH_WATCH_POWER
        return dcgm_structs.DCGM_HEALTH_WATCH_ALL & ~exclude_mask
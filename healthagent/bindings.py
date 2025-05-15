from ctypes import *
import os
import sys
from time import time
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

# Dynamically expose only the relevant callback function based on DCGM version
class Wrap:

    class DcgmConnectionFail(Exception):
        pass

    class DcgmGpuNotFound(Exception):
        pass

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
    def connect(cls, grp_name: str):
        ## Initialize the DCGM Engine as automatic operation mode. This is required when connecting
        ## to a "standalone" hostengine (one that is running separately) but can also be done on an
        ## embedded hostengine.  In this mode, fields are updated
        ## periodically based on their configured frequency.
        opMode = dcgm_structs.DCGM_OPERATION_MODE_AUTO
        # create a dcgm handle by connecting to host engine process
        try:
            dcgmHandle = pydcgm.DcgmHandle(ipAddress='127.0.0.1', opMode=opMode)

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
    def get_policy_fields(cls):
        return [dcgm_fields.DCGM_FI_DEV_ECC_DBE_VOL_DEV,
                dcgm_fields.DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
                dcgm_fields.DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL,
                dcgm_fields.DCGM_FI_DEV_NVLINK_CRC_DATA_ERROR_COUNT_TOTAL,
                dcgm_fields.DCGM_FI_DEV_NVLINK_REPLAY_ERROR_COUNT_TOTAL,
                dcgm_fields.DCGM_FI_DEV_NVLINK_RECOVERY_ERROR_COUNT_TOTAL,
                dcgm_fields.DCGM_FI_DEV_XID_ERRORS,
                dcgm_fields.DCGM_FI_DEV_GPU_TEMP,
                dcgm_fields.DCGM_FI_DEV_RETIRED_SBE,
                dcgm_fields.DCGM_FI_DEV_POWER_USAGE,
                dcgm_fields.DCGM_FI_DEV_RETIRED_DBE]

    @classmethod
    def set_policy(cls, temperature, mpe, power):
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


        # Thermal errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_THERMAL
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].tag = 1
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].val.llval = temperature

        # Max pages retired errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].tag = 1
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].val.llval = mpe


        # Power draw HW errors
        policy.condition |= dcgm_structs.DCGM_POLICY_COND_POWER
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_POWER].tag = 1
        policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_POWER].val.llval = power

        return policy
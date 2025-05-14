import asyncio
import sys
import json
import time
import logging
import os
from time import time
from healthagent import epilog,status
from healthagent.AsyncScheduler import AsyncScheduler,Priority
from dataclasses import asdict
from healthagent.reporter import Reporter, HealthReport,HealthStatus
from healthagent.bindings import *

log = logging.getLogger('healthagent')

class GpuHealthChecksException(Exception):
    pass

class GpuNotFoundException(Exception):
    pass
class GpuHealthChecks:

    def __init__(self, reporter: Reporter):

        self.dcgmGroup = None
        self.dcgmHandle = None

        self.reporter = reporter
        self.policy = None
        self.policy_fields = []
        self.watch_fields = []
        self.gpu_config = []
        # Right now we are only looking for nvidia devices.
        if not os.path.exists("/dev/nvidia0"):
            log.info("GPU devices not found, skipping GPU checks")
            raise GpuNotFoundException("No Gpu's Found, Skipping GPU HealthChecks")
        ## Initialize the DCGM Engine as automatic operation mode. This is required when connecting
        ## to a "standalone" hostengine (one that is running separately) but can also be done on an
        ## embedded hostengine.  In this mode, fields are updated
        ## periodically based on their configured frequency.
        self.opMode = dcgm_structs.DCGM_OPERATION_MODE_AUTO
        # create a dcgm handle by connecting to host engine process
        try:
            self.dcgmHandle = pydcgm.DcgmHandle(ipAddress='127.0.0.1', opMode=self.opMode)

            ## Get a handle to the system level object for DCGM
            self.dcgmSystem = self.dcgmHandle.GetSystem()
            self.supportedGPUs = self.dcgmSystem.discovery.GetAllSupportedGpuIds()
        except Exception as e:
            log.debug(f"Unable to get dcgm handle {e}")
            raise GpuHealthChecksException("Unable to get a DCGM Handle")

        ## Create an empty group. Let's call the group as "one_gpus_group".
        ## We will add the first supported GPU in the system to this group.
        self.dcgmGroup = pydcgm.DcgmGroup(self.dcgmHandle, groupName="one_gpu_group", groupType=dcgm_structs.DCGM_GROUP_EMPTY)

        #Skip the test if no supported gpus are available
        if len(self.supportedGPUs) < 1:
            log.debug("Unable to find atleast 1 supported GPU on this system")
            raise GpuHealthChecksException("Unable to find atleast 1 supported GPU on this system")

        for gpu in self.supportedGPUs:
            self.dcgmGroup.AddGpu(gpu)
        log.debug("Initialized DCGM Monitor")
        log.debug("Number of GPU's: %d" % len(self.supportedGPUs))

        #TODO: Do we need to enable persistence mode?
        ## Trigger field updates since we just started DCGM (always necessary in MANUAL mode to get recent values)
        self.dcgmSystem.UpdateAllFields(waitForUpdate=True)

        ## Get the current configuration for the group
        self.gpu_config = self.dcgmGroup.config.Get(dcgm_structs.DCGM_CONFIG_CURRENT_STATE)
        self.__display_gpu_config()
        self.setup_dcgm_policy()
        self.setup_background_watches()
        log.debug("Initialized GPU Healthchecks")

    def setup_dcgm_policy(self):
        """
        Setup policy violations and thresholds.
        Add required fields for tracking.
        """

        ## Field Id's for double bit ECC
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_ECC_DBE_VOL_DEV)
        # Device memory double bit volatile ECC errors
        self.policy = dcgm_structs.c_dcgmPolicy_v1()
        self.policy.version = dcgm_structs.dcgmPolicy_version1
        self.policy.condition = dcgm_structs.DCGM_POLICY_COND_DBE
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_DBE].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_DBE].val.boolean = True

        ## Field Id's for PCIe errors
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_PCIE_REPLAY_COUNTER)
        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_PCI
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_PCI].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_PCI].val.llval = 1

        ## Field Id's for NVLink errors
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL)
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_NVLINK_CRC_DATA_ERROR_COUNT_TOTAL)
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_NVLINK_REPLAY_ERROR_COUNT_TOTAL)
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_NVLINK_RECOVERY_ERROR_COUNT_TOTAL)
        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_NVLINK
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_NVLINK].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_NVLINK].val.boolean = True

        ## Field Id's for XID Errors
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_XID_ERRORS)
        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_XID
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].val.boolean = True

        ## Field Id's for thermal violations
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_GPU_TEMP)
        # Above 85 degrees GPU performance begins to drop significantly leading to shutdown so we set the threshold to 90% of that.
        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_THERMAL
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].tag = 1
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].val.llval = 76

        ## Field Id for retired pages
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_RETIRED_SBE)
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_RETIRED_DBE)
        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].tag = 1
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].val.llval = 8

        ## Field Id for power usage
        self.policy_fields.append(dcgm_fields.DCGM_FI_DEV_POWER_USAGE)
        # Set violation to 90% of allowed maximum power limit.
        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_POWER
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_POWER].tag = 1
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_POWER].val.llval = int(0.9 * (self.gpu_config[0].mPowerLimit.val))

        self.dcgmGroup.policy.Set(self.policy)
        self.c_callback = create_c_callback(self.handle_policy_violation, asyncio.get_running_loop())
        self.dcgmGroup.policy.Register(self.policy.condition, self.c_callback, None)
        log.debug("Applied GPU violation Policies")

    def setup_background_watches(self):
        """
        Setup field watches and health watches.
        """
        # TODO: Add more fields to watch fields in addition to policy fields
        # but for now just add policy fields.
        if self.policy_fields:
            self.watch_fields = self.policy_fields
        self.field_group = DcgmFieldGroup.DcgmFieldGroup(self.dcgmHandle, name="ccfield_group", fieldIds=self.watch_fields)
        ## Add the health watches
        self.dcgmGroup.health.Set(dcgm_structs.DCGM_HEALTH_WATCH_ALL)

    def __display_gpu_config(self):

        ## Invoke method to get gpu IDs of the members of the newly-created group
        groupGpuIds = self.dcgmGroup.GetGpuIds()
        ## Display current configuration for the group
        for x in range(0, len(groupGpuIds)):
            log.debug("GPU Id      : %d" % (self.gpu_config[x].gpuId))
            log.debug("Ecc  Mode   : %s" % (self.convert_value_to_string(self.gpu_config[x].mEccMode)))
            log.debug("Sync Boost  : %s" % (self.convert_value_to_string(self.gpu_config[x].mPerfState.syncBoost)))
            log.debug("Mem Clock   : %s" % (self.convert_value_to_string(self.gpu_config[x].mPerfState.targetClocks.memClock)))
            log.debug("SM  Clock   : %s" % (self.convert_value_to_string(self.gpu_config[x].mPerfState.targetClocks.smClock)))
            log.debug("Power Limit : %s" % (self.convert_value_to_string(self.gpu_config[x].mPowerLimit.val)))
            log.debug("Compute Mode: %s" % (self.convert_value_to_string(self.gpu_config[x].mComputeMode)))

    async def create(self):
        await self.reporter.clear_all_errors()
        log.debug("Adding periodic background healthchecks")
        await AsyncScheduler.add_periodic_task(time(), 60, Priority.HARDWARE_CHECKS_POLL, self.run_background_healthchecks)

    async def handle_policy_violation(self, callbackresp):

        health_system = "GPUPolicyChecks"
        report = self.reporter.get_report(health_system) or HealthReport()
        condition = callbackresp.condition
        gpuid = callbackresp.gpuId
        try:
            condition_str = self.dcgm_condition_to_string(condition=condition)
        except ValueError as e:
            log.exception(e)
            return

        log.critical("Violation detected: %s  on gpu: %d" % (condition_str, gpuid))

        if condition_str not in report.custom_fields:
            report.custom_fields[condition_str] = {}
            # violation data is a 2D dict containing violation results indexed by condition and gpu id. vd[condition][gpuid]
            vd = {}
        else:
            vd = report.custom_fields[condition_str]

        #vd['timestamp'] = condition.val.timestamp
        if gpuid not in vd:
            vd[gpuid] = {}

        info = vd[gpuid]
        if condition == dcgm_structs.DCGM_POLICY_COND_DBE:
            if 'location' not in info:
                info['location'] = set()
            info['location'].add(next(key for key, value in dcgm_structs.c_dcgmPolicyConditionDbe_t.LOCATIONS.items() if value == callbackresp.val.dbe.location))
            info['numerrors'] = callbackresp.val.dbe.numerrors
            violation_descr = f"Double-Bit ECC errors({info['numerrors']}) found at location: {info['location']} on GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_PCI:
            info['replay_count'] = callbackresp.val.pci.counter
            violation_descr = f"PCI replay count({info['replay_count']}) on GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_NVLINK:
            info['error_count'] = callbackresp.val.nvlink.counter
            if 'field_id' not in info:
                info['field_id'] = set()
            info['field_id'].add(callbackresp.val.nvlink.fieldId)
            violation_descr =f"Nvlink violation on GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_XID:
            if 'xid_error' not in info:
                info['xid_error'] = set()
            info['xid_error'].add(callbackresp.val.xid.errnum)
            violation_descr = f"XID errors found: XID {info['xid_error']} on GPU {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_THERMAL:
            info['temperature'] = callbackresp.val.thermal.thermalViolation
            violation_descr = f"Thermal violation detected: Temperature reached {info['temperature']} Celsius GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_POWER:
            info['power'] = callbackresp.val.power.powerViolation
            violation_descr = f"Power violation detected: Power draw {info['power']} Watts GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED:
            info['sbepage_count'] = callbackresp.val.mpr.sbepages
            info['dbepage_count'] = callbackresp.val.mpr.dbepages
            violation_descr = f"Max retired pages violation: SBE retired pages: {info['sbepage_count']}, DBE retired pages {info['dbepage_count']} GPU: {gpuid}"

        vd[gpuid] = info
        report.custom_fields[condition_str] = vd

        report.status = HealthStatus.ERROR
        report.description = "GPU Policy Violations detected"
        if not report.details:
            report.details = violation_descr
        else:
            report.details += "\n"
            #TODO Fix
            report.details += violation_descr
        log.debug(asdict(report))
        await self.reporter.update_report(name=health_system, report=report)
        return

    async def track_fields(self):

        try:
            response = self.dcgmGroup.samples.GetLatest(self.field_group)
            for gpu in response.values:
                log.debug(f"Field structure per gpu: {response.values[gpu]}")
                for field in self.watch_fields:
                    log.debug(f"field name: {field}")
                    log.debug(f"field value: {response.values[gpu][field][0].value}")
        except Exception as e:
            log.exception(e)

    def convert_system_enum_to_system_name(self, system):
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
    def convert_value_to_string(self, value):
        v = dcgmvalue.DcgmValue(value)

        try:
            if (v.IsBlank()):
                return "N/A"
            else:
                return v.__str__()
        except:
            ## Exception is generally thorwn when int32 is
            ## passed as an input. Use additional methods to fix it
            sys.exc_clear()
            v = dcgmvalue.DcgmValue(0)
            v.SetFromInt32(value)

            if (v.IsBlank()):
                return "N/A"
            else:
                return v.__str__()

    def convert_overall_health_to_string(self, health):
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

    def dcgm_diag_test_didnt_pass(self, rc):
        if rc == dcgm_structs.DCGM_DIAG_RESULT_FAIL or rc == dcgm_structs.DCGM_DIAG_RESULT_WARN:
            return True
        else:
            return False

    def dcgm_diag_test_index_to_name(self, index):
        if index == dcgm_structs.DCGM_SWTEST_DENYLIST:
            return "denylist"
        elif index == dcgm_structs.DCGM_SWTEST_NVML_LIBRARY:
            return "nvmlLibrary"
        elif index == dcgm_structs.DCGM_SWTEST_CUDA_MAIN_LIBRARY:
            return "cudaMainLibrary"
        elif index == dcgm_structs.DCGM_SWTEST_CUDA_RUNTIME_LIBRARY:
            return "cudaRuntimeLibrary"
        elif index == dcgm_structs.DCGM_SWTEST_PERMISSIONS:
            return "permissions"
        elif index == dcgm_structs.DCGM_SWTEST_PERSISTENCE_MODE:
            return "persistenceMode"
        elif index == dcgm_structs.DCGM_SWTEST_ENVIRONMENT:
            return "environment"
        elif index == dcgm_structs.DCGM_SWTEST_PAGE_RETIREMENT:
            return "pageRetirement"
        elif index == dcgm_structs.DCGM_SWTEST_GRAPHICS_PROCESSES:
            return "graphicsProcesses"
        elif index == dcgm_structs.DCGM_SWTEST_INFOROM:
            return "inforom"
        else:
            raise dcgm_structs.DCGMError(dcgm_structs.DCGM_ST_BADPARAM)

    def dcgm_condition_to_string(self, condition):

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

    def dcgm_error_category_to_string(self, category):
        dcgm_error_category_map = {
            0: "NONE",
            1: "Performance Threshold",
            2: "Performance Violation",
            3: "Software Configuration",
            4: "Software Library",
            5: "Software XID",
            6: "Software Cuda",
            7: "Software EUD",
            8: "Software Other",
            9: "Hardware Thermal",
            10: "Hardware Memory",
            11: "Hardware NvLink",
            12: "Hardware NvSwitch",
            13: "Hardware PCIe",
            14: "Hardware Power",
            15: "Hardware Other",
            16: "Internal Other"
        }
        return dcgm_error_category_map.get(category, "Unknown Category")

    def dcgm_error_severity_to_string(self, severity):
        dcgm_error_severity_map = {
            0: "NONE",
            1: "Can perform workload, but needs to be monitored",
            2: "Cannot perform workload. GPU should be isolated",
            3: "This error code is not recognized",
            4: "This error should be triaged",
            5: "This error can be configured",
            6: "Drain and reset GPU"
        }
        return dcgm_error_severity_map.get(severity, "Unknown Severity")

    # Returns true if the error here should be ignored
    def should_ignore_error(self, diagException):
        if diagException.info:
            if diagException.info.find("MIG configuration is incompatible with the diagnostic because it prevents access to the entire GPU."
        ) != -1:
                return True

            if diagException.info.find("Cannot run diagnostic: CUDA does not support enumerating GPUs with MIG mode enabled") == 0:
                return True

        return False

    async def run_background_healthchecks(self):
        """
        Invoke Health checks periodically.
        These are safe to run constantly alongside jobs.
        """

        health_system = f"BackgroundGPUHealthChecks"
        custom_fields = {}
        try:
            try:
                details = list()
                subsystems = set()
                group_health = self.dcgmGroup.health.Check()
                status = self.convert_overall_health_to_string(group_health.overallHealth)
                error_count = group_health.incidentCount

                if status == HealthStatus.OK and error_count == 0:
                    report = HealthReport()
                    await self.reporter.update_report(name=health_system, report=report)
                    return
                elif status == HealthStatus.NA:
                    log.error("Invalid health status received")
                    return

                for index in range (0, error_count):
                    gpu_id = group_health.incidents[index].entityInfo.entityId
                    system = self.convert_system_enum_to_system_name(group_health.incidents[index].system)
                    subsystems.add(system)
                    details.append(group_health.incidents[index].error.msg)

                description=f"{health_system} report {status.value} count={error_count} subsystem={', '.join(subsystems)}"
                custom_fields['categories'] = subsystems
                custom_fields['error_count'] = error_count
                report = HealthReport(status=status,
                                      description=description,
                                      details='\n'.join(details),
                                      custom_fields=custom_fields)
                await self.reporter.update_report(name=health_system, report=report)
                return
            except dcgm_structs.DCGMError as e:
                errorCode = e.value
                log.error("dcgmHealthCheck returned error %d: %s" % (errorCode, e))
                sys.exc_clear()
        except Exception as e:
            log.exception(f"{e}")

    @epilog
    async def run_active_healthchecksv2(self):

        """
        Run active healthchecks, before or after a job.
        These checks do require exclusive access to the GPU's and cannot
        be run alongside jobs.
        """
        health_system = f"ActiveDCGMHealthChecks"
        DIAG_LEVEL=dcgm_structs.DCGM_DIAG_LVL_MED
        isHealthy = True
        report = HealthReport()
        custom_fields = {}

        try:
            response = self.dcgmGroup.action.RunDiagnostic(DIAG_LEVEL)
        except dcgmExceptionClass(dcgm_structs.DCGM_ST_NOT_CONFIGURED):
            log.error("One of the GPUs on your system is not supported by NVVS")
        except dcgmExceptionClass(dcgm_structs.DCGM_ST_GROUP_INCOMPATIBLE):
            log.error("GPUs in the group are not compatible with each other for running diagnostics")
        except dcgmExceptionClass(dcgm_structs.DCGM_ST_NVVS_ERROR) as e:
            if not self.should_ignore_error(e):
               raise(e)
            else:
                log.error(str(e))
        test_types = set()
        failures = list()
        if response.numErrors > 0:
            isHealthy = False
            for errIdx in range(response.numErrors):
                curErr = response.errors[errIdx]
                error_msg = curErr.msg
                if curErr.testId == dcgm_structs.DCGM_DIAG_RESPONSE_SYSTEM_ERROR:
                    testName = "System"
                    failures.append(f"System Error: {error_msg}")
                    test_types.add(testName)
                elif curErr.entity.entityGroupId == dcgm_fields.DCGM_FE_GPU:
                    testName = response.tests[curErr.testId].name
                    gpuId = curErr.entity.entityId
                    failures.append(f"GPU: {gpuId}, Test name: {testName}, Error: {error_msg}")
                    test_types.add(testName)
                else:
                    failures.append(f"Test name: {testName}, Error: {error_msg}")
        if response.numInfo > 0:
            for i in range(response.numInfo):
                info = response.info[i]
                testName = response.tests[info.testId].name
                log.debug(f"Test: {testName}, Info: {info.msg}")

        if not isHealthy:
            custom_fields['failures'] = test_types
            custom_fields['error_count'] = len(failures)
            report.status = HealthStatus.ERROR
            report.details = "\n".join(failures)
            report.description = f"DCGM Epilog failures in {', '.join(test_types)}"
            report.message = "GPU Epilog Errors"
            report.custom_fields = custom_fields
        await self.reporter.update_report(name=health_system, report=report)
        return report.view()

    async def run_active_healthchecks(self):
        """
        Run active healthchecks, before or after a job.
        These checks do require exclusive access to the GPU's and cannot
        be run alongside jobs.
        """
        health_system = f"ActiveDCGMHealthChecks"
        DIAG_LEVEL=dcgm_structs.DCGM_DIAG_LVL_MED
        isHealthy = True
        report = HealthReport()
        errors = []
        failed_tests = []
        custom_fields = {}

        try:
            response = self.dcgmGroup.action.RunDiagnostic(DIAG_LEVEL)
        except dcgmExceptionClass(dcgm_structs.DCGM_ST_NOT_CONFIGURED):
            log.error("One of the GPUs on your system is not supported by NVVS")
        except dcgmExceptionClass(dcgm_structs.DCGM_ST_GROUP_INCOMPATIBLE):
            log.error("GPUs in the group are not compatible with each other for running diagnostics")
        except dcgmExceptionClass(dcgm_structs.DCGM_ST_NVVS_ERROR) as e:
            if not self.should_ignore_error(e):
               raise(e)
            else:
                log.error(str(e))

        for i in range(0, response.levelOneTestCount):
            if self.dcgm_diag_test_didnt_pass(response.levelOneResults[i].result):
                failed_tests.append(self.dcgm_diag_test_index_to_name(i))
                isHealthy = False

        log.debug("Failed Tests: %s" % ", ".join(failed_tests))
        log.debug("Per GPU Results: %d" % response.gpuCount)
        categories = set()
        for i in range(0, response.gpuCount):
            for j in range(0, len(response.perGpuResponses[i].results)):
                if self.dcgm_diag_test_didnt_pass(response.perGpuResponses[i].results[j].result):
                    for k in range(0, len(response.perGpuResponses[i].results[j].error)):
                        error_category = self.dcgm_error_category_to_string(response.perGpuResponses[i].results[j].error[k].category)
                        categories.add(error_category)
                        if error_category == "NONE":
                            continue
                        severity_message = self.dcgm_error_severity_to_string(response.perGpuResponses[i].results[j].error[k].severity)
                        message = response.perGpuResponses[i].results[j].error[k].msg
                        gpu_id = response.perGpuResponses[i].results[j].error[k].gpuId
                        code = response.perGpuResponses[i].results[j].error[k].code
                        msg= f"Found errors: {error_category}, Suggested Action: {severity_message}, Additional Info: {message}"
                        isHealthy = False
                        errors.append(msg)
        if not isHealthy:
            custom_fields['categories'] = categories
            custom_fields['error_count'] = len(errors)
            report.status=HealthStatus.ERROR
            report.details = json.dumps(errors, indent=4)
            report.description = f"DCGM Test Failures: {', '.join(failed_tests)}"
            report.custom_fields = custom_fields

        await self.reporter.update_report(name=health_system, report=report)
        return report.view()


    @status
    def show_status(self):
        return self.reporter.summarize()

    def __del__(self):
        ## Delete the group
        if self.dcgmGroup:
            self.dcgmGroup.Delete()

        if self.dcgmHandle:
            ## disconnect from the hostengine by deleting the DcgmHandle object
            del(self.dcgmHandle)
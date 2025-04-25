import asyncio
import sys
import json
import time
import logging
from time import time
from healthagent.AsyncScheduler import AsyncScheduler,Priority

from healthagent.reporter import Reporter, HealthReport,HealthStatus
from healthagent.bindings import *

log = logging.getLogger(__name__)

class GpuHealthChecksException(Exception):
    pass

class GpuHealthChecks:

    def __init__(self):

        ## Initialize the DCGM Engine as automatic operation mode. This is required when connecting
        ## to a "standalone" hostengine (one that is running separately) but can also be done on an
        ## embedded hostengine.  In this mode, fields are updated
        ## periodically based on their configured frequency.  When watching new fields you must still manually
        ## trigger an update if you wish to view these new fields' values right away.
        self.opMode = dcgm_structs.DCGM_OPERATION_MODE_AUTO
        # create a dcgm handle by connecting to host engine process
        self.dcgmGroup = None
        self.dcgmHandle = None
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
        ## Invoke method to get gpu IDs of the members of the newly-created group
        #groupGpuIds = dcgmGroup.GetGpuIds()

        ## Trigger field updates since we just started DCGM (always necessary in MANUAL mode to get recent values)
        self.dcgmSystem.UpdateAllFields(waitForUpdate=True)

        ## Get the current configuration for the group
        #config_values = dcgmGroup.config.Get(dcgm_structs.DCGM_CONFIG_CURRENT_STATE)

        ## Add the health watches
        self.dcgmGroup.health.Set(dcgm_structs.DCGM_HEALTH_WATCH_ALL)

        ## Setting self.Policy
        self.policy = dcgm_structs.c_dcgmPolicy_v1()
        self.policy.version = dcgm_structs.dcgmPolicy_version1
        self.policy.condition = dcgm_structs.DCGM_POLICY_COND_DBE
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_DBE].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_DBE].val.boolean = True

        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_PCI
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_PCI].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_PCI].val.llval = 1

        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_NVLINK
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_NVLINK].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_NVLINK].val.boolean = True

        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_XID
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].tag = 0
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_XID].val.boolean = True

        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_THERMAL
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].tag = 1
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL].val.llval = 90

        self.policy.condition |= dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].tag = 1
        self.policy.parms[dcgm_structs.DCGM_POLICY_COND_IDX_MAX_PAGES_RETIRED].val.boolean = True


        self.dcgmGroup.policy.Set(self.policy)
        self.c_callback = create_c_callback(self.handle_policy_violation, asyncio.get_running_loop())
        self.dcgmGroup.policy.Register(self.policy.condition, self.c_callback, None)
        self.reporter = Reporter()
        log.debug("Initialized GPU Healthchecks")

    async def create(self):
        log.debug("Adding periodic background healthchecks")
        await AsyncScheduler.add_periodic_task(time(), 60, Priority.HARDWARE_CHECKS_POLL, self.run_background_healthchecks)

    async def handle_policy_violation(self, callbackresp):
        condition = callbackresp.condition
        log.error("Violation detected: %s" % self.dcgm_condition_to_string(condition=condition))

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
            return "PCIe replays"
        elif condition == dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED:
            return "Maximum number of retired pages"
        elif condition == dcgm_structs.DCGM_POLICY_COND_IDX_THERMAL:
            return "Thermal self.policy violation"
        elif condition == dcgm_structs.DCGM_POLICY_COND_POWER:
            return "Power self.policy violation"
        elif condition == dcgm_structs.DCGM_POLICY_COND_NVLINK:
            return "Nvlink self.policy violation"
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
                details = {}
                subsystems = set()
                group_health = self.dcgmGroup.health.Check()
                status = self.convert_overall_health_to_string(group_health.overallHealth)
                details['Health'] = status.value
                details["Incidents"] = group_health.incidentCount

                if status == HealthStatus.OK and details["Incidents"] == 0:
                    report = HealthReport()
                    await self.reporter.update_report(name=health_system, report=report)
                    return
                elif status == HealthStatus.NA:
                    log.error("Invalid health status received")
                    return

                for index in range (0, group_health.incidentCount):
                    gpu_id = group_health.incidents[index].entityInfo.entityId
                    if gpu_id not in details:
                        details[gpu_id] = {}
                    system = self.convert_system_enum_to_system_name(group_health.incidents[index].system)
                    subsystems.add(system)
                    if system not in details[gpu_id]:
                        details[gpu_id][system] = []
                    details[gpu_id][system].append(group_health.incidents[index].error.msg)

                description=f"{health_system} {details['Health']} count={details['Incidents']} subsystem={subsystems}"
                custom_fields['subsystems'] = subsystems
                custom_fields['error_count'] = details["Incidents"]
                report = HealthReport(status=status,
                                      description=description,
                                      details=json.dumps(details, indent=4),
                                      custom_fields=custom_fields)
                await self.reporter.update_report(name=health_system, report=report)
                return
            except dcgm_structs.DCGMError as e:
                errorCode = e.value
                log.error("dcgmHealthCheck returned error %d: %s" % (errorCode, e))
                sys.exc_clear()
        except Exception as e:
            log.exception(f"{e}")


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
        for i in range(0, response.gpuCount):
            for j in range(0, len(response.perGpuResponses[i].results)):
                if self.dcgm_diag_test_didnt_pass(response.perGpuResponses[i].results[j].result):
                    for k in range(0, len(response.perGpuResponses[i].results[j].error)):
                        error_category = self.dcgm_error_category_to_string(response.perGpuResponses[i].results[j].error[k].category)
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
            report.status=HealthStatus.ERROR
            report.details = json.dumps(errors, indent=4)
            report.description = f"DCGM Diagnostic Test Failures: {', '.join(failed_tests)}"

        await self.reporter.update_report(name=health_system, report=report)
        return



    def __del__(self):
        ## Delete the group
        if self.dcgmGroup:
            self.dcgmGroup.Delete()

        if self.dcgmHandle:
            ## disconnect from the hostengine by deleting the DcgmHandle object
            del(self.dcgmHandle)
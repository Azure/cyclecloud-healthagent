import asyncio
import sys
import json
import time
import logging
import os
from time import time
from healthagent import epilog,status
from healthagent.scheduler import Scheduler
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

        self.reporter = reporter

        # TODO: Move this to config file
        self.test_mode = os.getenv('DCGM_TEST_MODE', 'false').lower() == 'true'

        # Right now we are only looking for nvidia devices.
        if not os.path.exists("/dev/nvidia0"):
            log.info("GPU devices not found, skipping GPU checks")
            raise GpuNotFoundException("No Gpu's Found, Skipping GPU HealthChecks")
        try:
            self.setup()
        except Wrap.DcgmConnectionFail:
            raise GpuHealthChecksException
        except Wrap.DcgmGpuNotFound:
            raise GpuNotFoundException
        log.debug("Initialized GPU Healthchecks")

    def setup(self):

        self.dcgmGroup = None
        self.dcgmHandle = None
        self.watch_fields = []
        self.gpu_config = []
        if self.test_mode:
            log.info("Running GPU tests in DCGM_TEST_MODE")
        self.dcgmGroup, self.dcgmHandle = Wrap.connect(grp_name="healthagent_group", test_mode=self.test_mode)
        ## Get the current configuration for the group
        self.gpu_config = self.dcgmGroup.config.Get(dcgm_structs.DCGM_CONFIG_CURRENT_STATE)
        self.setup_dcgm_policy()
        self.setup_background_watches()
        self.__display_gpu_config()

    def setup_dcgm_policy(self):
        """
        Setup policy violations and thresholds.
        Add required fields for tracking.
        """

        # TODO: These limits will come from a configuration file eventually.
        policy = Wrap.set_policy(mpe=8)
        self.dcgmGroup.policy.Set(policy)
        self.c_callback = create_c_callback(self.handle_policy_violation, asyncio.get_running_loop())
        self.dcgmGroup.policy.Register(policy.condition, self.c_callback, None)
        log.debug("Applied GPU violation Policies")

    def setup_background_watches(self):
        """
        Setup field watches and health watches.
        """
        # TODO: Add more fields to watch fields in addition to policy fields
        # but for now just add policy fields.
        self.watch_fields = Wrap.get_fields()
        self.field_group = DcgmFieldGroup.DcgmFieldGroup(self.dcgmHandle, name="ccfield_group", fieldIds=self.watch_fields)
        self.dcgmGroup.samples.WatchFields(fieldGroup=self.field_group, updateFreq=1000000, maxKeepAge=3600.00, maxKeepSamples=0)
        ## Add the health watches
        self.dcgmGroup.health.Set(Wrap.get_health_mask())

    def __display_gpu_config(self):

        ## Invoke method to get gpu IDs of the members of the newly-created group
        groupGpuIds = self.dcgmGroup.GetGpuIds()
        ## Display current configuration for the group
        for x in range(0, len(groupGpuIds)):
            log.debug("GPU Id      : %d" % (self.gpu_config[x].gpuId))
            log.debug("Ecc  Mode   : %s" % (Wrap.convert_value_to_string(self.gpu_config[x].mEccMode)))
            log.debug("Sync Boost  : %s" % (Wrap.convert_value_to_string(self.gpu_config[x].mPerfState.syncBoost)))
            log.debug("Mem Clock   : %s" % (Wrap.convert_value_to_string(self.gpu_config[x].mPerfState.targetClocks.memClock)))
            log.debug("SM  Clock   : %s" % (Wrap.convert_value_to_string(self.gpu_config[x].mPerfState.targetClocks.smClock)))
            log.debug("Power Limit : %s" % (Wrap.convert_value_to_string(self.gpu_config[x].mPowerLimit.val)))
            log.debug("Compute Mode: %s" % (Wrap.convert_value_to_string(self.gpu_config[x].mComputeMode)))

    async def create(self):
        await self.reporter.clear_all_errors()
        log.debug("Adding periodic background healthchecks")
        Scheduler.add_task(self.run_background_healthchecks)

    async def handle_policy_violation(self, callbackresp):

        health_system = "GPUPolicyChecks"
        report = self.reporter.get_report(health_system) or HealthReport()
        condition = callbackresp.condition
        gpuid = callbackresp.gpuId
        try:
            condition_str = Wrap.dcgm_condition_to_string(condition=condition)
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
        status = HealthStatus.ERROR
        if condition == dcgm_structs.DCGM_POLICY_COND_DBE:
            if 'location' not in info:
                info['location'] = set()
            info['location'].add(next(key for key, value in dcgm_structs.c_dcgmPolicyConditionDbe_t.LOCATIONS.items() if value == callbackresp.val.dbe.location))
            info['numerrors'] = callbackresp.val.dbe.numerrors
            info['details'] = f"Double-Bit ECC errors({info['numerrors']}) found at location: {info['location']} on GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_PCI:
            info['replay_count'] = callbackresp.val.pci.counter
            info['details'] = f"PCI replay count({info['replay_count']}) on GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_NVLINK:
            info['error_count'] = callbackresp.val.nvlink.counter
            if 'field_id' not in info:
                info['field_id'] = set()
            info['field_id'].add(callbackresp.val.nvlink.fieldId)
            info['details'] = f"Nvlink violation on GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_XID:
            if 'xid_error' not in info:
                info['xid_error'] = set()
            info['xid_error'].add(callbackresp.val.xid.errnum)
            info['details'] = f"XID errors found: XID {info['xid_error']} on GPU {gpuid}"
        # elif condition == dcgm_structs.DCGM_POLICY_COND_THERMAL:
        #     status = HealthStatus.WARNING
        #     info['temperature'] = callbackresp.val.thermal.thermalViolation
        #     info['details'] = f"Thermal violation detected: Temperature reached {info['temperature']} Celsius GPU: {gpuid}"
        # elif condition == dcgm_structs.DCGM_POLICY_COND_POWER:
        #     status = HealthStatus.WARNING
        #     info['power'] = callbackresp.val.power.powerViolation
        #     info['details'] = f"Power violation detected: Power draw {info['power']} Watts GPU: {gpuid}"
        elif condition == dcgm_structs.DCGM_POLICY_COND_MAX_PAGES_RETIRED:
            info['sbepage_count'] = callbackresp.val.mpr.sbepages
            info['dbepage_count'] = callbackresp.val.mpr.dbepages
            info['details'] = f"Max retired pages violation: SBE retired pages: {info['sbepage_count']}, DBE retired pages {info['dbepage_count']} GPU: {gpuid}"

        vd[gpuid] = info
        report.custom_fields[condition_str] = vd

        report.status = status
        report.description = "GPU Policy Violations detected"
        if not report.details:
            report.details = info['details']
        else:
            # regenerate
            report.details = ""
            for condition in report.custom_fields:
                for gpu in report.custom_fields[condition]:
                    report.details += f"\n{report.custom_fields[condition][gpu]['details']}"
        await self.reporter.update_report(name=health_system, report=report)
        return


    def track_fields(self):

        """
        Read field values from DCGM.
        """

        errors = []
        category = []
        try:
            response = self.dcgmGroup.samples.GetLatest(self.field_group)
            for gpu in response.values:

                curr_temp = response.values[gpu][Wrap.fields.GPUTEMP][0].value
                slowdown_temp = response.values[gpu][Wrap.fields.GPUTEMP_SLOWDOWN][0].value
                if curr_temp >= (0.95*slowdown_temp):
                    msg = f"GPU temperature reached very close to the slowdown limit gpu: {gpu} Curr temp: {curr_temp}, slowdown limit: {slowdown_temp}"
                    errors.append(msg)
                    category.append("Thermal")

                clock_reason = response.values[gpu][Wrap.fields.CLOCK_REASON][0].value
                throttle_reasons = Wrap.get_throttle_reasons(clock_reason)
                if throttle_reasons:
                    errors.extend(throttle_reasons)
                    category.append("Clocks")
                fabric_status = response.values[gpu][Wrap.fields.FABRIC_STATUS][0].value
                if fabric_status == dcgm_structs.DcgmFMStatusFailure:
                    fabric_error = response.values[gpu][Wrap.fields.FABRIC_ERROR][0].value
                    msg = f"Fabric Manager finished training but failed, error code: {fabric_error}"
                    errors.append(msg)
                    category.append("FabricManager")
                elif fabric_status == dcgm_structs.DcgmFMStatusInProgress:
                    fabric_error = response.values[gpu][Wrap.fields.FABRIC_ERROR][0].value
                    msg = f"Fabric Manager not running, training in progress, error code: {fabric_error}"
                    errors.append(msg)
                    category.append("FabricManager")
                persistence_mode = response.values[gpu][Wrap.fields.PERSISTENCE_MODE][0].value
                if persistence_mode != 1:
                    msg = f"Persistence Mode not set for GPU: {gpu}, Restart nvidia-persistenced or Reboot the system."
                    errors.append(msg)
                    category.append("System")

            return errors, category
        except Exception as e:
            log.exception(e)

    @epilog
    async def run_epilog(self):
        health_system = f"ActiveGPUHealthChecks"
        report = await Scheduler.add_task(run_active_healthchecksv2)
        await self.reporter.update_report(name=health_system, report=report)
        response = {}
        response[health_system] = report.view()
        return response

    @Scheduler.periodic(60)
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
                if not self.dcgmGroup:
                    raise Wrap.DcgmInvalidHandle
                group_health = self.dcgmGroup.health.Check()
                status = Wrap.convert_overall_health_to_string(group_health.overallHealth)
                error_count = group_health.incidentCount

                field_errors, category = self.track_fields()
                if len(field_errors) > 0:
                    status = HealthStatus.ERROR
                if status == HealthStatus.OK and error_count == 0:
                    report = HealthReport()
                    await self.reporter.update_report(name=health_system, report=report)
                    return
                elif status == HealthStatus.NA:
                    log.error("Invalid health status received")
                    return

                for index in range (0, error_count):
                    gpu_id = group_health.incidents[index].entityInfo.entityId
                    system = Wrap.convert_system_enum_to_system_name(group_health.incidents[index].system)
                    subsystems.add(system)
                    details.append(group_health.incidents[index].error.msg)

                details.extend(field_errors)
                subsystems.update(category)
                error_count += len(field_errors)
                description = f"{health_system} report {status.value} count={error_count} subsystem={', '.join(subsystems)}"
                custom_fields['categories'] = subsystems
                custom_fields['error_count'] = error_count
                report = HealthReport(status=status,
                                      description=description,
                                      details='\n'.join(details),
                                      custom_fields=custom_fields)
                await self.reporter.update_report(name=health_system, report=report)
                return

            except dcgm_structs.DCGMError as e:
                code = e.value
                if code == dcgm_structs.DCGM_ST_CONNECTION_NOT_VALID:
                    # We lost connection to DCGM, try to re-initialize.
                    log.error("DCGM Connection not valid")
                    raise Wrap.DcgmInvalidHandle from None
                else:
                    log.error("dcgmHealthCheck returned error %d: %s" % (code, e))
        except Wrap.DcgmInvalidHandle:
            log.critical("Invalid DCGM Handle, Attempting to reconnect.")
            try:
                self.setup()
            except Wrap.DcgmConnectionFail as e:
                log.critical("Unable to connect to DCGM: {e}")
                log.critical("To re-instantiate checks, restart the nvidia-dcgm service.")
            else:
                log.info("Re-initialized our connection to DCGM.")
        except Exception as e:
            log.exception(f"{e}")


    @status
    def show_status(self):
        return self.reporter.summarize()

    def __del__(self):
        ## Delete the group
        if hasattr(self, 'dcgmGroup') and self.dcgmGroup:
            self.dcgmGroup.Delete()

        if hasattr(self, 'dcgmHandle') and self.dcgmHandle:
            ## disconnect from the hostengine by deleting the DcgmHandle object
            del(self.dcgmHandle)

@Scheduler.pool
def run_active_healthchecksv2():

    """
    Run active healthchecks, before or after a job.
    These checks do require exclusive access to the GPU's and cannot
    be run alongside jobs.
    """
    DIAG_LEVEL=dcgm_structs.DCGM_DIAG_LVL_MED
    isHealthy = True
    report = HealthReport()
    custom_fields = {}

    failures = list()
    info_msgs = list()
    try:
        dcgmGroup, dcgmHandle = Wrap.connect(grp_name="epilog")
        response = dcgmGroup.action.RunDiagnostic(DIAG_LEVEL)
    except Wrap.DcgmConnectionFail as e:
        failures.append("Could not connect to DCGM, is nvidia-dcgm service running?")
        report = HealthReport(status=HealthStatus.WARNING, description="Test not performed",
                              details="Active diagnostics not performed.\nIs nvidia-dcgm service running?")
        return report
    except dcgmExceptionClass(dcgm_structs.DCGM_ST_NOT_CONFIGURED):
        failures.append("One of the GPUs on your system is not supported by NVVS")
    except dcgmExceptionClass(dcgm_structs.DCGM_ST_GROUP_INCOMPATIBLE):
        failures.append("GPUs in the group are not compatible with each other for running diagnostics")
    except dcgmExceptionClass(dcgm_structs.DCGM_ST_NVVS_ERROR) as e:
        if not Wrap.should_ignore_error(e):
            raise(e)
        else:
            failures.append(str(e))
    test_types = set()
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
                failures.append(f"Error: {error_msg}")
    if response.numInfo > 0:
        for i in range(response.numInfo):
            info = response.info[i]
            testName = response.tests[info.testId].name
            info_msgs.append(f"Test: {testName}, Info: {info.msg}")

    if not isHealthy:
        custom_fields['failures'] = test_types
        custom_fields['error_count'] = len(failures)
        custom_fields['info'] = info_msgs
        report.status = HealthStatus.ERROR
        report.details = "\n".join(failures)
        report.description = f"DCGM Epilog failures in {', '.join(test_types)}"
        report.message = "GPU Epilog Errors"
        report.custom_fields = custom_fields

    Wrap.disconnect(dcgmHandle, dcgmGroup)
    return report


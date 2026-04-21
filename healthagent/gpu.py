import asyncio
import sys
import logging
import os
import shutil
from datetime import datetime, timezone
from healthagent import epilog,status,healthcheck,prolog
from healthagent.scheduler import Scheduler
from healthagent.healthmodule import HealthModule
from healthagent.reporter import Reporter, HealthReport,HealthStatus
from healthagent.util import evaluate
from healthagent.bindings import *

log = logging.getLogger('healthagent')

class GpuHealthChecksException(Exception):
    pass

class GpuNotFoundException(Exception):
    pass

class GpuHealthChecks(HealthModule):

    def __init__(self, reporter: Reporter):

        super().__init__(reporter)

        # TODO: Move this to config file
        self.test_mode = os.getenv('DCGM_TEST_MODE', 'false').lower() == 'true'

        os_gpu_count = Wrap.count_os_gpu_devices()
        if os_gpu_count == 0:
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
        self.xid_history = {}
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
        policy = Wrap.set_policy()
        self.dcgmGroup.policy.Set(policy)
        self.c_callback = create_c_callback(self.handle_policy_violation, asyncio.get_running_loop())
        self.dcgmGroup.policy.Register(policy.condition, self.c_callback, None)
        log.debug("Applied GPU violation Policies")

    def setup_background_watches(self):
        """
        Setup field watches and health watches.
        """
        self.watch_fields = Wrap.get_fields()
        self.field_group = DcgmFieldGroup.DcgmFieldGroup(self.dcgmHandle, name="ccfield_group", fieldIds=self.watch_fields)
        # UpdateFreq is in microseconds. So we are updating every 1 second.
        # maxKeepSamples=300 bounds memory. We read the latest value each cycle.
        self.dcgmGroup.samples.WatchFields(fieldGroup=self.field_group, updateFreq=1000000, maxKeepAge=0, maxKeepSamples=300)
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

    @healthcheck("GpuCountCheck", description="Check OS vs PCI GPU count")
    @Scheduler.periodic(120)
    async def gpu_count_check(self):

        report = HealthReport()
        pci_gpu_count = Wrap.count_pci_gpu_devices()
        os_gpu_count = Wrap.count_os_gpu_devices()
        if os_gpu_count != pci_gpu_count:
            report.status = HealthStatus.ERROR
            report.details = f"OS shows {os_gpu_count} GPU devices, PCI Bus shows {pci_gpu_count} GPU devices"
            report.description = "GPU Count Mismatch"
        await self.reporter.update_report(name=self.gpu_count_check.report_name, report=report)

    async def create(self):
        await self.reporter.clear_all_errors()
        log.debug("Adding periodic background healthchecks")
        Scheduler.add_task(self.gpu_count_check)
        Scheduler.add_task(self.run_background_healthchecks)

    async def handle_policy_violation(self, callbackresp):

        condition = callbackresp.condition
        try:
            condition_str = Wrap.dcgm_condition_to_string(condition=condition)
            if condition == dcgm_structs.DCGM_POLICY_COND_XID:
                gpuid = callbackresp.gpuId
                xid_received = callbackresp.val.xid.errnum
                unix_ts = callbackresp.val.xid.timestamp
                timestamp = datetime.fromtimestamp(unix_ts / 1_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S UTC")
                log.critical("XID detected: %d  on gpu: %d" % (xid_received, gpuid))
                gpu_key = f'GPU_{gpuid}'
                if gpu_key not in self.xid_history:
                    self.xid_history[gpu_key] = {}
                if xid_received not in self.xid_history[gpu_key] or timestamp < self.xid_history[gpu_key][xid_received]["timestamp"]:
                    self.xid_history[gpu_key][xid_received] = {"xid": xid_received, "timestamp": timestamp}
        except ValueError as e:
            log.exception(e)
            return

    # TODO: Adding all config file attributes as class objects for now.
    XID_WARNING = [43, 63, 13, 31, 66, 94, 154]
    XID_IGNORE = []
    XID_ERROR = []

    @staticmethod
    def _gpu_entry():
        return {"errors": [], "warnings": [], "xid": []}

    def track_fieldsv2(self):
        """
        Generic field watch evaluation driven by Wrap.default_field_watches.
        Reads DCGM field values and evaluates each watch against its thresholds.
        XIDs are handled separately — not part of field watches.
        """
        custom_fields = {'error_count': 0, 'category': set()}
        try:
            response = self.dcgmGroup.samples.GetLatest_v2(self.field_group)
            gpu_values = response.values.get(dcgm_fields.DCGM_FE_GPU, {})
            for gpu in gpu_values:
                gpu_id = f'GPU_{gpu}'
                custom_fields.setdefault(gpu_id, self._gpu_entry())

                for watch in Wrap.default_field_watches:
                    samples = gpu_values[gpu].get(watch["field_id"])
                    if not samples or samples[0].isBlank:
                        continue

                    newest, oldest = samples[0], samples[-1]
                    severity = None
                    threshold_used = None
                    evaluated = None
                    for level in ("error", "warning"):
                        thresh = watch.get(level)
                        if thresh is None:
                            continue
                        triggered, evaluated = evaluate(
                            watch["eval"], newest.value, thresh,
                            prev_value=oldest.value,
                            prev_time=oldest.ts,
                            current_time=newest.ts,
                            window=watch.get("window", 60),
                        )
                        if triggered:
                            severity = level
                            threshold_used = thresh
                            break

                    if severity is None:
                        continue

                    msg = watch["message"].format(gpu=gpu, value=evaluated, threshold=threshold_used)
                    custom_fields[gpu_id]["errors" if severity == "error" else "warnings"].append(msg)
                    if severity == "error":
                        custom_fields['error_count'] += 1
                    custom_fields['category'].add(watch["category"])

                # XID - read latest, deduplicate by xid number per GPU, keep earliest timestamp
                xid_samples = gpu_values[gpu].get(Wrap.fields.XID_ERRORS, [])
                if gpu_id not in self.xid_history:
                    self.xid_history[gpu_id] = {}
                for sample in xid_samples:
                    if not sample.isBlank:
                        xid_num = sample.value
                        ts_utc = datetime.fromtimestamp(sample.ts / 1_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S UTC")
                        if xid_num not in self.xid_history[gpu_id] or ts_utc < self.xid_history[gpu_id][xid_num]["timestamp"]:
                            self.xid_history[gpu_id][xid_num] = {"xid": xid_num, "timestamp": ts_utc}

            # Populate report with full XID history
            for gpu_id, xids in self.xid_history.items():
                custom_fields.setdefault(gpu_id, self._gpu_entry())
                for entry in xids.values():
                    xid_num = entry["xid"]
                    if xid_num in self.XID_IGNORE:
                        severity = "ignore"
                    elif xid_num in self.XID_WARNING:
                        severity = "warning"
                    else:
                        severity = "error"
                        custom_fields['error_count'] += 1
                    custom_fields[gpu_id]['xid'].append({**entry, "severity": severity})
                if xids:
                    custom_fields['category'].add("XID")

            return custom_fields
        except Exception as e:
            log.exception(e)
            return custom_fields
    def track_fields(self):

        """
        Read field values from DCGM.
        All fields (polled and event-driven) are read via GetLatest_v2.
        XIDs are deduplicated and accumulated in self.xid_history for the node's lifetime.
        """

        custom_fields = {}
        custom_fields['error_count'] = 0
        custom_fields['category'] = set()
        try:
            response = self.dcgmGroup.samples.GetLatest_v2(self.field_group)
            gpu_values = response.values.get(dcgm_fields.DCGM_FE_GPU, {})
            for gpu in gpu_values:
                gpu_id = f'GPU_{gpu}'
                custom_fields.setdefault(gpu_id, self._gpu_entry())
                # Handle Thermal
                curr_temp = gpu_values[gpu][Wrap.fields.GPUTEMP][0].value
                slowdown_temp = gpu_values[gpu][Wrap.fields.GPUTEMP_SLOWDOWN][0].value
                triggered, _ = evaluate("ge", curr_temp, 0.95 * slowdown_temp)
                if triggered:
                    msg = f"GPU temperature reached very close to the slowdown limit gpu: {gpu} Curr temp: {curr_temp}, slowdown limit: {slowdown_temp}"
                    custom_fields[gpu_id]["errors"].append(msg)
                    custom_fields['error_count'] += 1
                    custom_fields['category'].add("Thermal")
                # Clocks
                clock_reason = gpu_values[gpu][Wrap.fields.CLOCK_REASON][0].value
                THROTTLE_MASK = (dcgm_fields.DCGM_CLOCKS_EVENT_REASON_HW_SLOWDOWN
                                 | dcgm_fields.DCGM_CLOCKS_EVENT_REASON_SW_THERMAL
                                 | dcgm_fields.DCGM_CLOCKS_EVENT_REASON_HW_THERMAL
                                 | dcgm_fields.DCGM_CLOCKS_EVENT_REASON_HW_POWER_BRAKE)
                triggered, _ = evaluate("bitmask", clock_reason, THROTTLE_MASK)
                if triggered:
                    throttle_reasons = Wrap.get_throttle_reasons(clock_reason)
                    custom_fields[gpu_id]["errors"].append(throttle_reasons)
                    custom_fields['error_count'] += 1
                    custom_fields['category'].add("Clocks")
                # Fabric errors
                fabric_status = gpu_values[gpu][Wrap.fields.FABRIC_STATUS][0].value
                triggered, _ = evaluate("eq", fabric_status, dcgm_structs.DcgmFMStatusFailure)
                if triggered:
                    fabric_error = gpu_values[gpu][Wrap.fields.FABRIC_ERROR][0].value
                    msg = f"Fabric Manager finished training but failed, error code: {fabric_error}"
                    custom_fields[gpu_id]["errors"].append(msg)
                    custom_fields['error_count'] += 1
                    custom_fields['category'].add("FabricManager")
                else:
                    triggered, _ = evaluate("eq", fabric_status, dcgm_structs.DcgmFMStatusInProgress)
                    if triggered:
                        fabric_error = gpu_values[gpu][Wrap.fields.FABRIC_ERROR][0].value
                        msg = f"Fabric Manager not running, training in progress, error code: {fabric_error}"
                        custom_fields[gpu_id]["errors"].append(msg)
                        custom_fields['error_count'] += 1
                        custom_fields['category'].add("FabricManager")
                # Persistence mode
                persistence_mode = gpu_values[gpu][Wrap.fields.PERSISTENCE_MODE][0].value
                triggered, _ = evaluate("ne", persistence_mode, 1)
                if triggered:
                    msg = f"Persistence Mode not set for GPU: {gpu}, Restart nvidia-persistenced or Reboot the system."
                    custom_fields[gpu_id]["errors"].append(msg)
                    custom_fields['error_count'] += 1
                    custom_fields['category'].add("System")
                # Row Remap failures
                row_remap_failure = gpu_values[gpu][Wrap.fields.ROW_REMAP_FAIL][0].value
                triggered, _ = evaluate("ne", row_remap_failure, 0)
                if triggered:
                    msg = f"GPU {gpu}: {dcgm_errors.DCGM_FR_ROW_REMAP_FAILURE_MSG} {dcgm_errors.DCGM_FR_ROW_REMAP_FAILURE_NEXT}"
                    custom_fields[gpu_id]["errors"].append(msg)
                    custom_fields['error_count'] += 1
                    custom_fields['category'].add("Memory")
                # DBE error
                dbe_error = gpu_values[gpu][Wrap.fields.DBE_ERRORS][0].value
                triggered, _ = evaluate("gt", dbe_error, 0)
                if triggered:
                    msg = f"{dcgm_errors.DCGM_FR_VOLATILE_DBE_DETECTED_MSG % (dbe_error, gpu)} {dcgm_errors.DCGM_FR_VOLATILE_DBE_DETECTED_NEXT}"
                    custom_fields[gpu_id]["errors"].append(msg)
                    custom_fields['error_count'] += 1
                    custom_fields['category'].add("Memory")
                # XID - read latest, deduplicate by xid number per GPU, keep earliest timestamp
                xid_samples = gpu_values[gpu].get(Wrap.fields.XID_ERRORS, [])
                if gpu_id not in self.xid_history:
                    self.xid_history[gpu_id] = {}
                for sample in xid_samples:
                    if not sample.isBlank:
                        xid_num = sample.value
                        ts_utc = datetime.fromtimestamp(sample.ts / 1_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S UTC")
                        if xid_num not in self.xid_history[gpu_id] or ts_utc < self.xid_history[gpu_id][xid_num]["timestamp"]:
                            self.xid_history[gpu_id][xid_num] = {"xid": xid_num, "timestamp": ts_utc}

            # Populate report with full XID history
            for gpu_id, xids in self.xid_history.items():
                custom_fields.setdefault(gpu_id, self._gpu_entry())
                for entry in xids.values():
                    xid_num = entry["xid"]
                    if xid_num in self.XID_IGNORE:
                        severity = "ignore"
                    elif xid_num in self.XID_WARNING:
                        severity = "warning"
                    else:
                        severity = "error"
                        custom_fields['error_count'] += 1
                    custom_fields[gpu_id]['xid'].append({**entry, "severity": severity})
                if xids:
                    custom_fields['category'].add("XID")

            return custom_fields
        except Exception as e:
            log.exception(e)
            return custom_fields

    @healthcheck("GpuMemoryCheck", args=["gpu_id"], description="Run GPU memory allocation test. Args: gpu_id=0,1")
    @epilog
    @prolog
    async def memory_allocation_test(self, gpu_id: list = None):
        health_system = self.memory_allocation_test.report_name
        report = HealthReport()
        script = os.path.join(os.path.dirname(__file__), "tools", "cuda_malloc.py")
        cmd = [sys.executable, script]
        if gpu_id:
            cmd.extend(["--gpus", ",".join(str(g) for g in gpu_id)])
        try:
            proc = await Scheduler.add_task(Scheduler.subprocess(*cmd))
            stdout, stderr = await proc.communicate()
            output = stdout.decode().strip()
            err_output = stderr.decode().strip()
            if proc.returncode == 0:
                report.status = HealthStatus.OK
                report.description = "Memory allocation test passed"
                report.details = output
            elif proc.returncode == 2:
                report.status = HealthStatus.WARNING
                report.description = "Test not run"
                report.details = err_output or output
            else:
                report.status = HealthStatus.ERROR
                report.description = "Memory allocation test failed"
                report.details = f"{output}\n{err_output}".strip()
        except Exception as e:
            log.exception(e)
            report.status = HealthStatus.WARNING
            report.description = "Test not run."
            report.details = str(e)
        await self.reporter.update_report(name=health_system, report=report)
        response = {}
        response[health_system] = report.view()
        return response

    DIAG_DEFAULTS = {
        "prolog": {"tests": "short", "params": ""},
        "epilog": {"tests": "medium", "params": ""},
    }
    @healthcheck("GpuDiagnosticCheck", description="Run DCGM diagnostics checks. Eg. Args: gpu_id=0,1 tests=memory", args=['gpu_id','tests', 'params'])
    @epilog
    @prolog
    async def run_diag(self, gpu_id: list = None, tests: str = '', params: str = '', _phase: str = None):
        phase_defaults = self.DIAG_DEFAULTS.get(_phase, {})
        tests = tests or phase_defaults.get("tests", "")
        params = params or phase_defaults.get("params", "")
        health_system = self.run_diag.report_name
        report = await Scheduler.add_task(run_active_healthchecksv2, gpu_id=gpu_id, tests=tests, params=params)
        await self.reporter.update_report(name=health_system, report=report)
        response = {}
        response[health_system] = report.view()
        return response


    @healthcheck("GpuHealthCheck", description="Periodic GPU health monitoring")
    @Scheduler.periodic(60)
    async def run_background_healthchecks(self):
        """
        Invoke Health checks periodically.
        These are safe to run constantly alongside jobs.
        """

        health_system = self.run_background_healthchecks.report_name
        custom_fields = {}
        try:
            try:
                details = list()
                subsystems = set()
                report = HealthReport()
                if not self.dcgmGroup:
                    raise Wrap.DcgmInvalidHandle
                group_health = self.dcgmGroup.health.Check()
                incident_count = group_health.incidentCount

                custom_fields = self.track_fields()
                if custom_fields.get('error_count', 0) > 0:
                    report.escalate(HealthStatus.ERROR)

                for index in range (0, incident_count):
                    entity_id = group_health.incidents[index].entityInfo.entityId
                    if entity_id == dcgm_fields.DCGM_FE_GPU:
                        entity = f"GPU_{entity_id}"
                        custom_fields.setdefault(entity, self._gpu_entry())
                    else:
                        entity = "overall"
                        custom_fields.setdefault(entity, {"errors": [], "warnings": []})
                    system = Wrap.convert_system_enum_to_system_name(group_health.incidents[index].system)
                    error_code = group_health.incidents[index].error.code
                    if error_code in Wrap.HEALTH_WARNINGS:
                        custom_fields[entity]['warnings'].append(group_health.incidents[index].error.msg)
                        report.escalate(HealthStatus.WARNING)
                        subsystems.add(system)
                    elif error_code in Wrap.HEALTH_ERRORS:
                        report.escalate(HealthStatus.ERROR)
                        custom_fields[entity]['errors'].append(group_health.incidents[index].error.msg)
                        subsystems.add(system)
                        custom_fields['error_count'] += 1
                    else:
                        #ignore
                        continue

                #details.extend(field_errors)
                custom_fields['category'].update(subsystems)
                report.custom_fields = {
                    k: v for k, v in custom_fields.items()
                    if not isinstance(v, dict) or any(v.values())
                }

                if custom_fields['error_count'] == 0:
                    await self.reporter.update_report(name=health_system, report=report)
                    return

                description = f"{health_system} report {custom_fields['error_count']} errors of type {', '.join(custom_fields['category'])}"
                report.description = description
                report.details = '\n'.join(details)
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
                log.critical(f"Unable to connect to DCGM: {e}")
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

def _diag_entry():
    return {"errors": [], "warnings": [], "suppressed": []}

@Scheduler.pool
def run_active_healthchecksv2(gpu_id: list = None, tests: str = '', params: str = ''):

    """
    Run active healthchecks, before or after a job.
    These checks do require exclusive access to the GPU's and cannot
    be run alongside jobs.
    """

    isHealthy = True
    report = HealthReport()
    custom_fields = {}
    custom_fields['error_count'] = 0
    response = None
    try:
        #TODO: Find a better way to get this directory later.
        log_dir = "/opt/healthagent"
        # Primary nvvs log
        diag_log = os.path.join(log_dir, "nvvs_diag.log")

        dcgmGroup, dcgmHandle = Wrap.connect(grp_name="epilog")
        if gpu_id is None:
            gpu_id = dcgmGroup.GetGpuIds()

        #params = "memory.minimum_allocation_percentage=90;memory.is_allowed=true"
        #tests = "software,memory,pcie"
        dd = DcgmDiag.DcgmDiag(gpuIds=gpu_id, testNamesStr=tests, paramsStr=params)

        if os.path.exists(log_dir):
            # Rotate if file is too large ( > 50MB)
            try:
                if os.path.exists(diag_log):
                    size_mb = os.path.getsize(diag_log) / (1024 * 1024)
                    if size_mb > 50:
                        # Rotate existing logs
                        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                        rotated_name = os.path.join(log_dir, f"nvvs_diag.log.{timestamp}")
                        shutil.move(diag_log, rotated_name)
            except Exception as e:
                # if we cant rotate logs, run diagnostics without it.
                pass
            else:
                dd.SetDebugLogFile(diag_log)
                #FATAL,ERROR,WARN,INFO,DEBUG
                dd.SetDebugLevel(5)
        # Helps exit the test early if there are failures.
        # TODO: Make this configurable later. For most part we want epilog to run as quickly as possible but sometimes
        # we might need exhaustive coverage.
        dd.SetFailEarly()
        response = dd.Execute(handle=dcgmHandle.handle)

    except Wrap.DcgmConnectionFail as e:
        report = HealthReport(status=HealthStatus.WARNING, description="Active Tests not performed",
                              details=f"Active diagnostics not performed.\n {e}")
        return report
    except dcgm_structs.DCGMError as e:
        isHealthy = False
        custom_fields['error_count'] += 1
        custom_fields.setdefault('overall', _diag_entry())
        custom_fields['overall']['errors'].append(str(e))

    if response and response.numErrors > 0:
        isHealthy = False
        for errIdx in range(response.numErrors):
            curErr = response.errors[errIdx]
            error_msg = curErr.msg
            error_code = curErr.code
            if curErr.entity.entityGroupId == dcgm_fields.DCGM_FE_GPU:
                entity_id = f"GPU_{curErr.entity.entityId}"
            else:
                entity_id = "overall"
            custom_fields.setdefault(entity_id, _diag_entry())
            if error_code in Wrap.DIAG_ERRORS:
                custom_fields[entity_id]['errors'].append(error_msg)
                custom_fields['error_count'] += 1
                report.escalate(HealthStatus.ERROR)
            elif error_code in Wrap.DIAG_WARNINGS:
                note = Wrap.DIAG_WARNINGS[error_code]
                custom_fields[entity_id]['warnings'].append({"msg": error_msg, "note": note})
                report.escalate(HealthStatus.WARNING)
            elif error_code in Wrap.DIAG_IGNORE:
                continue
            else:
                custom_fields[entity_id]['suppressed'].append({"msg": error_msg, "note": Wrap.DIAG_SUPPRESSED_NOTE})
    if not isHealthy:
        #report.details = "\n".join(failures)
        report.description = "Active Diagnostic test failures"
        report.message = "GPU Epilog Errors"
        report.custom_fields = custom_fields

    Wrap.disconnect(dcgmHandle, dcgmGroup)
    return report


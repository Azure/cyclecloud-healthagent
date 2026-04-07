from healthagent import epilog, status, prolog, healthcheck
from healthagent.reporter import Reporter
from healthagent.healthmodule import HealthModule
from healthagent.scheduler import Scheduler

# Validate _get_handlers with MRO-based discovery.

# Simulated module with multiple epilog handlers
class FakeGpuModule(HealthModule):

    @healthcheck("ActiveGPUHealthChecks")
    @epilog
    async def run_epilog(self):
        return {"ActiveGPUHealthChecks": {"status": "OK"}}

    @healthcheck("NvlinkCheck")
    @epilog
    async def run_nvlink_epilog(self):
        return {"NvlinkCheck": {"status": "OK"}}

    @status
    def show_status(self):
        return {"gpu_custom": "healthy"}


# Module with no epilog, only inherits default status from base
class FakeSystemdModule(HealthModule):
    pass


# Module with prolog + epilog
class FakeProcessModule(HealthModule):

    @healthcheck("ZombieCheck")
    @epilog
    async def check_zombies(self):
        return {"ZombieCheck": {"status": "OK"}}

    @healthcheck("Readiness")
    @prolog
    async def check_readiness(self):
        return {"Readiness": {"status": "OK"}}


# Module with multi-decorated methods
class FakeMultiDecoratorModule(HealthModule):

    @healthcheck("ValidateBoth")
    @prolog
    @epilog
    async def validate_both(self):
        return {"ValidateBoth": {"status": "OK"}}

    @healthcheck("CheckAndReport")
    @prolog
    @status
    def check_and_report(self):
        return {"CheckAndReport": {"status": "OK"}}

    @healthcheck("DoEverything")
    @prolog
    @epilog
    @status
    def do_everything(self):
        return {"DoEverything": {"status": "OK"}}


def test_multiple_epilog_handlers():
    reporter = Reporter()
    gpu = FakeGpuModule(reporter=reporter)

    handlers = gpu._get_handlers("epilog")
    assert len(handlers) == 2
    assert set(h.__name__ for h in handlers) == {"run_epilog", "run_nvlink_epilog"}


def test_status_includes_subclass_and_base():
    reporter = Reporter()
    gpu = FakeGpuModule(reporter=reporter)

    handlers = gpu._get_handlers("status")
    # show_status from FakeGpuModule + status from HealthModule base
    assert len(handlers) == 2
    assert set(h.__name__ for h in handlers) == {"show_status", "status"}


def test_no_prolog_when_none_defined():
    reporter = Reporter()
    gpu = FakeGpuModule(reporter=reporter)

    handlers = gpu._get_handlers("prolog")
    assert len(handlers) == 0


async def test_execute_merges_results():
    reporter = Reporter()
    gpu = FakeGpuModule(reporter=reporter)

    result = await gpu.execute("epilog")
    assert "ActiveGPUHealthChecks" in result
    assert "NvlinkCheck" in result


def test_inherited_status_only():
    reporter = Reporter()
    systemd = FakeSystemdModule(reporter=reporter)

    handlers = systemd._get_handlers("status")
    assert len(handlers) == 1
    assert handlers[0].__name__ == "status"

    assert len(systemd._get_handlers("epilog")) == 0
    assert len(systemd._get_handlers("prolog")) == 0


def test_prolog_and_epilog_mix():
    reporter = Reporter()
    proc = FakeProcessModule(reporter=reporter)

    epilog_handlers = proc._get_handlers("epilog")
    assert len(epilog_handlers) == 1
    assert epilog_handlers[0].__name__ == "check_zombies"

    prolog_handlers = proc._get_handlers("prolog")
    assert len(prolog_handlers) == 1
    assert prolog_handlers[0].__name__ == "check_readiness"


def test_no_class_or_init_leakage():
    reporter = Reporter()
    modules = [
        FakeGpuModule(reporter=reporter),
        FakeSystemdModule(reporter=reporter),
        FakeProcessModule(reporter=reporter),
    ]
    for mod in modules:
        for flag in ("epilog", "prolog", "status"):
            for h in mod._get_handlers(flag):
                assert h.__name__ not in ("__init__", "__class__")

def test_multi_decorator_prolog_and_epilog():
    reporter = Reporter()
    mod = FakeMultiDecoratorModule(reporter=reporter)

    epilog_handlers = mod._get_handlers("epilog")
    prolog_handlers = mod._get_handlers("prolog")

    assert "validate_both" in {h.__name__ for h in epilog_handlers}
    assert "validate_both" in {h.__name__ for h in prolog_handlers}


def test_multi_decorator_prolog_and_status():
    reporter = Reporter()
    mod = FakeMultiDecoratorModule(reporter=reporter)

    prolog_handlers = mod._get_handlers("prolog")
    status_handlers = mod._get_handlers("status")

    assert "check_and_report" in {h.__name__ for h in prolog_handlers}
    assert "check_and_report" in {h.__name__ for h in status_handlers}
    # should NOT appear in epilog
    epilog_names = {h.__name__ for h in mod._get_handlers("epilog")}
    assert "check_and_report" not in epilog_names


def test_multi_decorator_all_three():
    reporter = Reporter()
    mod = FakeMultiDecoratorModule(reporter=reporter)

    epilog_handlers = mod._get_handlers("epilog")
    prolog_handlers = mod._get_handlers("prolog")
    status_handlers = mod._get_handlers("status")

    assert "do_everything" in {h.__name__ for h in epilog_handlers}
    assert "do_everything" in {h.__name__ for h in prolog_handlers}
    assert "do_everything" in {h.__name__ for h in status_handlers}


async def test_multi_decorator_execute():
    reporter = Reporter()
    mod = FakeMultiDecoratorModule(reporter=reporter)

    epilog_result = await mod.execute("epilog")
    assert "ValidateBoth" in epilog_result
    assert "DoEverything" in epilog_result

    prolog_result = await mod.execute("prolog")
    assert "ValidateBoth" in prolog_result
    assert "CheckAndReport" in prolog_result
    assert "DoEverything" in prolog_result


# Module that overrides the base `status` method — should NOT produce duplicates
class FakeOverrideStatusModule(HealthModule):

    @status
    def status(self) -> dict:
        return {"custom_status": "overridden"}


def test_override_base_method_no_duplicates():
    """When a subclass overrides a decorated base method with the same name,
    _get_handlers should only return it once (the most-derived version)."""
    reporter = Reporter()
    mod = FakeOverrideStatusModule(reporter=reporter)

    handlers = mod._get_handlers("status")
    names = [h.__name__ for h in handlers]
    assert names.count("status") == 1
    assert len(handlers) == 1
    # The returned handler should be the overridden version
    assert handlers[0]() == {"custom_status": "overridden"}


# --- Tests for @healthcheck, execute with checks filtering, list_checks, and kwargs ---

class FakeEpilogWithArgs(HealthModule):

    @healthcheck("MemTest", args=["gpu_id"])
    @epilog
    async def memory_test(self, gpu_id: list = None):
        return {"MemTest": {"status": "OK", "gpu_id": gpu_id}}

    @healthcheck("DiagTest")
    @epilog
    async def diag_test(self):
        return {"DiagTest": {"status": "OK"}}


def test_healthcheck_decorator_sets_report_name():
    reporter = Reporter()
    mod = FakeGpuModule(reporter=reporter)
    assert mod.run_epilog.report_name == "ActiveGPUHealthChecks"
    assert mod.run_nvlink_epilog.report_name == "NvlinkCheck"


def test_list_checks():
    reporter = Reporter()
    mod = FakeGpuModule(reporter=reporter)
    checks = mod.list_checks("epilog")
    assert "ActiveGPUHealthChecks" in checks
    assert "NvlinkCheck" in checks
    assert len(checks) == 2


def test_list_checks_empty_for_undecorated_phase():
    reporter = Reporter()
    mod = FakeGpuModule(reporter=reporter)
    checks = mod.list_checks("prolog")
    assert len(checks) == 0


async def test_execute_with_checks_filter():
    """When checks dict is provided, only matching handlers should run."""
    reporter = Reporter()
    mod = FakeGpuModule(reporter=reporter)

    result = await mod.execute("epilog", checks={"ActiveGPUHealthChecks": {}})
    assert "ActiveGPUHealthChecks" in result
    assert "NvlinkCheck" not in result


async def test_execute_with_checks_filter_case_insensitive():
    """Check name matching should be case-insensitive."""
    reporter = Reporter()
    mod = FakeGpuModule(reporter=reporter)

    result = await mod.execute("epilog", checks={"activegpuhealthchecks": {}})
    assert "ActiveGPUHealthChecks" in result
    assert "NvlinkCheck" not in result

    result2 = await mod.execute("epilog", checks={"NVLINKCHECK": {}})
    assert "NvlinkCheck" in result2
    assert "ActiveGPUHealthChecks" not in result2


async def test_execute_with_checks_none_runs_all():
    """When checks is None, all handlers should run (backward compat)."""
    reporter = Reporter()
    mod = FakeGpuModule(reporter=reporter)

    result = await mod.execute("epilog", checks=None)
    assert "ActiveGPUHealthChecks" in result
    assert "NvlinkCheck" in result


async def test_execute_with_kwargs():
    """Check kwargs are passed through to the handler."""
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)

    result = await mod.execute("epilog", checks={"MemTest": {"gpu_id": [0, 1]}})
    assert result["MemTest"]["gpu_id"] == [0, 1]
    assert "DiagTest" not in result


async def test_execute_with_kwargs_default():
    """Handler called with no kwargs uses default args."""
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)

    result = await mod.execute("epilog", checks={"MemTest": {}})
    assert result["MemTest"]["gpu_id"] is None


async def test_execute_bad_kwargs_logs_error(capfd):
    """Invalid kwargs should be caught and not crash the run."""
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)

    # DiagTest declares no args, so kwargs are stripped and it runs normally
    result = await mod.execute("epilog", checks={"DiagTest": {"nonexistent": "value"}})
    assert result["DiagTest"]["status"] == "OK"


def test_list_checks_includes_signature():
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)
    checks = mod.list_checks("epilog")
    assert "MemTest" in checks
    assert "gpu_id" in checks["MemTest"]
    assert "DiagTest" in checks
    assert checks["DiagTest"] == ""


# --- Tests for background vs on-demand distinction ---

class FakeBackgroundOnlyModule(HealthModule):
    """Module with only background checks — no epilog/prolog."""

    @healthcheck("BackgroundMetric")
    def some_periodic_check(self):
        return {"BackgroundMetric": {"status": "OK"}}


async def test_background_check_not_targetable_via_epilog():
    """A check that only has @healthcheck (no @epilog) should not appear in epilog list_checks."""
    reporter = Reporter()
    mod = FakeBackgroundOnlyModule(reporter=reporter)
    checks = mod.list_checks("epilog")
    assert "BackgroundMetric" not in checks


async def test_execute_epilog_skips_background_only():
    """Targeting a background-only check via epilog should produce empty result."""
    reporter = Reporter()
    mod = FakeBackgroundOnlyModule(reporter=reporter)
    result = await mod.execute("epilog", checks={"BackgroundMetric": {}})
    assert "BackgroundMetric" not in result


# --- Tests for list_all_checks ---

class FakeFullModule(HealthModule):
    """Module with checks in every category for list_all_checks testing."""

    @healthcheck("EpilogOnly")
    @epilog
    async def epilog_check(self):
        return {"EpilogOnly": {"status": "OK"}}

    @healthcheck("PrologOnly")
    @prolog
    async def prolog_check(self):
        return {"PrologOnly": {"status": "OK"}}

    @healthcheck("BackgroundOnly")
    @Scheduler.periodic(120)
    async def background_check(self):
        return {"BackgroundOnly": {"status": "OK"}}

    @healthcheck("EpilogAndBackground")
    @epilog
    @Scheduler.periodic(60)
    async def dual_check(self):
        return {"EpilogAndBackground": {"status": "OK"}}

    @healthcheck("AsyncCallback")
    def callback_check(self):
        return {"AsyncCallback": {"status": "OK"}}

    @Scheduler.periodic(300)
    async def admin_task(self):
        """No @healthcheck — should NOT appear in list_all_checks."""
        pass


def test_list_all_checks_discovers_all_healthchecks():
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    checks = mod.list_checks()
    assert "EpilogOnly" in checks
    assert "PrologOnly" in checks
    assert "BackgroundOnly" in checks
    assert "EpilogAndBackground" in checks
    assert "AsyncCallback" in checks
    assert len(checks) == 5


def test_list_all_checks_excludes_non_healthcheck():
    """Admin tasks without @healthcheck should not appear."""
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    checks = mod.list_checks()
    # admin_task has no report_name, so it must not be present
    for info in checks.values():
        assert "admin_task" not in str(info)


def test_list_all_checks_categories():
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    checks = mod.list_checks()
    assert checks["EpilogOnly"]["category"] == ["epilog"]
    assert checks["PrologOnly"]["category"] == ["prolog"]
    assert checks["BackgroundOnly"]["category"] == ["background"]
    assert checks["AsyncCallback"]["category"] == ["background"]
    # Dual check should have both categories
    assert "epilog" in checks["EpilogAndBackground"]["category"]
    assert "background" in checks["EpilogAndBackground"]["category"]


def test_list_all_checks_interval():
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    checks = mod.list_checks()
    assert checks["BackgroundOnly"]["interval"] == 120
    assert checks["EpilogAndBackground"]["interval"] == 60
    assert checks["EpilogOnly"]["interval"] == -1
    assert checks["AsyncCallback"]["interval"] == "async"


def test_list_all_checks_signature():
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    checks = mod.list_checks()
    for name, info in checks.items():
        assert "args" in info
        assert "description" in info


def test_list_checks_phase_filters_from_registry():
    """list_checks with a phase should only return checks in that category."""
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    epilog_checks = mod.list_checks("epilog")
    assert "EpilogOnly" in epilog_checks
    assert "EpilogAndBackground" in epilog_checks
    assert "BackgroundOnly" not in epilog_checks
    assert "AsyncCallback" not in epilog_checks
    # Phase-filtered results return {name: args_str}
    assert isinstance(epilog_checks["EpilogOnly"], str)


def test_list_checks_cached():
    """Calling list_checks multiple times should return the same cached registry."""
    reporter = Reporter()
    mod = FakeFullModule(reporter=reporter)
    first = mod.list_checks()
    second = mod.list_checks()
    assert first is second


# --- Tests for stale report pruning ---

from healthagent.reporter import HealthReport

def test_status_prunes_stale_reporter_keys():
    """Reporter keys that don't match any registered healthcheck should be removed by status()."""
    reporter = Reporter()
    # Simulate stale keys restored from a pickle (old check names)
    reporter.store["OldRenamedCheck"] = HealthReport()
    reporter.store["AnotherObsoleteCheck"] = HealthReport()
    # Add a valid key that matches a real healthcheck
    reporter.store["ActiveGPUHealthChecks"] = HealthReport()

    mod = FakeGpuModule(reporter=reporter)
    result = mod.status()

    # Stale keys should be gone
    assert "OldRenamedCheck" not in reporter.store
    assert "AnotherObsoleteCheck" not in reporter.store
    # Valid key should remain
    assert "ActiveGPUHealthChecks" in reporter.store
    # Summarized result should only contain the valid key
    assert "OldRenamedCheck" not in result
    assert "ActiveGPUHealthChecks" in result


def test_status_no_pruning_when_all_keys_valid():
    """When all reporter keys match registered checks, nothing should be pruned."""
    reporter = Reporter()
    reporter.store["ActiveGPUHealthChecks"] = HealthReport()
    reporter.store["NvlinkCheck"] = HealthReport()

    mod = FakeGpuModule(reporter=reporter)
    result = mod.status()

    assert len(reporter.store) == 2
    assert "ActiveGPUHealthChecks" in result


# --- Tests for _phase injection ---

class FakePhaseAwareModule(HealthModule):
    """Module with a handler that accepts _phase to apply phase-specific defaults."""

    DEFAULTS = {
        "prolog": {"level": "1"},
        "epilog": {"level": "2"},
    }

    @healthcheck("PhaseCheck", args=["level"])
    @prolog
    @epilog
    async def phase_check(self, level: str = '', _phase: str = None):
        defaults = self.DEFAULTS.get(_phase, {})
        level = level or defaults.get("level", "")
        return {"PhaseCheck": {"level": level, "phase": _phase}}


class FakePhaseUnawareModule(HealthModule):
    """Module with a handler that does NOT accept _phase — should still work."""

    @healthcheck("SimpleCheck")
    @epilog
    async def simple_check(self):
        return {"SimpleCheck": {"status": "OK"}}


async def test_phase_injected_for_epilog():
    """Handler with _phase param receives 'epilog' when run via epilog."""
    reporter = Reporter()
    mod = FakePhaseAwareModule(reporter=reporter)
    result = await mod.execute("epilog", checks={"PhaseCheck": {}})
    assert result["PhaseCheck"]["phase"] == "epilog"
    assert result["PhaseCheck"]["level"] == "2"


async def test_phase_injected_for_prolog():
    """Handler with _phase param receives 'prolog' when run via prolog."""
    reporter = Reporter()
    mod = FakePhaseAwareModule(reporter=reporter)
    result = await mod.execute("prolog", checks={"PhaseCheck": {}})
    assert result["PhaseCheck"]["phase"] == "prolog"
    assert result["PhaseCheck"]["level"] == "1"


async def test_explicit_kwarg_overrides_phase_default():
    """Explicitly provided kwargs should override phase defaults."""
    reporter = Reporter()
    mod = FakePhaseAwareModule(reporter=reporter)
    result = await mod.execute("epilog", checks={"PhaseCheck": {"level": "3"}})
    assert result["PhaseCheck"]["level"] == "3"
    assert result["PhaseCheck"]["phase"] == "epilog"


async def test_phase_not_injected_when_not_in_signature():
    """Handlers without _phase in signature should not receive it."""
    reporter = Reporter()
    mod = FakePhaseUnawareModule(reporter=reporter)
    result = await mod.execute("epilog")
    assert result["SimpleCheck"]["status"] == "OK"


def test_status_prunes_with_empty_registry():
    """Module with no healthchecks should prune all reporter keys."""
    reporter = Reporter()
    reporter.store["StaleKey"] = HealthReport()

    mod = FakeSystemdModule(reporter=reporter)
    result = mod.status()

    assert len(reporter.store) == 0
    assert "StaleKey" not in result


# --- Tests for args, description, and type coercion ---

class FakeDescriptionModule(HealthModule):
    """Module to test description fallback to docstring."""

    @healthcheck("WithDesc", description="Explicit description")
    @epilog
    async def with_desc(self):
        return {"WithDesc": {"status": "OK"}}

    @healthcheck("WithDocstring")
    @epilog
    async def with_docstring(self):
        """Docstring description here"""
        return {"WithDocstring": {"status": "OK"}}

    @healthcheck("NoDesc")
    @epilog
    async def no_desc(self):
        return {"NoDesc": {"status": "OK"}}


def test_description_explicit():
    reporter = Reporter()
    mod = FakeDescriptionModule(reporter=reporter)
    checks = mod.list_checks()
    assert checks["WithDesc"]["description"] == "Explicit description"


def test_description_fallback_to_docstring():
    reporter = Reporter()
    mod = FakeDescriptionModule(reporter=reporter)
    checks = mod.list_checks()
    assert checks["WithDocstring"]["description"] == "Docstring description here"


def test_description_empty_when_none():
    reporter = Reporter()
    mod = FakeDescriptionModule(reporter=reporter)
    checks = mod.list_checks()
    assert checks["NoDesc"]["description"] == ""


def test_healthcheck_args_stored():
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)
    checks = mod.list_checks()
    assert checks["MemTest"]["args"] == ["gpu_id"]
    assert checks["DiagTest"]["args"] == []


async def test_execute_coerces_string_to_list():
    """When a comma-separated string is passed for a list param, it should be split."""
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)
    result = await mod.execute("epilog", checks={"MemTest": {"gpu_id": "0,1,2"}})
    assert result["MemTest"]["gpu_id"] == ["0", "1", "2"]


async def test_execute_rejects_unknown_args():
    """Args not declared in healthcheck_args should be stripped."""
    reporter = Reporter()
    mod = FakeEpilogWithArgs(reporter=reporter)
    # MemTest allows gpu_id but not 'bad_arg'
    result = await mod.execute("epilog", checks={"MemTest": {"gpu_id": [0], "bad_arg": "x"}})
    assert result["MemTest"]["gpu_id"] == [0]

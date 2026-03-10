from healthagent import epilog, status, prolog
from healthagent.reporter import Reporter
from healthagent.healthmodule import HealthModule

# Validate _get_handlers with MRO-based discovery.

# Simulated module with multiple epilog handlers
class FakeGpuModule(HealthModule):

    @epilog
    async def run_epilog(self):
        return {"ActiveGPUHealthChecks": {"status": "OK"}}

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

    @epilog
    async def check_zombies(self):
        return {"ZombieCheck": {"status": "OK"}}

    @prolog
    async def check_readiness(self):
        return {"Readiness": {"status": "OK"}}


# Module with multi-decorated methods
class FakeMultiDecoratorModule(HealthModule):

    @prolog
    @epilog
    async def validate_both(self):
        return {"ValidateBoth": {"status": "OK"}}

    @prolog
    @status
    def check_and_report(self):
        return {"CheckAndReport": {"status": "OK"}}

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

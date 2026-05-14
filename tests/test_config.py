import yaml
import pytest
from unittest.mock import patch
from pydantic import ValidationError

from healthagent.config import (
    deep_merge, load_config, HealthagentConfig,
    ThresholdCheck, EvalType, ModuleName, ModuleConfig,
)
from healthagent.healthmodule import HealthModule
from healthagent.reporter import Reporter


# ── deep_merge tests ────────────────────────────────────────

class TestDeepMerge:

    def test_scalar_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 99}

    def test_list_replace(self):
        base = {"services": ["a", "b", "c"]}
        override = {"services": ["x", "y"]}
        result = deep_merge(base, override)
        assert result == {"services": ["x", "y"]}

    def test_dict_recursive_merge(self):
        base = {"gpu": {"warning": 83, "error": 90, "category": "Thermal"}}
        override = {"gpu": {"warning": 78}}
        result = deep_merge(base, override)
        assert result == {"gpu": {"warning": 78, "error": 90, "category": "Thermal"}}

    def test_null_removes_key(self):
        base = {"a": 1, "b": 2, "c": 3}
        override = {"b": None}
        result = deep_merge(base, override)
        assert result == {"a": 1, "c": 3}

    def test_null_removes_nested_key(self):
        base = {"gpu": {"field_watches": {"TEMP": {"warn": 83}, "PCIE": {"warn": 50}}}}
        override = {"gpu": {"field_watches": {"TEMP": None}}}
        result = deep_merge(base, override)
        assert result == {"gpu": {"field_watches": {"PCIE": {"warn": 50}}}}

    def test_null_nonexistent_key_is_noop(self):
        base = {"a": 1}
        override = {"nonexistent": None}
        result = deep_merge(base, override)
        assert result == {"a": 1}

    def test_new_key_added(self):
        base = {"a": 1}
        override = {"b": 2}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_deeply_nested(self):
        base = {"l1": {"l2": {"l3": {"val": "old", "keep": True}}}}
        override = {"l1": {"l2": {"l3": {"val": "new"}}}}
        result = deep_merge(base, override)
        assert result == {"l1": {"l2": {"l3": {"val": "new", "keep": True}}}}

    def test_override_dict_with_scalar(self):
        """If override replaces a dict with a scalar, scalar wins."""
        base = {"gpu": {"warning": 83, "error": 90}}
        override = {"gpu": "disabled"}
        result = deep_merge(base, override)
        assert result == {"gpu": "disabled"}

    def test_empty_override(self):
        base = {"a": 1, "b": {"c": 2}}
        result = deep_merge(base, {})
        assert result == base

    def test_empty_base(self):
        override = {"a": 1}
        result = deep_merge({}, override)
        assert result == {"a": 1}


# ── load_config tests ───────────────────────────────────────

class TestLoadConfig:

    def test_loads_defaults_only(self, tmp_path):
        """When no config.yaml exists, returns packaged defaults as HealthagentConfig."""
        defaults = {"modules": ["gpu", "network"], "gpu": {"xid": {"warning": [43]}}}

        with patch("healthagent.config._load_packaged_defaults", return_value=defaults):
            config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))

        assert isinstance(config, HealthagentConfig)
        assert config.modules == [ModuleName.GPU, ModuleName.NETWORK]
        assert config.gpu.xid.warning == [43]

    def test_merges_overrides(self, tmp_path):
        """User config.yaml overrides are merged into packaged defaults."""
        defaults = {"modules": ["gpu", "network"], "gpu": {"xid": {"warning": [43, 63]}}}
        overrides = {"gpu": {"xid": {"warning": [99]}}}

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(overrides))

        with patch("healthagent.config._load_packaged_defaults", return_value=defaults):
            config = load_config(config_path=str(config_file))

        assert config.gpu.xid.warning == [99]
        assert config.modules == [ModuleName.GPU, ModuleName.NETWORK]

    def test_null_override_removes_key(self, tmp_path):
        """null in config.yaml removes a default key via deep_merge."""
        defaults = {"gpu": {"field_watches": {
            "TEMP": {"eval": "gt", "warning": 83},
            "PCIE": {"eval": "gt", "warning": 50},
        }}}
        overrides = {"gpu": {"field_watches": {"TEMP": None}}}

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(overrides))

        with patch("healthagent.config._load_packaged_defaults", return_value=defaults):
            config = load_config(config_path=str(config_file))

        assert "TEMP" not in config.gpu.field_watches
        assert "PCIE" in config.gpu.field_watches

    def test_loads_packaged_defaults(self, tmp_path):
        """Always loads packaged defaults.yaml when no user config is present."""
        config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))

        assert isinstance(config, HealthagentConfig)
        assert len(config.modules) > 0
        assert config.gpu.gpudiagnosticcheck.prolog.tests == "short"


# ── HealthModule config injection tests ─────────────────────

class TestHealthModuleConfig:

    def test_config_defaults_to_module_config(self):
        """When no config is passed, self.config is a default ModuleConfig."""
        class FakeModule(HealthModule):
            pass
        module = FakeModule(reporter=Reporter())
        assert module.config == ModuleConfig()

    def test_config_is_stored(self):
        """When config is passed, it's available as self.config."""
        class FakeModule(HealthModule):
            pass
        test_config = ModuleConfig(services=["a", "b"])
        module = FakeModule(reporter=Reporter(), config=test_config)
        assert module.config == test_config
        assert module.config.services == ["a", "b"]

    def test_config_none_becomes_module_config(self):
        """Passing None explicitly results in default ModuleConfig."""
        class FakeModule(HealthModule):
            pass
        module = FakeModule(reporter=Reporter(), config=None)
        assert module.config == ModuleConfig()


# ── Schema validation tests ─────────────────────────────────

class TestSchemaValidation:

    def test_valid_defaults_pass(self, tmp_path):
        """Packaged defaults.yaml passes validation."""
        config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))
        assert isinstance(config, HealthagentConfig)

    def test_invalid_module_name(self):
        """Unknown module name is rejected."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({"modules": ["gpu", "bogus"]})

    def test_modules_not_a_list(self):
        """modules must be a list."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({"modules": "gpu"})

    def test_invalid_eval_type(self):
        """Invalid eval type in ThresholdCheck is rejected."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({
                "network": {"infiniband": {"state": {"eval": "invalid_op"}}}
            })

    def test_unknown_key_in_threshold_check(self):
        """Extra keys on ThresholdCheck are rejected (extra=forbid)."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({
                "network": {"infiniband": {"state": {"eval": "gt", "typo_field": 1}}}
            })

    def test_bad_xid_warning_element_type(self):
        """gpu.xid.warning must be list of ints."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({"gpu": {"xid": {"warning": ["not_int"]}}})

    def test_bad_services_element_type(self):
        """services list must contain strings."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({"systemd": {"services": [123]}})

    def test_bad_xid_warning_type(self):
        """gpu.xid.warning must be list of ints."""
        with pytest.raises(ValidationError):
            HealthagentConfig.model_validate({"gpu": {"xid": {"warning": ["not_int"]}}})

    def test_valid_threshold_check(self):
        """Valid ThresholdCheck with all eval types."""
        for eval_type in EvalType:
            check = ThresholdCheck(eval=eval_type, warning=10)
            assert check.eval == eval_type

    def test_valid_partial_config(self):
        """Partial config with only some modules specified."""
        config = HealthagentConfig.model_validate({"modules": ["gpu"]})
        assert config.modules == [ModuleName.GPU]
        # Other module configs still get defaults
        assert config.systemd.services == []
        assert config.proc.zombie_per_core == 50

    def test_model_dump_roundtrip(self):
        """model_dump produces a dict that can be re-validated."""
        config = HealthagentConfig.model_validate({
            "modules": ["gpu", "network"],
            "gpu": {"xid": {"warning": [43, 63]}},
        })
        dumped = config.model_dump()
        reloaded = HealthagentConfig.model_validate(dumped)
        assert reloaded.gpu.xid.warning == [43, 63]
        assert reloaded.modules == [ModuleName.GPU, ModuleName.NETWORK]

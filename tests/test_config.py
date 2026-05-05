import yaml
from unittest.mock import patch

from healthagent.config import deep_merge, load_config
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
        """When no config.yaml exists, returns packaged defaults unchanged."""
        defaults = {"modules": ["gpu", "network"], "gpu": {"temp": 83}}

        with patch("healthagent.config._load_packaged_defaults", return_value=defaults):
            config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))

        assert config == defaults

    def test_merges_overrides(self, tmp_path):
        """User config.yaml overrides are merged into packaged defaults."""
        defaults = {"modules": ["gpu", "network"], "gpu": {"temp": 83, "error": 90}}
        overrides = {"gpu": {"temp": 78}}

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(overrides))

        with patch("healthagent.config._load_packaged_defaults", return_value=defaults):
            config = load_config(config_path=str(config_file))

        assert config["gpu"]["temp"] == 78
        assert config["gpu"]["error"] == 90
        assert config["modules"] == ["gpu", "network"]

    def test_null_override_removes_key(self, tmp_path):
        """null in config.yaml removes a default key."""
        defaults = {"gpu": {"field_watches": {"TEMP": {"warn": 83}, "PCIE": {"warn": 50}}}}
        overrides = {"gpu": {"field_watches": {"TEMP": None}}}

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(overrides))

        with patch("healthagent.config._load_packaged_defaults", return_value=defaults):
            config = load_config(config_path=str(config_file))

        assert "TEMP" not in config["gpu"]["field_watches"]
        assert config["gpu"]["field_watches"]["PCIE"] == {"warn": 50}

    def test_loads_packaged_defaults(self, tmp_path):
        """Always loads packaged defaults.yaml when no user config is present."""
        config = load_config(config_path=str(tmp_path / "nonexistent.yaml"))

        # Should have loaded the real packaged defaults.yaml
        assert "modules" in config
        assert "gpu" in config


# ── HealthModule config injection tests ─────────────────────

class TestHealthModuleConfig:

    def test_config_defaults_to_empty_dict(self):
        """When no config is passed, self.config is an empty dict."""
        class FakeModule(HealthModule):
            pass
        module = FakeModule(reporter=Reporter())
        assert module.config == {}

    def test_config_is_stored(self):
        """When config is passed, it's available as self.config."""
        class FakeModule(HealthModule):
            pass
        test_config = {"temp": 83, "services": ["a", "b"]}
        module = FakeModule(reporter=Reporter(), config=test_config)
        assert module.config == test_config

    def test_config_none_becomes_empty_dict(self):
        """Passing None explicitly results in empty dict."""
        class FakeModule(HealthModule):
            pass
        module = FakeModule(reporter=Reporter(), config=None)
        assert module.config == {}

import os
import logging
import importlib.resources
import yaml

log = logging.getLogger(__name__)

CONFIG_DIR = "/etc/healthagent"
CONFIG_FILE = f"{CONFIG_DIR}/config.yaml"


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base.

    Rules:
      - dicts: recursive merge
      - lists/scalars: override replaces base
      - null value: removes the key from the result (explicit deletion)
    """
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
        elif key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_packaged_defaults() -> dict:
    """Load the defaults.yaml bundled with the package."""
    with importlib.resources.files("healthagent").joinpath("defaults.yaml").open() as f:
        return yaml.safe_load(f)


def load_config(config_path: str = CONFIG_FILE) -> dict:
    """Load defaults from the package, then overlay user config if present."""

    config = _load_packaged_defaults()

    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError(f"defaults.yaml must be a YAML mapping, got {type(config).__name__}")

    log.debug("Loaded default config from package")
    if os.path.isfile(config_path):
        log.info(f"Loading config overrides from {config_path}")
        with open(config_path) as f:
            overrides = yaml.safe_load(f)
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise ValueError(f"{config_path} must be a YAML mapping, got {type(overrides).__name__}")
        config = deep_merge(config, overrides)
    else:
        log.debug(f"No config file at {config_path}")

    return config

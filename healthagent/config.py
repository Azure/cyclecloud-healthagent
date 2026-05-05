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


def load_config(config_path: str = CONFIG_FILE) -> dict:
    """Load defaults from the package, then overlay user config if present."""
    defaults_path = os.path.join(CONFIG_DIR, "defaults.yaml")
    if os.path.isfile(defaults_path):
        with open(defaults_path) as f:
            config = yaml.safe_load(f)
    else:
        log.error(f"defaults.yaml not found at {defaults_path}, falling back to packaged defaults")
        with importlib.resources.open_text("healthagent", "defaults.yaml") as f:
            config = yaml.safe_load(f)
        defaults_path = "healthagent (package)"

    if config:
        log.debug(f"Loaded default config from {defaults_path}")
    if os.path.isfile(config_path):
        log.info(f"Loading config overrides from {config_path}")
        with open(config_path) as f:
            overrides = yaml.safe_load(f) or {}
        config = deep_merge(config, overrides)
    else:
        log.debug(f"No config file at {config_path}")

    return config

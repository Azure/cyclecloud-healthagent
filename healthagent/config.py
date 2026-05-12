import os
import logging
import importlib.resources
from enum import StrEnum
from typing import Union

import yaml
from pydantic import BaseModel, field_validator


log = logging.getLogger(__name__)

CONFIG_DIR = "/etc/healthagent"
CONFIG_FILE = f"{CONFIG_DIR}/config.yaml"


# ── Pydantic config models ─────────────────────────────────

class EvalType(StrEnum):
    GT = "gt"
    LT = "lt"
    GE = "ge"
    LE = "le"
    EQ = "eq"
    NE = "ne"
    IN = "in"
    BITMASK = "bitmask"
    DELTA_GT = "delta_gt"
    WINDOW_GT = "window_gt"


class ModuleName(StrEnum):
    GPU = "gpu"
    SYSTEMD = "systemd"
    NETWORK = "network"
    KMSG = "kmsg"
    PROC = "proc"


class ThresholdCheck(BaseModel, extra="forbid"):
    """A single threshold-based health check rule (network or GPU field watch)."""
    eval: EvalType
    msg: str | None = None
    category: str | None = None
    warning: Union[int, float, str, list] | None = None
    error: Union[int, float, str, list] | None = None
    window: int | None = None
    strikes: int = 0

    @field_validator('window')
    @classmethod
    def window_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError('window must be > 0')
        return v

    @field_validator('strikes')
    @classmethod
    def strikes_must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError('strikes must be >= 0')
        return v


class ModuleConfig(BaseModel):
    """Base config shared by all health modules."""
    services: list[str] = []


class NetworkConfig(ModuleConfig):
    infiniband: dict[str, ThresholdCheck] = {}
    ethernet: dict[str, ThresholdCheck] = {}


class XidConfig(BaseModel):
    warning: list[int] = []
    ignore: list[int] = []
    error: list[int] = []


class DiagPhase(BaseModel):
    tests: str = "short"
    params: str = ""


class GpuDiagnosticCheckConfig(BaseModel):
    prolog: DiagPhase = DiagPhase()
    epilog: DiagPhase = DiagPhase(tests="medium")


class GpuConfig(ModuleConfig):
    xid: XidConfig = XidConfig()
    gpudiagnosticcheck: GpuDiagnosticCheckConfig = GpuDiagnosticCheckConfig()
    field_watches: dict[str, ThresholdCheck] = {}


class SystemdConfig(ModuleConfig):
    pass


class ProcConfig(ModuleConfig):
    zombie_per_core: int | float = 50
    pid_max_warn_pct: int | float = 10
    pid_saturation_pct: int | float = 50


class HealthagentConfig(BaseModel, extra="allow"):
    modules: list[ModuleName] = list(ModuleName)
    network: NetworkConfig = NetworkConfig()
    gpu: GpuConfig = GpuConfig()
    systemd: SystemdConfig = SystemdConfig()
    proc: ProcConfig = ProcConfig()
    kmsg: ModuleConfig = ModuleConfig()


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


def load_config(config_path: str = CONFIG_FILE) -> HealthagentConfig:
    """Load defaults from the package, then overlay user config if present.

    Returns a validated HealthagentConfig object. Raises
    pydantic.ValidationError if the merged config has type errors.
    """

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

    return HealthagentConfig.model_validate(config)

# Cyclecloud-Healthagent Repository Map

> High-level orientation for contributors and agents. See [README.md](../../README.md) for detailed usage and configuration.

## What This Project Does

Healthagent is a node health monitoring daemon for HPC/AI workloads on datacenter-class GPUs. It runs as a systemd service, exposes a Unix socket API, and integrates with SLURM and Azure CycleCloud for automated node management.

## Directory Layout

| Path | Purpose |
|------|---------|
| `healthagent/` | Python package — all application source code |
| `healthagent/etc/` | Example SLURM integration scripts (epilog, prolog, health check) |
| `healthagent/tools/` | Helper scripts used by health checks at runtime |
| `tests/` | pytest test suite |
| `build/` | CycleCloud project spec (manifest, install scripts) |
| `specs/` | CycleCloud cluster-init specs |
| `blobs/` | Built artifacts |
| `pyproject.toml` | Package metadata, dependencies, and entry points |
| `package.sh` | Builds the sdist artifact |
| `deploy.sh` | Deploys artifact to a remote node |

## Architecture at a Glance

- **Async Python daemon** using `asyncio` with a Unix socket server
- **Modular health checks** — each check type is a separate module that inherits from a common base class
- **Health check modules**: GPU (via DCGM), systemd services (via D-Bus), kernel messages, network interfaces, processes
- **Decorator-driven**: Health checks declare their phase (background status, epilog, prolog) and scheduling via decorators
- **Reporting**: Each check produces a health report with severity levels (OK → Warning → Error). Reports are published to Azure CycleCloud
- **CLI client** (`health`) communicates with the daemon over the Unix socket
- **SLURM integration**: Example scripts in `healthagent/etc/` for draining/resuming nodes based on health status

## Key Entry Points

Defined in `pyproject.toml`:
- `healthagent` — background daemon
- `health` — CLI client
- `healthagent-install` — post-install setup

## Dependencies

- `dbus-next`, `systemd-python` — systemd/D-Bus monitoring
- `cuda-bindings` (optional) — GPU support
- DCGM ≥ 4.0.0 — GPU health checks (system-level dependency)

## Tests

Run with `pytest tests/`. Tests cover the health module base class, reporter, systemd monitor, and scheduler.

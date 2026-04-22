#!/usr/bin/env python3
"""
Inject DCGM field values for testing healthagent field watches.
Uses `dcgmi test --inject` CLI (official DCGM error injection API).
Requires DCGM hostengine running in standalone mode (nvidia-dcgm.service)
and healthagent started with DCGM_TEST_MODE=true.

Injected values expire from DCGM cache quickly, so the --duration flag
re-injects every second for the specified period.

Usage:
    python3 test_inject.py [--gpu 0] [--test all|temp|clocks|...] [--duration 120]

Examples:
    # Run all injection tests on GPU 0, keep injecting for 2 minutes
    python3 test_inject.py --test all --duration 120

    # Inject high temperature on GPU 1 for 60 seconds
    python3 test_inject.py --gpu 1 --test temp --duration 60

    # Clear injected values (inject healthy values)
    python3 test_inject.py --test clear
"""
import sys
import time
import argparse
import subprocess


def inject(gpu_id, field_id, value):
    """Inject a field value using dcgmi test --inject."""
    cmd = ["dcgmi", "test", "--inject", "--gpuid", str(gpu_id),
           "-f", str(field_id), "-v", str(value)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    ok = result.returncode == 0 and "Successfully" in result.stdout
    return ok


def inject_loop(gpu_id, field_id, value, duration, name):
    """Re-inject a value every second for duration seconds."""
    ok = inject(gpu_id, field_id, value)
    _report(name, str(value), ok)
    if duration > 1:
        end_time = time.time() + duration
        while time.time() < end_time:
            time.sleep(1)
            inject(gpu_id, field_id, value)


def _report(name, value_str, ok):
    status = "OK" if ok else "FAIL"
    print(f"  {name} = {value_str}  ->  {status}")


# ── Field IDs (from dcgm_fields.h) ────────────────────────────
# Using integer IDs directly so we don't need DCGM Python bindings
FI_GPU_TEMP                  = 150
FI_CLOCKS_EVENT_REASONS      = 112
FI_PERSISTENCE_MODE          = 65
FI_ECC_DBE_VOL_TOTAL         = 311
FI_ROW_REMAP_FAILURE         = 395
FI_RETIRED_SBE               = 390
FI_FABRIC_MANAGER_STATUS     = 170
FI_RECOVERY_ACTION           = 1523
FI_ECC_SBE_AGG_TOTAL         = 312
FI_PCIE_REPLAY_COUNTER       = 202
FI_FABRIC_HEALTH_MASK        = 174


# ── Test functions ─────────────────────────────────────────────

def test_temperature(gpu_id, duration):
    """Inject GPU temp 95°C — triggers gt error (> 90)."""
    print(f"\n=== GPU {gpu_id}: Temperature (warning > 83, error > 90) ===")
    inject_loop(gpu_id, FI_GPU_TEMP, 95, duration, "GPU_TEMP (error)")


def test_clocks(gpu_id, duration):
    """Inject clock throttle reasons — triggers bitmask error (0xE8)."""
    print(f"\n=== GPU {gpu_id}: CLOCKS_EVENT_REASONS = 0xE8 (all throttle bits) ===")
    inject_loop(gpu_id, FI_CLOCKS_EVENT_REASONS, 0xE8, duration, "CLOCKS_EVENT_REASONS")


def test_pcie_replay(gpu_id, duration):
    """Inject high PCIe replay counter — triggers delta_gt."""
    print(f"\n=== GPU {gpu_id}: PCIe replay counter (delta_gt warning > 50, error > 200) ===")
    inject_loop(gpu_id, FI_PCIE_REPLAY_COUNTER, 99999, duration, "PCIE_REPLAY_COUNTER")


def test_persistence_mode(gpu_id, duration):
    """Inject persistence mode = 0 — triggers ne 1 error."""
    print(f"\n=== GPU {gpu_id}: Persistence mode (error if != 1) ===")
    inject_loop(gpu_id, FI_PERSISTENCE_MODE, 0, duration, "PERSISTENCE_MODE")


def test_dbe(gpu_id, duration):
    """Inject volatile DBE error — triggers gt 0 error."""
    print(f"\n=== GPU {gpu_id}: Volatile DBE (error if > 0) ===")
    inject_loop(gpu_id, FI_ECC_DBE_VOL_TOTAL, 1, duration, "DBE_VOL_TOTAL")


def test_row_remap(gpu_id, duration):
    """Inject row remap failure — triggers ne 0 error."""
    print(f"\n=== GPU {gpu_id}: Row remap failure (error if != 0) ===")
    inject_loop(gpu_id, FI_ROW_REMAP_FAILURE, 1, duration, "ROW_REMAP_FAILURE")


def test_retired_sbe(gpu_id, duration):
    """Inject retired SBE pages = 65 — triggers gt error (> 63)."""
    print(f"\n=== GPU {gpu_id}: Retired SBE pages (warning > 50, error > 63) ===")
    inject_loop(gpu_id, FI_RETIRED_SBE, 65, duration, "RETIRED_SBE (error)")


def test_fabric_status(gpu_id, duration):
    """Inject fabric manager failure (status=4) — triggers DCGM_FR_FABRIC_PROBE_STATE via health watch."""
    print(f"\n=== GPU {gpu_id}: Fabric Manager status (error if in [4, 1]) ===")
    inject_loop(gpu_id, FI_FABRIC_MANAGER_STATUS, 4, duration, "FABRIC_STATUS (failure)")


def test_fabric_health(gpu_id, duration):
    """Inject fabric health mask with ROUTE_UNHEALTHY=TRUE — triggers DCGM_FR_FIELD_VIOLATION via health watch.

    Bitmask layout (from nvml.h):
      [1:0]  DEGRADED_BW              (0=NOT_SUPPORTED, 1=TRUE, 2=FALSE)
      [3:2]  ROUTE_RECOVERY           (0=NOT_SUPPORTED, 1=TRUE, 2=FALSE)
      [5:4]  ROUTE_UNHEALTHY          (0=NOT_SUPPORTED, 1=TRUE, 2=FALSE)
      [7:6]  ACCESS_TIMEOUT_RECOVERY  (0=NOT_SUPPORTED, 1=TRUE, 2=FALSE)
      [11:8] INCORRECT_CONFIGURATION  (0=NOT_SUPPORTED, 1=NONE, ...)

    Healthy = 0x1AA (all FALSE/NONE).  Unhealthy = 0x19A (ROUTE_UNHEALTHY=TRUE).
    """
    print(f"\n=== GPU {gpu_id}: Fabric Health Mask (ROUTE_UNHEALTHY=TRUE) ===")
    inject_loop(gpu_id, FI_FABRIC_HEALTH_MASK, 0x19A, duration, "FABRIC_HEALTH_MASK (route_unhealthy)")


def test_recovery_action(gpu_id, duration):
    """Inject recovery action = 3 (GPU_RESET) — triggers in [3, 4] error."""
    print(f"\n=== GPU {gpu_id}: Recovery action (warning [2], error [3, 4]) ===")
    inject_loop(gpu_id, FI_RECOVERY_ACTION, 3, duration, "RECOVERY_ACTION (GPU_RESET)")


def test_sbe_rate(gpu_id, duration):
    """Inject high SBE aggregate counter — triggers delta_gt."""
    print(f"\n=== GPU {gpu_id}: SBE aggregate rate (delta_gt warning > 100/min) ===")
    inject_loop(gpu_id, FI_ECC_SBE_AGG_TOTAL, 99999, duration, "SBE_AGG_TOTAL")


def test_clear(gpu_id, duration):
    """Inject healthy values to clear previous injections."""
    print(f"\n=== GPU {gpu_id}: Clearing — injecting healthy values ===")
    clears = [
        (FI_GPU_TEMP, 45, "GPU_TEMP"),
        (FI_CLOCKS_EVENT_REASONS, 0, "CLOCKS_EVENT_REASONS"),
        (FI_PERSISTENCE_MODE, 1, "PERSISTENCE_MODE"),
        (FI_ECC_DBE_VOL_TOTAL, 0, "DBE_VOL_TOTAL"),
        (FI_ROW_REMAP_FAILURE, 0, "ROW_REMAP_FAILURE"),
        (FI_RETIRED_SBE, 0, "RETIRED_SBE"),
        (FI_FABRIC_MANAGER_STATUS, 3, "FABRIC_STATUS (success)"),
        (FI_FABRIC_HEALTH_MASK, 0x1AA, "FABRIC_HEALTH_MASK (healthy)"),
        (FI_RECOVERY_ACTION, 0, "RECOVERY_ACTION"),
        (FI_ECC_SBE_AGG_TOTAL, 0, "SBE_AGG_TOTAL"),
        (FI_PCIE_REPLAY_COUNTER, 0, "PCIE_REPLAY_COUNTER"),
    ]
    for field_id, value, name in clears:
        ok = inject(gpu_id, field_id, value)
        _report(name, str(value), ok)


# ── Main ───────────────────────────────────────────────────────

TESTS = {
    "temp":     test_temperature,
    "clocks":   test_clocks,
    "pcie":     test_pcie_replay,
    "persist":  test_persistence_mode,
    "dbe":      test_dbe,
    "remap":    test_row_remap,
    "sbe":      test_retired_sbe,
    "fabric":   test_fabric_status,
    "fabric_health": test_fabric_health,
    "recovery": test_recovery_action,
    "sberate":  test_sbe_rate,
    "clear":    test_clear,
}


def main():
    parser = argparse.ArgumentParser(description="Inject DCGM field values for testing healthagent")
    parser.add_argument("--gpu", type=int, default=0, help="GPU ID to inject into (default: 0)")
    parser.add_argument("--test", type=str, default="all",
                        choices=["all"] + list(TESTS.keys()),
                        help="Which test to run (default: all)")
    parser.add_argument("--duration", type=int, default=120,
                        help="How long to keep re-injecting values in seconds (default: 120)")
    args = parser.parse_args()

    # Verify dcgmi is available
    result = subprocess.run(["which", "dcgmi"], capture_output=True)
    if result.returncode != 0:
        print("Error: dcgmi not found. Is DCGM installed?")
        sys.exit(1)

    gpu_id = args.gpu
    duration = args.duration
    print(f"Target GPU: {gpu_id}, Duration: {duration}s")

    if args.test == "all":
        for name, func in TESTS.items():
            if name == "clear":
                continue
            print(f"\n{'='*60}")
            print(f"  Running: {name}")
            print(f"{'='*60}")
            func(gpu_id, duration)
    else:
        TESTS[args.test](gpu_id, duration)

    print("\n" + "="*60)
    print("Done. Check healthagent output with: sudo health -s")
    print("To clear injected values: python3 test_inject.py --test clear")


if __name__ == "__main__":
    main()

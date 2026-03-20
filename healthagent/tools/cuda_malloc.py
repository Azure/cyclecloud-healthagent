import argparse, sys

try:
    from cuda.bindings import runtime as cudart
except ImportError:
    print("cuda.bindings not available, skipping memory allocation test", file=sys.stderr)
    sys.exit(2)


def check(result):
    if result[0] != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f"CUDA error: {cudart.cudaGetErrorString(result[0])[1]}")
    return result[1] if len(result) == 2 else result[1:]


def allocate_on_gpu(gpu_id: int, pct: float) -> None:
    check(cudart.cudaSetDevice(gpu_id))
    total = check(cudart.cudaMemGetInfo())[1]
    size = int(total * pct / 100)
    ptr = None
    try:
        ptr = check(cudart.cudaMalloc(size))
        check(cudart.cudaMemset(ptr, 0xAB, size))
    finally:
        if ptr is not None:
            # Best effort, dont mask original exception
            cudart.cudaFree(ptr)
    print(f"  GPU {gpu_id}: {total / 1e9:.2f} GB total, allocated {pct}% ({size / 1e9:.2f} GB) - OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pct", type=float, default=95, help="Percent of total GPU memory (default: 95)")
    parser.add_argument("--gpus", type=str, default=None, help="Comma-separated GPU IDs (default: all)")
    args = parser.parse_args()

    if args.pct <= 0 or args.pct >= 100:
        parser.error(f"--pct must be between 0 and 100 exclusive, got {args.pct}")

    test_name = "Memory Allocation test"
    gpu_count = check(cudart.cudaGetDeviceCount())
    gpu_ids = [int(g) for g in args.gpus.split(",")] if args.gpus else list(range(gpu_count))
    print(f"Found {gpu_count} GPU(s), testing {gpu_ids} at {args.pct}%\n")

    failures = []
    for gpu in gpu_ids:
        try:
            allocate_on_gpu(gpu, args.pct)
        except RuntimeError as e:
            failures.append(f"{test_name} failed on GPU {gpu}: {e}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        sys.exit(1)

import operator
import os
from pathlib import Path


def _read_file_nonblock(filepath: str) -> str | None:
    """Read a single pseudo-filesystem file using O_NONBLOCK.

    Returns the stripped file contents, or None if the file cannot be read
    (missing, permission denied, blocking pseudo-file like /proc/kmsg,
    or binary content like /proc/kcore).
    """
    fd = -1
    try:
        fd = os.open(filepath, os.O_RDONLY | os.O_NONBLOCK)
        data = os.read(fd, 4096)
        if b"\x00" in data:
            return None
        return data.decode("utf-8", errors="replace").strip()
    except (OSError, IOError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def read_kernel_attrs(root: str | Path, paths: list[str] | None = None) -> dict:
    """Read attributes from a kernel pseudo-filesystem (/sys, /proc).

    Args:
        root: Base directory to read from (e.g. "/sys/class/net").
              Accepts str or Path objects (as returned by enumeration mode).
        paths: Optional list of relative paths to read. Supports nested paths
               like "statistics/rx_errors" which produce nested dicts.
               If None, enumerates all top-level entries (see below).

    Returns:
        When paths is provided (selective mode):
            Dict mapping filenames to their stripped string contents, or
            Path objects for entries that resolve to directories.
            Symlinks are resolved: links to files are read as strings,
            links to directories are returned as resolved Path objects.
            Nested paths (e.g. "counters/symbol_error") produce nested dicts.
            If a path conflicts with a nested prefix, the nested dict wins.

        When paths is None (enumeration mode):
            Dict mapping entry names to:
            - str: file contents (stripped) for regular files.
            - Path: resolved absolute path for directories.
            Symlinks are resolved: links to files are read as strings,
            links to directories are returned as resolved Path objects.
            Use str(value) to pass to a subsequent read_kernel_attrs() call.

        Unreadable or missing entries are silently skipped.
        All reads are performed non-blocking.
        Returns {} if root does not exist.

    Examples:
        # Enumerate /sys/class/net — symlinks resolve to Path objects
        read_kernel_attrs("/sys/class/net")
        {"ib0": Path("/sys/devices/.../net/ib0"), "eth0": Path("/sys/devices/.../net/eth0")}

        # Enumerate an interface directory — files are strings, subdirs are Paths
        read_kernel_attrs("/sys/devices/.../net/ib0")
        {"operstate": "up", "mtu": "2044", "statistics": Path("/.../statistics"), ...}

        # Selective read — files are strings, directories are Paths
        read_kernel_attrs("/sys/devices/.../net/ib0", ["operstate", "statistics/rx_errors"])
        {"operstate": "up", "statistics": {"rx_errors": "0"}}

        # Selecting a directory returns a Path (usable for follow-up reads)
        read_kernel_attrs("/sys/devices/.../net/ib0", ["operstate", "statistics"])
        {"operstate": "up", "statistics": Path("/.../statistics")}
    """
    root = str(root)
    if not os.path.isdir(root):
        return {}

    if paths is None:
        return _read_top_level(root)

    result = {}
    for path in paths:
        parts = path.split("/")
        full = os.path.join(root, path)
        resolved = os.path.realpath(full)
        if os.path.isdir(resolved):
            value = Path(resolved)
        else:
            value = _read_file_nonblock(resolved)
            if value is None:
                continue

        # Walk into nested dict for paths like "statistics/rx_errors"
        node = result
        for part in parts[:-1]:
            existing = node.get(part)
            if not isinstance(existing, dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    return result


def _read_top_level(root: str) -> dict:
    """Read all top-level entries in a kernel pseudo-filesystem directory.

    Files (and symlinks to files) are read and returned as stripped strings.
    Directories (and symlinks to directories) are returned as resolved Path objects.
    Uses O_NONBLOCK to avoid hanging on blocking pseudo-files (e.g. /proc/kmsg).
    """
    result = {}
    try:
        entries = os.listdir(root)
    except OSError:
        return result

    for entry in entries:
        full = os.path.join(root, entry)
        resolved = os.path.realpath(full)
        if os.path.isdir(resolved):
            result[entry] = Path(resolved)
        elif os.path.isfile(resolved):
            value = _read_file_nonblock(resolved)
            if value is not None:
                result[entry] = value
    return result


def evaluate(eval_type, value, threshold, *, prev_value=None, prev_time=None,
             current_time=None, window=60):
    """Unified threshold evaluation. Returns (triggered: bool, evaluated_value).

    For delta_gt, evaluated_value is the computed rate per window.
    For bitmask, evaluated_value is the matching bits (value & threshold).
    For all others, evaluated_value is the input value.

    Args:
        eval_type: Comparison type (gt, lt, ge, le, eq, ne, in, bitmask, delta_gt)
        value: Current value to evaluate
        threshold: Threshold to compare against (list for 'in' eval type)
        prev_value: Previous sample value (delta_gt only)
        prev_time: Previous sample timestamp in monotonic seconds (delta_gt only)
        current_time: Current sample timestamp in monotonic seconds (delta_gt only)
        window: Time window in seconds for rate normalization (delta_gt only, default: 60)
    """
    eval_type = str(eval_type).strip().lower()

    if eval_type == "gt":
        return value > threshold, value
    elif eval_type == "lt":
        return value < threshold, value
    elif eval_type == "ge":
        return value >= threshold, value
    elif eval_type == "le":
        return value <= threshold, value
    elif eval_type == "eq":
        return value == threshold, value
    elif eval_type == "ne":
        return value != threshold, value
    elif eval_type == "in":
        return value in threshold, value
    elif eval_type == "bitmask":
        matched = operator.index(value) & operator.index(threshold)
        return matched != 0, matched
    elif eval_type == "delta_gt":
        if prev_value is None or prev_time is None or current_time is None:
            return False, 0.0
        delta = value - prev_value
        if delta < 0:
            return False, 0.0
        elapsed = current_time - prev_time
        if elapsed <= 0:
            return False, 0.0
        rate = (delta * window) / elapsed
        return rate > threshold, rate
    raise ValueError(f"Unknown eval_type: {eval_type!r}")
import operator


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
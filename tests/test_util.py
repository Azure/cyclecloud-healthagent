from healthagent.util import evaluate
import pytest


class TestEvaluateComparisons:

    def test_gt_true(self):
        triggered, val = evaluate("gt", 85, 80)
        assert triggered is True
        assert val == 85

    def test_gt_false_equal(self):
        triggered, val = evaluate("gt", 80, 80)
        assert triggered is False

    def test_gt_false_below(self):
        triggered, val = evaluate("gt", 75, 80)
        assert triggered is False

    def test_lt_true(self):
        triggered, val = evaluate("lt", 3, 5)
        assert triggered is True
        assert val == 3

    def test_lt_false(self):
        triggered, val = evaluate("lt", 5, 5)
        assert triggered is False

    def test_ge_true_equal(self):
        triggered, val = evaluate("ge", 80, 80)
        assert triggered is True

    def test_ge_true_above(self):
        triggered, val = evaluate("ge", 81, 80)
        assert triggered is True

    def test_ge_false(self):
        triggered, val = evaluate("ge", 79, 80)
        assert triggered is False

    def test_le_true_equal(self):
        triggered, val = evaluate("le", 80, 80)
        assert triggered is True

    def test_le_true_below(self):
        triggered, val = evaluate("le", 79, 80)
        assert triggered is True

    def test_le_false(self):
        triggered, val = evaluate("le", 81, 80)
        assert triggered is False

    def test_eq_true(self):
        triggered, val = evaluate("eq", 4, 4)
        assert triggered is True

    def test_eq_false(self):
        triggered, val = evaluate("eq", 4, 5)
        assert triggered is False

    def test_ne_true(self):
        triggered, val = evaluate("ne", 0, 1)
        assert triggered is True

    def test_ne_false(self):
        triggered, val = evaluate("ne", 1, 1)
        assert triggered is False

    def test_float_gt(self):
        triggered, val = evaluate("gt", 700.5, 700.0)
        assert triggered is True
        assert val == 700.5

    def test_returns_input_value(self):
        """All simple comparisons return the input value as evaluated_value."""
        for op in ("gt", "lt", "ge", "le", "eq", "ne"):
            _, val = evaluate(op, 42, 100)
            assert val == 42


class TestEvaluateBitmask:

    def test_bitmask_match(self):
        # HW_SLOWDOWN (0x08) is set in 0xE8
        triggered, matched = evaluate("bitmask", 0x08, 0xE8)
        assert triggered is True
        assert matched == 0x08

    def test_bitmask_multiple_bits(self):
        # HW_SLOWDOWN | HW_THERMAL (0x48) against mask 0xE8
        triggered, matched = evaluate("bitmask", 0x48, 0xE8)
        assert triggered is True
        assert matched == 0x48

    def test_bitmask_no_match(self):
        # GPU_IDLE (0x01) not in mask 0xE8
        triggered, matched = evaluate("bitmask", 0x01, 0xE8)
        assert triggered is False
        assert matched == 0

    def test_bitmask_zero_value(self):
        triggered, matched = evaluate("bitmask", 0, 0xFF)
        assert triggered is False
        assert matched == 0

    def test_bitmask_returns_matched_bits(self):
        # Only overlapping bits returned
        triggered, matched = evaluate("bitmask", 0xFF, 0x08)
        assert triggered is True
        assert matched == 0x08

    def test_bitmask_rejects_float_value(self):
        with pytest.raises(TypeError):
            evaluate("bitmask", 3.14, 0xFF)

    def test_bitmask_rejects_float_threshold(self):
        with pytest.raises(TypeError):
            evaluate("bitmask", 0xFF, 3.14)


class TestEvaluateDeltaGt:

    def test_normal_rate_triggers(self):
        # 120 events in 60 seconds = 120/min, threshold 100/min
        triggered, rate = evaluate("delta_gt", 620, 100,
                                   prev_value=500, prev_time=0.0, current_time=60.0)
        assert triggered is True
        assert rate == 120.0

    def test_normal_rate_below_threshold(self):
        # 50 events in 60 seconds = 50/min, threshold 100/min
        triggered, rate = evaluate("delta_gt", 550, 100,
                                   prev_value=500, prev_time=0.0, current_time=60.0)
        assert triggered is False
        assert rate == 50.0

    def test_exact_threshold_not_triggered(self):
        # 100 events in 60 seconds = 100/min, threshold 100/min (not strictly greater)
        triggered, rate = evaluate("delta_gt", 600, 100,
                                   prev_value=500, prev_time=0.0, current_time=60.0)
        assert triggered is False
        assert rate == 100.0

    def test_negative_delta_ignored(self):
        # Counter reset: current < prev
        triggered, rate = evaluate("delta_gt", 10, 0,
                                   prev_value=500, prev_time=0.0, current_time=60.0)
        assert triggered is False
        assert rate == 0.0

    def test_zero_elapsed_ignored(self):
        triggered, rate = evaluate("delta_gt", 600, 100,
                                   prev_value=500, prev_time=10.0, current_time=10.0)
        assert triggered is False
        assert rate == 0.0

    def test_missing_prev_value(self):
        # First sample — no previous data
        triggered, rate = evaluate("delta_gt", 500, 100,
                                   prev_value=None, prev_time=0.0, current_time=60.0)
        assert triggered is False
        assert rate == 0.0

    def test_missing_prev_time(self):
        triggered, rate = evaluate("delta_gt", 500, 100,
                                   prev_value=400, prev_time=None, current_time=60.0)
        assert triggered is False
        assert rate == 0.0

    def test_missing_current_time(self):
        triggered, rate = evaluate("delta_gt", 500, 100,
                                   prev_value=400, prev_time=0.0, current_time=None)
        assert triggered is False
        assert rate == 0.0

    def test_custom_window_per_hour(self):
        # 10 events in 60 seconds = 600/hour, threshold 500/hour
        triggered, rate = evaluate("delta_gt", 510, 500,
                                   prev_value=500, prev_time=0.0, current_time=60.0,
                                   window=3600)
        assert triggered is True
        assert rate == 600.0

    def test_custom_window_per_second(self):
        # 120 events in 60 seconds = 2/sec, threshold 1/sec
        triggered, rate = evaluate("delta_gt", 620, 1,
                                   prev_value=500, prev_time=0.0, current_time=60.0,
                                   window=1)
        assert triggered is True
        assert rate == 2.0

    def test_threshold_zero_any_increment(self):
        # Any new event triggers when threshold is 0
        triggered, rate = evaluate("delta_gt", 501, 0,
                                   prev_value=500, prev_time=0.0, current_time=60.0)
        assert triggered is True
        assert rate > 0

    def test_no_change_threshold_zero(self):
        # No new events, threshold 0 — should not trigger
        triggered, rate = evaluate("delta_gt", 500, 0,
                                   prev_value=500, prev_time=0.0, current_time=60.0)
        assert triggered is False
        assert rate == 0.0

    def test_delayed_cycle_rate_normalized(self):
        # 250 events over 5 minutes = 50/min, threshold 100/min — should NOT trigger
        triggered, rate = evaluate("delta_gt", 750, 100,
                                   prev_value=500, prev_time=0.0, current_time=300.0)
        assert triggered is False
        assert rate == 50.0


class TestEvaluateIn:

    def test_in_match(self):
        triggered, val = evaluate("in", 4, [4, 1])
        assert triggered is True
        assert val == 4

    def test_in_match_second(self):
        triggered, val = evaluate("in", 1, [4, 1])
        assert triggered is True
        assert val == 1

    def test_in_no_match(self):
        triggered, val = evaluate("in", 3, [4, 1])
        assert triggered is False
        assert val == 3

    def test_in_single_element(self):
        triggered, val = evaluate("in", 2, [2])
        assert triggered is True

    def test_in_empty_list(self):
        triggered, val = evaluate("in", 4, [])
        assert triggered is False
        assert val == 4

    def test_in_returns_input_value(self):
        _, val = evaluate("in", 42, [1, 2, 3])
        assert val == 42


class TestEvaluateUnknown:

    def test_unknown_eval_type(self):
        with pytest.raises(ValueError, match="Unknown eval_type"):
            evaluate("invalid_op", 85, 80)

    def test_empty_eval_type(self):
        with pytest.raises(ValueError, match="Unknown eval_type"):
            evaluate("", 85, 80)


class TestEvaluateNormalization:

    def test_uppercase_eval_type(self):
        triggered, val = evaluate("GT", 85, 80)
        assert triggered is True

    def test_whitespace_eval_type(self):
        triggered, val = evaluate("  gt  ", 85, 80)
        assert triggered is True

    def test_none_eval_type_raises(self):
        with pytest.raises(ValueError, match="Unknown eval_type"):
            evaluate(None, 85, 80)

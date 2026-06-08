from healthagent.util import evaluate, read_kernel_attrs, TimeSeries
from pathlib import Path
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


class TestTimeSeries:

    def test_record_and_len(self):
        ts = TimeSeries(maxlen=10)
        assert len(ts) == 0
        ts.record(5, timestamp=0.0)
        ts.record(10, timestamp=1.0)
        assert len(ts) == 2

    def test_maxlen_evicts_oldest(self):
        ts = TimeSeries(maxlen=3)
        ts.record(1, timestamp=0.0)
        ts.record(2, timestamp=1.0)
        ts.record(3, timestamp=2.0)
        ts.record(4, timestamp=3.0)
        assert len(ts) == 3
        # Oldest sample (1, 0.0) should be evicted
        assert ts._samples[0] == (2, 1.0)

    def test_delta_in_window_insufficient_samples(self):
        ts = TimeSeries()
        ts.record(5, timestamp=0.0)
        delta, sufficient = ts.delta_in_window(100)
        assert sufficient is False
        assert delta == 0

    def test_delta_in_window_insufficient_timespan(self):
        ts = TimeSeries()
        ts.record(0, timestamp=0.0)
        ts.record(5, timestamp=50.0)
        # Window is 100s but only 50s of data
        delta, sufficient = ts.delta_in_window(100)
        assert sufficient is False
        assert delta == 0

    def test_delta_in_window_exact_window(self):
        ts = TimeSeries()
        ts.record(10, timestamp=0.0)
        ts.record(15, timestamp=100.0)
        delta, sufficient = ts.delta_in_window(100)
        assert sufficient is True
        assert delta == 5

    def test_delta_in_window_exceeds_window(self):
        ts = TimeSeries()
        ts.record(0, timestamp=0.0)
        ts.record(3, timestamp=50.0)
        ts.record(7, timestamp=150.0)
        # Window=100, cutoff=50.0 — oldest in window is (3, 50.0)
        delta, sufficient = ts.delta_in_window(100)
        assert sufficient is True
        assert delta == 4  # 7 - 3

    def test_delta_in_window_all_samples_outside(self):
        """If all samples are older than the cutoff except the latest,
        the latest is both the cutoff match and the endpoint — delta=0."""
        ts = TimeSeries()
        ts.record(0, timestamp=0.0)
        ts.record(5, timestamp=1.0)
        ts.record(100, timestamp=500.0)
        # Window=10, cutoff=490 — only (100, 500) is in window
        delta, sufficient = ts.delta_in_window(10)
        assert sufficient is True
        assert delta == 0  # 100 - 100

    def test_delta_in_window_no_samples(self):
        ts = TimeSeries()
        delta, sufficient = ts.delta_in_window(100)
        assert sufficient is False
        assert delta == 0

    def test_default_timestamp_uses_monotonic(self):
        ts = TimeSeries()
        ts.record(1)
        ts.record(2)
        assert len(ts) == 2
        # Timestamps should be monotonically increasing
        assert ts._samples[1][1] >= ts._samples[0][1]

    def test_counter_reset_clamps_to_zero(self):
        """Counter reset (value drops) should return delta=0, not negative."""
        ts = TimeSeries()
        ts.record(50, timestamp=0.0)
        ts.record(55, timestamp=50.0)
        ts.record(0, timestamp=100.0)  # counter reset
        delta, sufficient = ts.delta_in_window(100)
        assert sufficient is True
        assert delta == 0


class TestEvaluateWindowGt:

    def _build_ts(self, samples):
        """Helper: build a TimeSeries from a list of (value, timestamp) pairs."""
        ts = TimeSeries()
        for val, t in samples:
            ts.record(val, timestamp=t)
        return ts

    def test_no_samples_object(self):
        triggered, val = evaluate("window_gt", 5, 3, window=100, samples=None)
        assert triggered is False
        assert val == 0

    def test_insufficient_history(self):
        ts = TimeSeries()
        ts.record(5, timestamp=0.0)
        # Only 1 sample, insufficient
        triggered, delta = evaluate("window_gt", 5, 3, window=10800, samples=ts)
        assert triggered is False

    def test_insufficient_timespan(self):
        ts = self._build_ts([(0, 0.0), (2, 60.0)])
        # Window is 10800 but only 60s of data
        triggered, delta = evaluate("window_gt", 2, 3, window=10800, samples=ts)
        assert triggered is False

    def test_triggers_when_delta_exceeds_threshold(self):
        """3 flaps in 3-hour window should trigger with threshold=3."""
        ts = self._build_ts([
            (0, 0.0),
            (1, 1800.0),
            (2, 5400.0),
            (4, 10800.0),
        ])
        # delta = 4 - 0 = 4 > 3
        triggered, delta = evaluate("window_gt", 4, 3, window=10800, samples=ts)
        assert triggered is True
        assert delta == 4

    def test_does_not_trigger_below_threshold(self):
        """2 flaps in 3-hour window should not trigger with threshold=3."""
        ts = self._build_ts([
            (0, 0.0),
            (1, 3600.0),
            (2, 10800.0),
        ])
        # delta = 2 - 0 = 2, not > 3
        triggered, delta = evaluate("window_gt", 2, 3, window=10800, samples=ts)
        assert triggered is False
        assert delta == 2

    def test_exact_threshold_triggered(self):
        """Delta exactly equal to threshold should trigger (gte)."""
        ts = self._build_ts([
            (0, 0.0),
            (1, 3600.0),
            (3, 10800.0),
        ])
        triggered, delta = evaluate("window_gt", 3, 3, window=10800, samples=ts)
        assert triggered is True
        assert delta == 3

    def test_windowed_delta_uses_cutoff(self):
        """Events outside the window should not count toward the delta."""
        ts = self._build_ts([
            (0, 0.0),       # outside window at t=10801
            (3, 5000.0),
            (4, 8000.0),
            (5, 10801.0),
        ])
        # cutoff = 10801 - 10800 = 1.0
        # Oldest in window is (3, 5000.0), delta = 5 - 3 = 2
        triggered, delta = evaluate("window_gt", 5, 3, window=10800, samples=ts)
        assert triggered is False
        assert delta == 2

    def test_evaluate_does_not_modify_timeseries(self):
        """evaluate() should not record into the TimeSeries."""
        ts = self._build_ts([(0, 0.0), (1, 10800.0)])
        assert len(ts) == 2
        evaluate("window_gt", 42, 100, window=10800, samples=ts)
        assert len(ts) == 2

    def test_spread_over_6_hours(self):
        """4 flaps spread over 6 hours — first window has 2, second has 3."""
        ts = self._build_ts([
            (0, 0.0),
            (1, 3600.0),     # 1hr
            (2, 7200.0),     # 2hr
            (3, 10860.0),    # 3hr+1min
        ])
        # cutoff = 10860 - 10800 = 60 → oldest in window is (1, 3600), delta = 3-1=2
        triggered, delta = evaluate("window_gt", 3, 3, window=10800, samples=ts)
        assert triggered is False

        # Add sample at t=14400 (4hr)
        ts.record(4, timestamp=14400.0)
        # cutoff = 14400 - 10800 = 3600 → oldest in window is (1, 3600), delta = 4-1=3
        triggered, delta = evaluate("window_gt", 4, 3, window=10800, samples=ts)
        assert triggered is True
        assert delta == 3


class TestReadKernelAttrs:
    """Tests for read_kernel_attrs using a fake sysfs-like directory tree.

    Simulates the layout of /sys/class/net/<iface> and
    /sys/class/infiniband/<dev>/ports/<port> with plain files
    and subdirectories, similar to what the kernel exposes.
    """

    @pytest.fixture(autouse=True)
    def fake_sysfs(self, tmp_path):
        """Build a fake sysfs tree resembling /sys/class/net/ib0 and
        /sys/class/infiniband/mlx5_ib0/ports/1."""

        # -- /sys/class/net/ib0 --
        ib0 = tmp_path / "net" / "ib0"
        ib0.mkdir(parents=True)
        (ib0 / "operstate").write_text("up\n")
        (ib0 / "carrier").write_text("1\n")
        (ib0 / "carrier_changes").write_text("1\n")
        (ib0 / "carrier_down_count").write_text("0\n")
        (ib0 / "type").write_text("32\n")
        (ib0 / "mtu").write_text("2044\n")
        (ib0 / "flags").write_text("0x1043\n")
        (ib0 / "mode").write_text("datagram\n")

        # statistics subdirectory with counter files
        stats = ib0 / "statistics"
        stats.mkdir()
        (stats / "rx_bytes").write_text("123456789\n")
        (stats / "tx_bytes").write_text("987654321\n")
        (stats / "rx_errors").write_text("0\n")
        (stats / "tx_errors").write_text("0\n")
        (stats / "rx_dropped").write_text("0\n")

        # device subdirectory (in real sysfs this is a symlink)
        device = ib0 / "device"
        device.mkdir()

        # -- /sys/class/infiniband/mlx5_ib0/ports/1 --
        port = tmp_path / "infiniband" / "mlx5_ib0" / "ports" / "1"
        port.mkdir(parents=True)
        (port / "state").write_text("4: ACTIVE\n")
        (port / "phys_state").write_text("5: LinkUp\n")
        (port / "rate").write_text("400 Gb/sec (4X NDR)\n")
        (port / "lid").write_text("0x0001\n")
        (port / "sm_lid").write_text("0x0001\n")
        (port / "link_layer").write_text("InfiniBand\n")

        # counters subdirectory
        counters = port / "counters"
        counters.mkdir()
        (counters / "symbol_error").write_text("0\n")
        (counters / "link_error_recovery").write_text("0\n")
        (counters / "link_downed").write_text("0\n")
        (counters / "port_rcv_errors").write_text("42\n")
        (counters / "local_link_integrity_errors").write_text("0\n")
        (counters / "excessive_buffer_overrun_errors").write_text("0\n")
        (counters / "port_rcv_data").write_text("58742985234\n")
        (counters / "port_xmit_data").write_text("39281740123\n")

        # hw_counters subdirectory
        hw = port / "hw_counters"
        hw.mkdir()
        (hw / "duplicate_request").write_text("0\n")
        (hw / "out_of_sequence").write_text("0\n")
        (hw / "local_ack_timeout_err").write_text("5\n")
        (hw / "req_transport_retries_exceeded").write_text("0\n")
        (hw / "lifespan").write_text("10\n")

        # gid_attrs for port-to-ndev matching
        gid_attrs = port / "gid_attrs" / "ndevs"
        gid_attrs.mkdir(parents=True)
        (gid_attrs / "0").write_text("ib0\n")

        self.root = tmp_path
        self.ib0 = ib0
        self.port = port

    # --- Top-level enumeration (paths=None) ---

    def test_enumerate_reads_files(self):
        result = read_kernel_attrs(str(self.ib0))
        assert result["operstate"] == "up"
        assert result["carrier"] == "1"
        assert result["mtu"] == "2044"
        assert result["type"] == "32"

    def test_enumerate_directories_as_paths(self):
        result = read_kernel_attrs(str(self.ib0))
        assert isinstance(result["statistics"], Path)
        assert isinstance(result["device"], Path)
        assert result["statistics"].name == "statistics"
        assert result["device"].name == "device"

    def test_enumerate_does_not_recurse(self):
        result = read_kernel_attrs(str(self.ib0))
        # statistics is a Path, not the actual counter values
        assert "rx_bytes" not in result
        assert "rx_errors" not in result

    def test_enumerate_ib_port(self):
        result = read_kernel_attrs(str(self.port))
        assert result["state"] == "4: ACTIVE"
        assert result["phys_state"] == "5: LinkUp"
        assert result["rate"] == "400 Gb/sec (4X NDR)"
        assert isinstance(result["counters"], Path)
        assert isinstance(result["hw_counters"], Path)

    # --- Selective paths ---

    def test_selective_single_file(self):
        result = read_kernel_attrs(str(self.ib0), ["operstate"])
        assert result == {"operstate": "up"}

    def test_selective_multiple_files(self):
        result = read_kernel_attrs(str(self.ib0), ["operstate", "carrier", "mtu"])
        assert result["operstate"] == "up"
        assert result["carrier"] == "1"
        assert result["mtu"] == "2044"

    def test_selective_nested_path(self):
        result = read_kernel_attrs(str(self.ib0), ["statistics/rx_errors"])
        assert result == {"statistics": {"rx_errors": "0"}}

    def test_selective_multiple_nested(self):
        result = read_kernel_attrs(str(self.ib0), [
            "statistics/rx_errors", "statistics/tx_errors", "statistics/rx_bytes"
        ])
        assert result["statistics"]["rx_errors"] == "0"
        assert result["statistics"]["tx_errors"] == "0"
        assert result["statistics"]["rx_bytes"] == "123456789"

    def test_selective_mixed_flat_and_nested(self):
        result = read_kernel_attrs(str(self.ib0), [
            "operstate", "mtu", "statistics/rx_errors"
        ])
        assert result["operstate"] == "up"
        assert result["mtu"] == "2044"
        assert result["statistics"]["rx_errors"] == "0"

    def test_selective_ib_counters(self):
        result = read_kernel_attrs(str(self.port), [
            "state", "phys_state", "counters/symbol_error",
            "counters/link_downed", "hw_counters/duplicate_request"
        ])
        assert result["state"] == "4: ACTIVE"
        assert result["phys_state"] == "5: LinkUp"
        assert result["counters"]["symbol_error"] == "0"
        assert result["counters"]["link_downed"] == "0"
        assert result["hw_counters"]["duplicate_request"] == "0"

    def test_selective_deep_nested(self):
        result = read_kernel_attrs(str(self.port), ["gid_attrs/ndevs/0"])
        assert result["gid_attrs"]["ndevs"]["0"] == "ib0"

    # --- Missing / nonexistent paths ---

    def test_nonexistent_root(self):
        result = read_kernel_attrs("/nonexistent/path/that/does/not/exist")
        assert result == {}

    def test_selective_missing_file_skipped(self):
        result = read_kernel_attrs(str(self.ib0), ["operstate", "nonexistent_file"])
        assert result == {"operstate": "up"}

    def test_selective_all_missing(self):
        result = read_kernel_attrs(str(self.ib0), ["no_such_file", "also_missing"])
        assert result == {}

    def test_selective_missing_nested_skipped(self):
        result = read_kernel_attrs(str(self.ib0), [
            "operstate", "nonexistent_dir/some_file"
        ])
        assert result == {"operstate": "up"}

    # --- Edge cases ---

    def test_empty_paths_list(self):
        result = read_kernel_attrs(str(self.ib0), [])
        assert result == {}

    def test_whitespace_stripped(self):
        result = read_kernel_attrs(str(self.ib0), ["operstate"])
        # File contains "up\n", should be stripped to "up"
        assert result["operstate"] == "up"

    def test_large_counter_value(self):
        result = read_kernel_attrs(str(self.port), ["counters/port_rcv_data"])
        assert result["counters"]["port_rcv_data"] == "58742985234"

    def test_enumerate_empty_directory(self, tmp_path):
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        result = read_kernel_attrs(str(empty))
        assert result == {}

    def test_symlink_to_directory_returns_resolved_path(self, tmp_path):
        """Symlinks pointing to directories should return resolved Path."""
        target = tmp_path / "real_dir"
        target.mkdir()
        (target / "some_file").write_text("data\n")
        link = tmp_path / "test_root" 
        link.mkdir()
        sym = link / "linked_dir"
        sym.symlink_to(target)
        result = read_kernel_attrs(str(link))
        assert isinstance(result["linked_dir"], Path)
        assert result["linked_dir"] == target
        # Can be used for follow-up read
        follow_up = read_kernel_attrs(str(result["linked_dir"]))
        assert follow_up["some_file"] == "data"

    def test_symlink_to_file_read(self, tmp_path):
        """Symlinks pointing to files should be read normally as strings."""
        target = tmp_path / "real_file"
        target.write_text("42\n")
        link_root = tmp_path / "test_root"
        link_root.mkdir()
        sym = link_root / "linked_file"
        sym.symlink_to(target)
        result = read_kernel_attrs(str(link_root))
        assert result["linked_file"] == "42"
        assert isinstance(result["linked_file"], str)

    def test_enumerate_directory_path_is_resolved(self):
        """Directory Path values should be absolute and resolved."""
        result = read_kernel_attrs(str(self.ib0))
        stats_path = result["statistics"]
        assert stats_path.is_absolute()
        assert stats_path.is_dir()

    def test_enumerate_path_usable_for_followup_read(self):
        """Path returned for a directory can be passed to read_kernel_attrs."""
        result = read_kernel_attrs(str(self.ib0))
        stats_path = result["statistics"]
        stats = read_kernel_attrs(str(stats_path))
        assert stats["rx_bytes"] == "123456789"
        assert stats["rx_errors"] == "0"

    def test_binary_file_skipped_enumerate(self, tmp_path):
        """Binary files (containing null bytes) should be skipped in enumeration."""
        root = tmp_path / "binary_test"
        root.mkdir()
        (root / "text_file").write_text("hello\n")
        (root / "binary_file").write_bytes(b"\x7fELF\x00\x01\x02\x03")
        result = read_kernel_attrs(str(root))
        assert result["text_file"] == "hello"
        assert "binary_file" not in result

    def test_binary_file_skipped_selective(self, tmp_path):
        """Binary files should be skipped in selective mode too."""
        root = tmp_path / "binary_test2"
        root.mkdir()
        (root / "text_file").write_text("42\n")
        (root / "binary_file").write_bytes(b"\x00" * 16)
        result = read_kernel_attrs(str(root), ["text_file", "binary_file"])
        assert result == {"text_file": "42"}

    def test_selective_directory_returns_path(self):
        """Selective mode should return Path for directories, same as enumeration."""
        result = read_kernel_attrs(str(self.ib0), ["statistics"])
        assert isinstance(result["statistics"], Path)
        assert result["statistics"].name == "statistics"
        assert result["statistics"].is_dir()

    def test_selective_nested_directory_returns_path(self):
        """Selective mode with nested directory path returns Path in nested dict."""
        # gid_attrs/ndevs is a directory containing file "0"
        result = read_kernel_attrs(str(self.port), ["gid_attrs/ndevs"])
        assert isinstance(result["gid_attrs"]["ndevs"], Path)

    def test_selective_mix_files_and_directories(self):
        """Selective mode handles files and directories in the same call."""
        result = read_kernel_attrs(str(self.ib0), ["operstate", "statistics", "mtu"])
        assert result["operstate"] == "up"
        assert result["mtu"] == "2044"
        assert isinstance(result["statistics"], Path)

    def test_selective_directory_path_usable_for_followup(self):
        """Path returned for directory in selective mode can be used for follow-up read."""
        result = read_kernel_attrs(str(self.ib0), ["statistics"])
        stats = read_kernel_attrs(result["statistics"])
        assert stats["rx_errors"] == "0"
        assert stats["rx_bytes"] == "123456789"

    def test_selective_dir_and_nested_file_coexist(self):
        """Requesting both a directory and a nested file under it should not crash."""
        result = read_kernel_attrs(str(self.ib0), ["statistics", "statistics/rx_errors"])
        # The nested file should be accessible
        assert result["statistics"]["rx_errors"] == "0"

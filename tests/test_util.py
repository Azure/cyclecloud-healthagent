from healthagent.util import evaluate, read_kernel_attrs
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

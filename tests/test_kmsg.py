from datetime import datetime, timedelta

import pytest

from healthagent.config import KmsgConfig, KmsgPatternCheck
from healthagent.kmsg import KmsgReader, msgbucket, SAMPLES_CAP
from healthagent.reporter import HealthStatus


class _Reader(KmsgReader):
    """KmsgReader without the /dev/kmsg + event-loop plumbing.

    Reuses the real _build_buckets/_ingest/_build_report/_format_details logic.
    """

    def __init__(self, config: KmsgConfig):
        self.config = config
        self.buckets = self._build_buckets()


def _config():
    return KmsgConfig(patterns={
        "nvme_io_timeout": KmsgPatternCheck(
            pattern=r"nvme nvme\d+: I/O tag .* QID \d+ timeout",
            warning=3, category="Storage", msg="Repeated NVMe I/O timeouts"),
        "ctrl_dead": KmsgPatternCheck(
            pattern="controller is down", error=1, category="Storage"),
    })


T0 = datetime(2026, 8, 11, 10, 0, 0)
TIMEOUT_LINE = "nvme nvme0: I/O tag 12 QID 4 timeout, aborting"


def _feed(reader, seconds, level, msg):
    reader._ingest(T0 + timedelta(seconds=seconds), level, msg)


# ── bucket construction ─────────────────────────────────────

class TestBuildBuckets:

    def test_reserved_and_pattern_buckets_present(self):
        reader = _Reader(_config())
        for name in ("nvme_io_timeout", "ctrl_dead",
                     "KERNEL_EMERGENCY", "KERNEL_ALERT", "KERNEL_CRITICAL"):
            assert name in reader.buckets

    def test_reserved_buckets_have_no_pattern(self):
        reader = _Reader(_config())
        assert reader.buckets["KERNEL_CRITICAL"].pattern_eval is None
        assert reader.buckets["nvme_io_timeout"].pattern_eval is not None


# ── matching / thresholds ───────────────────────────────────

class TestMatching:

    def test_below_threshold_is_ok(self):
        reader = _Reader(_config())
        for i in range(2):  # warning threshold is 3
            _feed(reader, i, 4, TIMEOUT_LINE)
        report = reader._build_report()
        assert report.status == HealthStatus.OK
        assert report.custom_fields == {}

    def test_warning_threshold_triggers(self):
        reader = _Reader(_config())
        for i in range(3):
            _feed(reader, i, 4, TIMEOUT_LINE)
        report = reader._build_report()
        assert report.status == HealthStatus.WARNING
        entry = report.custom_fields["nvme_io_timeout"]
        assert entry["severity"] == "Warning"
        assert entry["count"] == ">=3"

    def test_reserved_level_promoted_to_error(self):
        reader = _Reader(_config())
        _feed(reader, 0, 2, "EXT4-fs (sda1): Remounting filesystem read-only")
        report = reader._build_report()
        assert report.status == HealthStatus.ERROR
        entry = report.custom_fields["KERNEL_CRITICAL"]
        assert entry["severity"] == "Error"
        assert entry["count"] == ">=1"
        assert "pattern" not in entry  # reserved buckets aren't user rules

    def test_level_above_two_not_reserved(self):
        """A level-3 line with no matching pattern leaves the report OK."""
        reader = _Reader(_config())
        _feed(reader, 0, 3, "some unremarkable kernel error line")
        report = reader._build_report()
        assert report.status == HealthStatus.OK
        assert report.custom_fields == {}

    def test_error_pattern_on_first_hit(self):
        reader = _Reader(_config())
        _feed(reader, 0, 3, "nvme nvme0: controller is down; will reset")
        report = reader._build_report()
        assert report.custom_fields["ctrl_dead"]["severity"] == "Error"

    def test_max_severity_across_buckets(self):
        reader = _Reader(_config())
        for i in range(3):
            _feed(reader, i, 4, TIMEOUT_LINE)                                    # Warning
        _feed(reader, 10, 2, "EXT4-fs (sda1): Remounting filesystem read-only")  # Error
        assert reader._build_report().status == HealthStatus.ERROR


# ── samples / anti-churn ────────────────────────────────────

class TestSamplesAndChurn:

    def test_first_five_samples_and_first_seen(self):
        reader = _Reader(_config())
        for i in range(8):
            _feed(reader, i, 4, f"nvme nvme0: I/O tag {i} QID 4 timeout, aborting")
        bucket = reader.buckets["nvme_io_timeout"]
        assert len(bucket.samples) == SAMPLES_CAP
        # first-seen line retained, not evicted by later matches
        assert "tag 0 " in bucket.samples[0]
        assert bucket.first == T0

    def test_report_stable_under_flood(self):
        reader = _Reader(_config())
        # Feed enough to fill the 5-sample cap so the bucket is fully settled.
        for i in range(5):
            _feed(reader, i, 4, TIMEOUT_LINE)
        rep1 = reader._build_report()
        for i in range(50):
            _feed(reader, 100 + i, 4, TIMEOUT_LINE)
        rep2 = reader._build_report()
        assert rep1 == rep2
        assert reader.buckets["nvme_io_timeout"].count == 55
        assert rep2.custom_fields["nvme_io_timeout"]["count"] == ">=3"


# ── report shape ────────────────────────────────────────────

class TestReportShape:

    def test_healthy_report_is_ok(self):
        reader = _Reader(_config())
        report = reader._build_report()
        assert report.status == HealthStatus.OK
        assert report.message is None
        assert report.details is None

    def test_custom_fields_carry_pattern_and_category(self):
        reader = _Reader(_config())
        for i in range(3):
            _feed(reader, i, 4, TIMEOUT_LINE)
        entry = reader._build_report().custom_fields["nvme_io_timeout"]
        assert entry["pattern"] == r"nvme nvme\d+: I/O tag .* QID \d+ timeout"
        assert entry["category"] == "Storage"

    def test_details_groups_and_uses_configured_msg(self):
        reader = _Reader(_config())
        for i in range(3):
            _feed(reader, i, 4, TIMEOUT_LINE)
        _feed(reader, 10, 2, "EXT4-fs (sda1): Remounting filesystem read-only")
        details = reader._build_report().details
        assert "=== Errors ===" in details
        assert "=== Warnings ===" in details
        assert "Repeated NVMe I/O timeouts" in details  # configured msg

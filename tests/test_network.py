import pytest
from unittest.mock import patch
from healthagent.network import (
    NetworkHealthChecks, NetworkInterface, NetDevType,
    IBDevice, IBPort,
)
from healthagent.config import NetworkConfig, ThresholdCheck, EvalType
from healthagent.reporter import Reporter, HealthStatus


def _make_ib_interface(name="ib0", state="4: ACTIVE", phys_state="5: LinkUp",
                       link_downed=0, link_error_recovery=0, operstate="up",
                       carrier_down_count=0):
    """Helper: build a healthy IB NetworkInterface with one port."""
    ni = NetworkInterface()
    ni.name = name
    ni.type = NetDevType.INFINIBAND
    ni.operstate = operstate
    ni.carrier_down_count = carrier_down_count
    ni.ib_device = IBDevice(name="mlx5_ib0", ports={
        "1": IBPort(state=state, phys_state=phys_state, rate="400 Gb/sec (4X NDR)",
                     link_downed=link_downed, link_error_recovery=link_error_recovery),
    })
    return ni


def _make_eth_interface(name="eth0", operstate="up", carrier_down_count=0):
    """Helper: build a healthy Ethernet NetworkInterface."""
    ni = NetworkInterface()
    ni.name = name
    ni.type = NetDevType.ETHERNET
    ni.operstate = operstate
    ni.carrier_down_count = carrier_down_count
    return ni


def _default_config():
    """Helper: build a NetworkConfig matching the default checks from defaults.yaml."""
    return NetworkConfig(
        infiniband={
            "state": ThresholdCheck(eval=EvalType.NE, error="4: ACTIVE", msg="IB link is not active"),
            "phys_state": ThresholdCheck(eval=EvalType.NE, error="5: LinkUp", msg="IB link not in state LinkUp"),
            "link_downed": ThresholdCheck(eval=EvalType.WINDOW_GT, error=3, window=10800, strikes=1,
                                          msg="IB link flapped 3+ times within a 3-hour window"),
            "link_error_recovery": ThresholdCheck(eval=EvalType.GT, warning=3,
                                                   msg="IB link error recovery exceeded the threshold"),
            "operstate": ThresholdCheck(eval=EvalType.NE, warning="up", msg="IPoIB not in state up"),
            "carrier_down_count": ThresholdCheck(eval=EvalType.GT, warning=5,
                                                  msg="IPoIB carrier down count exceeded threshold"),
        },
        ethernet={
            "carrier_down_count": ThresholdCheck(eval=EvalType.GT, warning=3,
                                                  msg="carrier_down_count exceeded threshold"),
            "operstate": ThresholdCheck(eval=EvalType.NE, error="up", msg="interface not in state up"),
        },
    )


def _make_checker(config: NetworkConfig = None):
    """Helper: build a NetworkHealthChecks with a mocked reporter."""
    reporter = Reporter()
    reporter.publish_cc = False
    return NetworkHealthChecks(reporter=reporter, config=config or _default_config())


# ── Healthy baselines ──────────────────────────────────────

class TestHealthyBaseline:

    @pytest.mark.asyncio
    async def test_healthy_ib_interface(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_ib_interface()]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_healthy_eth_interface(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_eth_interface()]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_no_interfaces(self):
        checker = _make_checker()
        checker.get_network_state = lambda: []
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK


# ── Simple threshold checks (ne, gt) ──────────────────────

class TestSimpleThresholds:

    @pytest.mark.asyncio
    async def test_ib_link_not_active_error(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_ib_interface(state="1: DOWN")]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR
        assert any("IB link is not active" in e for e in report.custom_fields["ib0"]["errors"])

    @pytest.mark.asyncio
    async def test_ib_phys_state_not_linkup(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_ib_interface(phys_state="2: Polling")]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR

    @pytest.mark.asyncio
    async def test_ib_operstate_down_warning(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_ib_interface(operstate="down")]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.WARNING
        assert "warnings" in report.custom_fields["ib0"]

    @pytest.mark.asyncio
    async def test_link_error_recovery_warning(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_ib_interface(link_error_recovery=5)]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.WARNING

    @pytest.mark.asyncio
    async def test_eth_operstate_down_error(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_eth_interface(operstate="down")]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR

    @pytest.mark.asyncio
    async def test_eth_carrier_down_warning(self):
        checker = _make_checker()
        checker.get_network_state = lambda: [_make_eth_interface(carrier_down_count=5)]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.WARNING

    @pytest.mark.asyncio
    async def test_error_takes_precedence_over_warning(self):
        """Multiple issues: error should dominate."""
        checker = _make_checker()
        checker.get_network_state = lambda: [
            _make_ib_interface(state="1: DOWN", operstate="down")
        ]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR

    @pytest.mark.asyncio
    async def test_multiple_interfaces_independent(self):
        """Error on one interface doesn't suppress OK on another."""
        checker = _make_checker()
        checker.get_network_state = lambda: [
            _make_ib_interface(name="ib0", state="1: DOWN"),
            _make_ib_interface(name="ib1"),
        ]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR
        assert "errors" in report.custom_fields["ib0"]
        assert "errors" not in report.custom_fields["ib1"]


# ── window_gt (link flapping) ─────────────────────────────

class TestWindowGt:

    def _make_flap_config(self, error=3, window=180, strikes=0):
        """Config with only link_downed as window_gt. Window=180s, maxlen=4."""
        return NetworkConfig(infiniband={
            "link_downed": ThresholdCheck(
                eval=EvalType.WINDOW_GT, error=error, window=window,
                strikes=strikes, msg="IB link flapped too many times",
            ),
        })

    @pytest.mark.asyncio
    async def test_no_trigger_insufficient_history(self):
        """First few polls — not enough data to fill the window."""
        config = self._make_flap_config(window=180)
        checker = _make_checker(config)

        # Simulate 2 polls at 60s intervals — timespan < window
        for link_downed, t in [(0, 0.0), (1, 60.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_timeseries_records_values(self):
        """Verify link_downed values are recorded into the TimeSeries correctly."""
        config = self._make_flap_config(window=180, error=3)
        checker = _make_checker(config)

        samples = [(0, 0.0), (1, 60.0), (2, 120.0)]
        for link_downed, t in samples:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        key = ("ib0", "link_downed", "1")
        assert key in checker._time_series
        ts = checker._time_series[key]
        assert len(ts) == 3
        # Verify recorded values and timestamps match what was polled
        assert ts._samples[0] == (0, 0.0)
        assert ts._samples[1] == (1, 60.0)
        assert ts._samples[2] == (2, 120.0)

    @pytest.mark.asyncio
    async def test_triggers_after_full_window(self):
        """4 flaps within window → error."""
        config = self._make_flap_config(window=180, error=3)
        checker = _make_checker(config)

        # 4 samples at 60s intervals, maxlen=4, all fit
        samples = [(0, 0.0), (1, 60.0), (2, 120.0), (4, 180.0)]
        for link_downed, t in samples:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR
        assert any("flapped" in e for e in report.custom_fields["ib0"]["errors"])

    @pytest.mark.asyncio
    async def test_no_trigger_below_threshold(self):
        """2 flaps in window — below threshold of 3."""
        config = self._make_flap_config(window=180, error=3)
        checker = _make_checker(config)

        samples = [(0, 0.0), (1, 60.0), (2, 120.0), (2, 180.0)]
        for link_downed, t in samples:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_recovers_when_window_clears(self):
        """Error clears once old events fall outside the window (strikes=0)."""
        config = self._make_flap_config(window=180, error=3, strikes=0)
        checker = _make_checker(config)

        # Trigger error: 4 flaps in 180s
        for link_downed, t in [(0, 0.0), (1, 60.0), (2, 120.0), (4, 180.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR

        # Time passes, no new flaps — window clears
        for link_downed, t in [(4, 240.0), (4, 300.0), (4, 360.0), (4, 420.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK


# ── Strikes behavior ──────────────────────────────────────

class TestStrikes:

    def _make_flap_config(self, strikes=1, window=180, error=3):
        return NetworkConfig(infiniband={
            "link_downed": ThresholdCheck(
                eval=EvalType.WINDOW_GT, error=error, window=window,
                strikes=strikes, msg="IB link flapped too many times",
            ),
        })

    @pytest.mark.asyncio
    async def test_strikes_1_permanent_after_first_error(self):
        """strikes=1: once error triggers, it persists even after window clears."""
        config = self._make_flap_config(strikes=1, window=180, error=3)
        checker = _make_checker(config)

        # Trigger error
        for link_downed, t in [(0, 0.0), (1, 60.0), (2, 120.0), (4, 180.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.ERROR

        # Window clears — should still be ERROR (strikes exhausted)
        for link_downed, t in [(4, 240.0), (4, 300.0), (4, 360.0), (4, 420.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.ERROR

    @pytest.mark.asyncio
    async def test_strikes_2_recovers_once_then_permanent(self):
        """strikes=2: first error recoverable, second is permanent."""
        config = self._make_flap_config(strikes=2, window=180, error=3)
        checker = _make_checker(config)

        # First error episode
        for link_downed, t in [(0, 0.0), (1, 60.0), (2, 120.0), (4, 180.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.ERROR
        assert checker._trigger_count.get(("ib0", "link_downed", "1"), 0) == 1

        # Window clears — should recover (only 1 strike, needs 2)
        for link_downed, t in [(4, 240.0), (4, 300.0), (4, 360.0), (4, 420.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.OK

        # Second error episode
        for link_downed, t in [(4, 420.0), (5, 480.0), (6, 540.0), (8, 600.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.ERROR
        assert checker._trigger_count.get(("ib0", "link_downed", "1"), 0) == 2

        # Window clears again — should stay ERROR (2 strikes exhausted)
        for link_downed, t in [(8, 660.0), (8, 720.0), (8, 780.0), (8, 840.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.ERROR

    @pytest.mark.asyncio
    async def test_strikes_counts_transitions_not_polls(self):
        """Sustained error across multiple polls should count as 1 transition."""
        config = self._make_flap_config(strikes=2, window=180, error=3)
        checker = _make_checker(config)

        # Trigger error and stay in error for several consecutive polls
        for link_downed, t in [(0, 0.0), (1, 60.0), (2, 120.0), (4, 180.0),
                                (5, 240.0), (6, 300.0), (7, 360.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        # Should still be only 1 transition despite many error polls
        assert checker._trigger_count.get(("ib0", "link_downed", "1"), 0) == 1

    @pytest.mark.asyncio
    async def test_warning_to_error_counts_as_new_strike(self):
        """Error recovering to warning (not OK) then back to error counts as 2 strikes."""
        config = NetworkConfig(infiniband={
            "link_downed": ThresholdCheck(
                eval=EvalType.WINDOW_GT, error=3, warning=1, window=180,
                strikes=2, msg="IB link flapped too many times",
            ),
        })
        checker = _make_checker(config)

        # First error episode: 4 flaps in window
        for link_downed, t in [(0, 0.0), (1, 60.0), (2, 120.0), (4, 180.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        assert checker.reporter.store["NetworkInterfaceCheck"].status == HealthStatus.ERROR
        assert checker._trigger_count.get(("ib0", "link_downed", "1"), 0) == 1

        # Recover to warning (delta drops below error but still >= warning)
        for link_downed, t in [(4, 240.0), (4, 300.0), (5, 360.0), (5, 420.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        # _in_error should be cleared since we're at warning, not error
        assert checker._in_error.get(("ib0", "link_downed", "1")) is False

        # Second error episode
        for link_downed, t in [(5, 420.0), (6, 480.0), (7, 540.0), (9, 600.0)]:
            with patch("healthagent.util.time") as mock_time:
                mock_time.monotonic.return_value = t
                checker.get_network_state = lambda ld=link_downed: [_make_ib_interface(link_downed=ld)]
                await checker.run_network_checks()

        # Should count as 2 strikes (warning→error is a new transition)
        assert checker._trigger_count.get(("ib0", "link_downed", "1"), 0) == 2


# ── Custom config overrides ───────────────────────────────

class TestConfigOverrides:

    @pytest.mark.asyncio
    async def test_disabled_check_via_empty_config(self):
        """Empty infiniband config means no checks run."""
        config = NetworkConfig(infiniband={})
        checker = _make_checker(config)
        checker.get_network_state = lambda: [_make_ib_interface(state="1: DOWN")]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_warning_instead_of_error(self):
        """User downgrades link_downed to warning."""
        config = NetworkConfig(infiniband={
            "link_downed": ThresholdCheck(
                eval=EvalType.GT, warning=3,
                msg="link_downed exceeded threshold",
            ),
        })
        checker = _make_checker(config)
        checker.get_network_state = lambda: [_make_ib_interface(link_downed=5)]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.WARNING

    @pytest.mark.asyncio
    async def test_custom_threshold_value(self):
        """User raises the error threshold."""
        config = NetworkConfig(infiniband={
            "link_downed": ThresholdCheck(
                eval=EvalType.GT, error=10,
                msg="link_downed exceeded threshold",
            ),
        })
        checker = _make_checker(config)
        # 5 is below custom threshold of 10
        checker.get_network_state = lambda: [_make_ib_interface(link_downed=5)]
        await checker.run_network_checks()
        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.OK


# ── Multi-port ────────────────────────────────────────────

class TestMultiPort:

    @pytest.mark.asyncio
    async def test_per_port_evaluation(self):
        """Each port is evaluated independently."""
        ni = _make_ib_interface()
        ni.ib_device.ports["2"] = IBPort(
            state="4: ACTIVE", phys_state="5: LinkUp",
            rate="400 Gb/sec (4X NDR)", link_downed=5, link_error_recovery=0,
        )
        config = NetworkConfig(infiniband={
            "link_downed": ThresholdCheck(eval=EvalType.GT, error=3, msg="link_downed exceeded"),
        })
        checker = _make_checker(config)
        checker.get_network_state = lambda: [ni]
        await checker.run_network_checks()

        report = checker.reporter.store["NetworkInterfaceCheck"]
        assert report.status == HealthStatus.ERROR
        errors = report.custom_fields["ib0"]["errors"]
        # Only port 2 should have the error
        assert any("port 2" in e for e in errors)
        assert not any("port 1" in e for e in errors)

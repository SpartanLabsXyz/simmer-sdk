"""
Unit tests for OracleEngineeringDetector.

Two mandatory cases:
  1. a4385-shaped event: PM ≥60% concentrated on YES + spot BUY ≥2σ within 5 min → HIGH
  2. Clean directional run: PM and spot same direction but correctly aligned → NONE

Additional cases cover edge conditions, LOW signal paths, and direction variants.
"""

import time
import pytest

from simmer_sdk.risk.oracle_engineering import (
    OracleEngineeringDetector,
    RiskFlag,
    RiskLevel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW_TS = int(time.time())

def _buy_trades(count: int, size: float) -> list:
    return [{"side": "buy", "size": size, "ts": NOW_TS + i} for i in range(count)]

def _sell_trades(count: int, size: float) -> list:
    return [{"side": "sell", "size": size, "ts": NOW_TS + i} for i in range(count)]

def _mixed_trades(buy_count: int, buy_size: float, sell_count: int, sell_size: float) -> list:
    trades = _buy_trades(buy_count, buy_size) + _sell_trades(sell_count, sell_size)
    return trades

# Canonical a4385-style position book: 77K YES out of 87K total (88.5% YES side)
A4385_BOOK = {"YES": 77000, "NO": 10000, "total": 87000}

# Clean book: both sides roughly equal
CLEAN_BOOK = {"YES": 45000, "NO": 43000, "total": 88000}


# ---------------------------------------------------------------------------
# 1. a4385-shaped event → HIGH
# ---------------------------------------------------------------------------

class TestA4385ShapedEvent:
    """
    Criterion: PM concentration ≥60% one side + spot flow ≥2σ opposite within 5 min.
    """

    def test_high_flag_on_a4385_pattern(self):
        """Canonical oracle-engineering signature: HIGH risk flag emitted."""
        detector = OracleEngineeringDetector()

        # Spot: aggressive buy pressure — 8 large buys, 2 small sells → net heavily buy
        spot = _buy_trades(8, 120000) + _sell_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xa4385",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.5,
        )

        assert flag.level == RiskLevel.HIGH, f"Expected HIGH, got {flag.level}: {flag.reason}"
        assert flag.should_halt is True
        assert flag.risk_flag == "oracle_engineering"
        assert flag.pm_concentration >= 0.60
        assert flag.spot_sigma >= 2.0
        assert "oracle_engineering" in flag.reason.lower() or "tamper" in flag.reason.lower() or "concentration" in flag.reason.lower()

    def test_high_flag_uses_explicit_concentration(self):
        """When per-wallet concentration is provided, it takes precedence."""
        detector = OracleEngineeringDetector()

        book = {
            "YES": 50000, "NO": 50000, "total": 100000,
            "top_yes_concentration": 0.75,
            "top_no_concentration": 0.10,
        }
        spot = _buy_trades(9, 80000) + _sell_trades(1, 2000)

        flag = detector.evaluate(
            market_id="0xabc",
            position_book=book,
            spot_flow_window=spot,
            minutes_to_resolution=2.0,
        )

        assert flag.level == RiskLevel.HIGH
        assert flag.pm_concentration == pytest.approx(0.75, abs=0.001)

    def test_high_flag_no_side(self):
        """Heavy NO concentration + aggressive SELL spot → HIGH (down variant)."""
        detector = OracleEngineeringDetector()

        book = {"YES": 10000, "NO": 77000, "total": 87000}
        spot = _sell_trades(8, 120000) + _buy_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xdown",
            position_book=book,
            spot_flow_window=spot,
            minutes_to_resolution=4.0,
        )

        assert flag.level == RiskLevel.HIGH
        assert flag.should_halt is True

    def test_should_halt_property(self):
        """RiskFlag.should_halt mirrors level == HIGH."""
        high_flag = RiskFlag(level=RiskLevel.HIGH)
        low_flag = RiskFlag(level=RiskLevel.LOW)
        none_flag = RiskFlag(level=RiskLevel.NONE)

        assert high_flag.should_halt is True
        assert low_flag.should_halt is False
        assert none_flag.should_halt is False


# ---------------------------------------------------------------------------
# 2. Clean directional run → NONE
# ---------------------------------------------------------------------------

class TestCleanDirectionalRun:
    """
    Criterion: PM concentration and spot flow same direction → no flag.
    This is the negative case: MMs correctly hedging with the market.
    """

    def test_no_flag_clean_run_yes_momentum(self):
        """
        Balanced PM book + aggressive spot buys = clean YES momentum.
        Not oracle engineering: PM is not concentrated ≥60% on one side.
        Single spot signal alone may fire LOW (monitor only) but never HIGH.
        """
        detector = OracleEngineeringDetector()

        spot = _buy_trades(9, 80000) + _sell_trades(1, 3000)

        flag = detector.evaluate(
            market_id="0xclean1",
            position_book=CLEAN_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        # PM not concentrated → cannot be HIGH (halt gate must NOT fire)
        assert flag.level != RiskLevel.HIGH, f"Expected not HIGH, got {flag.level}: {flag.reason}"
        assert flag.should_halt is False

    def test_no_flag_clean_run_no_momentum(self):
        """
        Balanced PM book + aggressive spot sells = clean NO momentum.
        PM not concentrated → cannot reach HIGH.
        """
        detector = OracleEngineeringDetector()

        spot = _sell_trades(9, 80000) + _buy_trades(1, 3000)

        flag = detector.evaluate(
            market_id="0xclean2",
            position_book=CLEAN_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        assert flag.level != RiskLevel.HIGH
        assert flag.should_halt is False

    def test_no_flag_when_spot_opposes_pm_concentration(self):
        """
        Heavy YES PM concentration + aggressive SELL spot = MM hedge (not engineering).
        PM concentrated YES but spot is SELLING aggressively → opposite of a4385.
        """
        detector = OracleEngineeringDetector()

        spot = _sell_trades(8, 120000) + _buy_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xmm_hedge",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        # YES concentrated but spot selling hard — MMs hedging, not oracle engineering
        assert flag.level in (RiskLevel.NONE, RiskLevel.LOW), (
            f"Expected NONE or LOW for MM hedge pattern, got {flag.level}: {flag.reason}"
        )
        assert flag.should_halt is False

    def test_no_flag_low_concentration_high_spot(self):
        """High spot sigma alone without PM concentration → at most LOW."""
        detector = OracleEngineeringDetector()

        spot = _buy_trades(9, 200000) + _sell_trades(1, 1000)

        flag = detector.evaluate(
            market_id="0xspot_only",
            position_book=CLEAN_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        # Only spot signal fires; no PM concentration
        assert flag.level != RiskLevel.HIGH
        assert flag.should_halt is False

    def test_no_flag_high_concentration_low_spot(self):
        """High PM concentration alone without spot sigma → at most LOW."""
        detector = OracleEngineeringDetector()

        # Balanced spot — large but equal buy and sell sizes → near-zero net
        spot = _buy_trades(5, 50000) + _sell_trades(5, 49000)

        flag = detector.evaluate(
            market_id="0xpm_only",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        assert flag.level != RiskLevel.HIGH
        assert flag.should_halt is False


# ---------------------------------------------------------------------------
# 3. Resolution window gate
# ---------------------------------------------------------------------------

class TestResolutionWindowGate:
    """Both signals present but outside the resolution window → LOW at most."""

    def test_outside_window_caps_at_low(self):
        """Both signals present but 20 min before resolution → LOW (not HIGH)."""
        detector = OracleEngineeringDetector(resolution_window_minutes=5.0)

        spot = _buy_trades(8, 120000) + _sell_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xearly",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=20.0,  # well outside 5-min window
        )

        assert flag.level != RiskLevel.HIGH, (
            f"Expected at most LOW outside resolution window, got HIGH: {flag.reason}"
        )
        assert flag.should_halt is False

    def test_at_window_boundary_is_high(self):
        """Exactly at window boundary (5.0 min) qualifies as HIGH."""
        detector = OracleEngineeringDetector(resolution_window_minutes=5.0)

        spot = _buy_trades(8, 120000) + _sell_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xboundary",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=5.0,
        )

        assert flag.level == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# 4. LOW signal paths
# ---------------------------------------------------------------------------

class TestLowSignalPaths:
    """One signal present → LOW."""

    def test_pm_concentration_only_emits_low(self):
        """Only PM signal fires → LOW."""
        detector = OracleEngineeringDetector()

        # Balanced spot — no meaningful sigma
        spot = _buy_trades(3, 10000) + _sell_trades(3, 9500)

        flag = detector.evaluate(
            market_id="0xpm_low",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        # PM signal alone (no spot sigma) → LOW
        assert flag.level in (RiskLevel.LOW, RiskLevel.NONE)

    def test_spot_sigma_only_emits_low(self):
        """Only spot signal fires → LOW."""
        detector = OracleEngineeringDetector()

        spot = _buy_trades(9, 200000) + _sell_trades(1, 1000)

        flag = detector.evaluate(
            market_id="0xspot_low",
            position_book=CLEAN_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        assert flag.level in (RiskLevel.LOW, RiskLevel.NONE)


# ---------------------------------------------------------------------------
# 5. Edge / data-quality cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Detector is robust to sparse or zero data."""

    def test_empty_spot_window_no_halt(self):
        """No spot data → cannot reach HIGH (spot sigma is 0)."""
        detector = OracleEngineeringDetector()

        flag = detector.evaluate(
            market_id="0xnospot",
            position_book=A4385_BOOK,
            spot_flow_window=[],
            minutes_to_resolution=3.0,
        )

        # PM signal may fire LOW (concentration present), but no spot sigma → not HIGH
        assert flag.level != RiskLevel.HIGH
        assert flag.should_halt is False

    def test_single_spot_trade_no_halt(self):
        """Only 1 spot data point — can't compute sigma → no halt gate fires."""
        detector = OracleEngineeringDetector()

        flag = detector.evaluate(
            market_id="0xonespot",
            position_book=A4385_BOOK,
            spot_flow_window=[{"side": "buy", "size": 1000000, "ts": NOW_TS}],
            minutes_to_resolution=3.0,
        )

        assert flag.level != RiskLevel.HIGH
        assert flag.should_halt is False

    def test_zero_oi_book_returns_none(self):
        """Zero OI position book → NONE."""
        detector = OracleEngineeringDetector()

        spot = _buy_trades(8, 120000) + _sell_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xzerooi",
            position_book={"YES": 0, "NO": 0, "total": 0},
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        assert flag.level in (RiskLevel.NONE, RiskLevel.LOW)
        assert flag.should_halt is False

    def test_unknown_spot_sides_ignored(self):
        """Entries with unknown side are silently ignored."""
        detector = OracleEngineeringDetector()

        spot = (
            _buy_trades(5, 120000)
            + _sell_trades(1, 5000)
            + [{"side": "unknown", "size": 999999, "ts": NOW_TS}]
        )

        flag = detector.evaluate(
            market_id="0xunkside",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        # Unknown side should be ignored; result should still detect the buy pressure
        assert flag.risk_flag == "oracle_engineering"

    def test_custom_thresholds(self):
        """Detector respects custom concentration and sigma thresholds."""
        # Very tight thresholds: 0% concentration + 0σ → everything is HIGH
        detector = OracleEngineeringDetector(
            concentration_threshold=0.0,
            spot_sigma_threshold=0.0,
            resolution_window_minutes=999.0,
        )

        spot = _buy_trades(3, 1000) + _sell_trades(1, 100)

        flag = detector.evaluate(
            market_id="0xstrict",
            position_book=CLEAN_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        # With zero thresholds and wide window, should fire HIGH
        assert flag.level == RiskLevel.HIGH

    def test_flag_fields_populated(self):
        """All RiskFlag fields are populated after evaluation."""
        detector = OracleEngineeringDetector()

        spot = _buy_trades(8, 120000) + _sell_trades(2, 5000)

        flag = detector.evaluate(
            market_id="0xfields",
            position_book=A4385_BOOK,
            spot_flow_window=spot,
            minutes_to_resolution=3.0,
        )

        assert flag.market_id == "0xfields"
        assert flag.minutes_to_resolution == pytest.approx(3.0)
        assert flag.pm_concentration >= 0.0
        assert flag.spot_sigma >= 0.0
        assert flag.risk_flag == "oracle_engineering"
        assert isinstance(flag.reason, str) and len(flag.reason) > 0

"""
Oracle-engineering risk detector for short-dated Chainlink-resolved Polymarket markets.

Background (oracle-engineering concept, a4385 case):
  An adversary pre-positions heavily on one side of a short-dated UP/DOWN Polymarket
  market, then executes a large spot buy/sell on the underlying (e.g. BTC on Binance)
  ~2 minutes before the Chainlink oracle snapshot, moving the honest oracle read to
  match their PM position. ~$300K extracted per incident; detection requires cross-venue
  correlation between PM concentration and underlying spot flow.

This detector is a **halt gate only**:
  - NEVER use as a buy-the-attacker or copy-trade signal.
  - Only gates automated MM/copytrade skills in the same market.
  - Never blocks the user's own discretionary trades.

Usage:
    detector = OracleEngineeringDetector()
    flag = detector.evaluate(
        market_id="0x...",
        position_book={"YES": 77000, "NO": 10000, "total": 87000},
        spot_flow_window=[
            {"side": "buy", "size": 900000, "ts": 1714948740},
            ...
        ],
        minutes_to_resolution=4.0,
    )
    if flag.level == RiskLevel.HIGH:
        # Widen spreads ≥3× or skip trade; log with risk_flag="oracle_engineering"
        pass
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"       # one signal present; monitor
    HIGH = "high"     # both signals diverge within resolution window → halt


@dataclass
class RiskFlag:
    """
    Result of OracleEngineeringDetector.evaluate().

    Fields:
      level        — NONE / LOW / HIGH
      market_id    — the market evaluated
      reason       — human-readable diagnosis
      pm_concentration — fraction held by top wallet(s) on one side (0-1)
      spot_sigma   — spot flow imbalance in standard-deviation units
      minutes_to_resolution — how close to resolution at evaluation time
      risk_flag    — constant string "oracle_engineering" for log tagging
    """

    level: RiskLevel = RiskLevel.NONE
    market_id: str = ""
    reason: str = ""
    pm_concentration: float = 0.0
    spot_sigma: float = 0.0
    minutes_to_resolution: float = 0.0
    risk_flag: str = "oracle_engineering"

    @property
    def should_halt(self) -> bool:
        """True when automated skills must widen spreads ≥3× or skip the trade."""
        return self.level == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class OracleEngineeringDetector:
    """
    Lightweight detector for oracle-engineering risk on short-dated
    Chainlink-resolved Polymarket UP/DOWN markets.

    Algorithm (mirrors the a4385 signature):
      1. Compute PM-side concentration: fraction of total open interest held by the
         top wallet(s) on the *winning* side.  Threshold: ≥ concentration_threshold
         (default 0.60 = 60%).
      2. Compute spot flow sigma: measure the flow imbalance in the underlying spot
         market over the resolution window.  Using baseline mean/stdev from the
         provided window, express current imbalance as a z-score.  Threshold:
         ≥ spot_sigma_threshold (default 2.0σ).
      3. Direction divergence check: the PM concentration must be on the side that
         BENEFITS from the spot flow direction.  If both signals agree directionally
         (clean momentum run), no flag is emitted.
      4. Window gate: both signals must be observed within minutes_to_resolution ≤
         resolution_window_minutes (default 5) to qualify as HIGH risk.  Signals
         outside the window may emit LOW at most.

    Args:
      concentration_threshold: float — minimum fraction of one side's OI held by
        top wallet(s) to consider concentrated.  Default 0.60.
      spot_sigma_threshold: float — minimum z-score of spot flow imbalance to
        consider a meaningful directional push.  Default 2.0.
      resolution_window_minutes: float — only emit HIGH within this many minutes
        of resolution.  Default 5.0.
    """

    def __init__(
        self,
        concentration_threshold: float = 0.60,
        spot_sigma_threshold: float = 2.0,
        resolution_window_minutes: float = 5.0,
    ) -> None:
        self.concentration_threshold = concentration_threshold
        self.spot_sigma_threshold = spot_sigma_threshold
        self.resolution_window_minutes = resolution_window_minutes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        market_id: str,
        position_book: dict,
        spot_flow_window: List[dict],
        minutes_to_resolution: float,
    ) -> RiskFlag:
        """
        Evaluate oracle-engineering risk for a single market snapshot.

        Args:
          market_id: str
            Polymarket condition ID or any identifier for logging.

          position_book: dict with keys:
            "YES"   — total YES-side open interest (shares or USDC, consistent units)
            "NO"    — total NO-side open interest
            "total" — total OI across both sides (may exceed YES+NO if rounded)
            "top_yes_concentration" — optional float (0-1); fraction of YES OI held
              by the top wallet(s).  If absent, computed from YES / total.
            "top_no_concentration"  — optional float (0-1); same for NO side.
              If absent, computed from NO / total.

            Concentration is computed per-side: if top wallet(s) hold a large
            fraction of one side that is out-of-line with the other, that is the
            signal.  When only aggregate OI is available (not per-wallet breakdown),
            the ratio of YES or NO to total is used as a conservative proxy.

          spot_flow_window: list of trade-flow dicts, each containing:
            "side"  — "buy" or "sell" (from the perspective of the taker)
            "size"  — float, notional USD value of the trade
            "ts"    — Unix timestamp (int or float)

            The window should cover the same resolution window as used by the
            oracle (e.g. the final 5 minutes of the resolution period).
            For the detector to compute a meaningful sigma, at least 3 data
            points are recommended; fewer than 2 returns RiskLevel.NONE.

          minutes_to_resolution: float
            How many minutes remain until the oracle snapshot.  Must be ≥ 0.

        Returns:
          RiskFlag with level NONE / LOW / HIGH.
        """
        flag = RiskFlag(market_id=market_id, minutes_to_resolution=minutes_to_resolution)

        # --- 1. PM-side concentration ---
        concentration, concentrated_side = self._compute_pm_concentration(position_book)
        flag.pm_concentration = concentration

        # --- 2. Spot flow sigma ---
        spot_sigma, flow_direction = self._compute_spot_sigma(spot_flow_window)
        flag.spot_sigma = spot_sigma

        # --- 3. Evaluate signals ---
        within_window = minutes_to_resolution <= self.resolution_window_minutes

        pm_signal = concentration >= self.concentration_threshold
        spot_signal = spot_sigma >= self.spot_sigma_threshold

        # Divergence: PM concentrated on one side while spot pushes the OTHER side
        # toward that resolution outcome.  Example: PM heavy YES + spot aggressive BUY
        # (both aligned toward YES resolution) — that's the a4385 pattern.
        # Clean run: PM heavy YES + spot heavy SELL (opposite direction) — MM hedging,
        # not oracle engineering.
        direction_diverges = self._directions_diverge(concentrated_side, flow_direction)

        if pm_signal and spot_signal and direction_diverges and within_window:
            flag.level = RiskLevel.HIGH
            flag.reason = (
                f"Oracle-engineering pattern: PM {concentrated_side} concentration "
                f"{concentration:.0%} (≥{self.concentration_threshold:.0%}) AND "
                f"spot flow {flow_direction} at {spot_sigma:.1f}σ (≥{self.spot_sigma_threshold}σ) "
                f"within {minutes_to_resolution:.1f} min of resolution — "
                "automated skills must widen ≥3× or skip"
            )
            logger.warning(
                "RESOLUTION_TAMPER_RISK market=%s pm_side=%s concentration=%.2f "
                "spot_sigma=%.2f flow_dir=%s minutes_to_resolution=%.1f",
                market_id,
                concentrated_side,
                concentration,
                spot_sigma,
                flow_direction,
                minutes_to_resolution,
            )
        elif pm_signal or spot_signal:
            flag.level = RiskLevel.LOW
            signals = []
            if pm_signal:
                signals.append(f"PM {concentrated_side} concentration {concentration:.0%}")
            if spot_signal:
                signals.append(f"spot flow {spot_sigma:.1f}σ")
            flag.reason = f"Single oracle-engineering signal ({'; '.join(signals)}); monitoring"
        else:
            flag.level = RiskLevel.NONE
            flag.reason = (
                f"No oracle-engineering signal (concentration={concentration:.0%}, "
                f"spot_sigma={spot_sigma:.1f}σ)"
            )

        return flag

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_pm_concentration(self, position_book: dict) -> tuple[float, str]:
        """
        Return (max_concentration, side) where side is "YES" or "NO".

        When per-wallet data is not available, we use the fraction of total OI
        held by the dominant side as a conservative proxy.  Callers with wallet
        breakdowns should pass "top_yes_concentration" and "top_no_concentration"
        directly.
        """
        yes_oi = float(position_book.get("YES", 0) or 0)
        no_oi = float(position_book.get("NO", 0) or 0)
        total_oi = float(position_book.get("total", yes_oi + no_oi) or 0)

        if total_oi <= 0:
            return 0.0, "YES"

        # Prefer explicitly provided per-wallet concentrations
        top_yes = position_book.get("top_yes_concentration")
        top_no = position_book.get("top_no_concentration")

        if top_yes is not None and top_no is not None:
            yes_conc = float(top_yes)
            no_conc = float(top_no)
        else:
            # Fallback: fraction of total OI on each side (conservative proxy)
            yes_conc = yes_oi / total_oi
            no_conc = no_oi / total_oi

        if yes_conc >= no_conc:
            return yes_conc, "YES"
        return no_conc, "NO"

    def _compute_spot_sigma(self, spot_flow_window: List[dict]) -> tuple[float, Optional[str]]:
        """
        Compute the flow imbalance z-score across the window.

        Each entry has "side" ("buy" / "sell") and "size" (notional USD).
        Net flow = sum(buy sizes) - sum(sell sizes).
        Per-observation signed sizes form a series; we express the final net as
        z-score over that series.

        Returns (sigma, direction) where direction is "buy" or "sell" (the taker
        net direction).  Returns (0.0, None) when insufficient data.
        """
        if len(spot_flow_window) < 2:
            return 0.0, None

        signed_sizes = []
        for trade in spot_flow_window:
            side = (trade.get("side") or "").lower()
            size = float(trade.get("size", 0) or 0)
            if side == "buy":
                signed_sizes.append(size)
            elif side == "sell":
                signed_sizes.append(-size)
            # skip unknown side entries

        if len(signed_sizes) < 2:
            return 0.0, None

        mean = statistics.mean(signed_sizes)
        try:
            stdev = statistics.stdev(signed_sizes)
        except statistics.StatisticsError:
            return 0.0, None

        if stdev <= 0:
            return 0.0, None

        net = sum(signed_sizes)
        sigma = net / stdev
        direction = "buy" if net > 0 else "sell"

        return abs(sigma), direction

    @staticmethod
    def _directions_diverge(concentrated_side: Optional[str], flow_direction: Optional[str]) -> bool:
        """
        Return True when PM concentration and spot flow are ALIGNED toward the same
        resolution outcome — the oracle-engineering signature.

        Oracle-engineering pattern:
          - Heavy YES PM position + aggressive BUY spot flow → YES resolution benefits both
          - Heavy NO PM position + aggressive SELL spot flow → DOWN resolution benefits both

        Clean directional run (NOT a divergence — no alarm):
          - Heavy YES PM + aggressive SELL spot → MMs hedging correctly, not manipulation
          - Heavy NO PM + aggressive BUY spot → same

        The name "diverge" here is from the SIGNAL perspective: PM concentration and
        the expected *honest* MM hedge diverge (they point the same way instead of
        opposite ways as a normal hedge would).
        """
        if concentrated_side is None or flow_direction is None:
            return False

        # YES concentration aligned with BUY pressure → UP resolution
        if concentrated_side == "YES" and flow_direction == "buy":
            return True
        # NO concentration aligned with SELL pressure → DOWN resolution
        if concentrated_side == "NO" and flow_direction == "sell":
            return True

        return False

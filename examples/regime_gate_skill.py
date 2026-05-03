"""
Reference integration for the realized-vol regime gate (SIM-1450).

Shows the canonical pattern for skills that should only trade in a specific
regime: fetch a candle series, run the gate, and skip sizing entirely if
the regime doesn't match.

Pattern:
    1. Fetch the last N candle close prices from your venue.
    2. Call `realized_vol_gate(prices, ...)` with the regime your strategy
       is registered for ("range_bound" or "trending").
    3. If `decision.allowed` is False, log the reason and return — do NOT
       fall through to sizing.
    4. If allowed, proceed to `size_position(...)` as usual.

The gate is a *precondition* to sizing. It is distinct from the empirical-
Kelly haircut in `simmer_sdk.sizing` (SIM-1012), which only scales an
already-allowed trade. The gate decides trade / no-trade; the haircut
decides how much.

Usage:
    export SIMMER_API_KEY="sk_live_..."
    python regime_gate_skill.py
"""

from __future__ import annotations

import os
from typing import Sequence

from simmer_sdk import (
    SimmerClient,
    realized_vol_gate,
    size_position,
)


def fetch_recent_close_prices(
    client: SimmerClient,
    market_id: str,
    lookback_candles: int = 12,
) -> Sequence[float]:
    """Fetch the last `lookback_candles` close prices for `market_id`.

    Replace this stub with your venue's actual candle endpoint. The gate is
    venue-agnostic — it only needs a sequence of floats, oldest first.
    """
    # Example placeholder: in a real skill, call your venue's price-history
    # API and return the closes. For Polymarket / Kalshi / Simmer, see the
    # respective skill for the canonical fetch path.
    history = getattr(client, "get_price_history", None)
    if history is None:
        return []
    candles = history(market_id=market_id, limit=lookback_candles) or []
    return [float(c["close"]) for c in candles]


def run_one_market_with_regime_gate(
    client: SimmerClient,
    market_id: str,
    p_win: float,
    market_price: float,
    bankroll: float,
    *,
    regime_strategy: str = "range_bound",
    lookback_candles: int = 12,
    vol_threshold: float = 0.02,
) -> float:
    """Decide and (optionally) size a trade for one market, gated by regime.

    Returns the dollar amount the strategy would allocate. 0.0 means skip.
    """
    prices = fetch_recent_close_prices(client, market_id, lookback_candles)

    decision = realized_vol_gate(
        prices,
        lookback_candles=lookback_candles,
        regime_strategy=regime_strategy,
        vol_threshold=vol_threshold,
        asset=market_id,
        timeframe="1m",
    )

    if not decision.allowed:
        print(
            f"[regime-gate] skip market={market_id} "
            f"reason={decision.reason} "
            f"realized_vol={decision.realized_vol:.4f} "
            f"observed_regime={decision.regime} "
            f"strategy_regime={regime_strategy}"
        )
        return 0.0

    print(
        f"[regime-gate] allow market={market_id} "
        f"realized_vol={decision.realized_vol:.4f} "
        f"regime={decision.regime}"
    )
    # Gate passed -> proceed to sizing as usual.
    return size_position(
        p_win=p_win,
        market_price=market_price,
        bankroll=bankroll,
    )


if __name__ == "__main__":
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        raise SystemExit("SIMMER_API_KEY not set; this example requires a live key.")

    client = SimmerClient(api_key=api_key)
    markets = client.get_markets(import_source="polymarket", limit=1)
    if not markets:
        raise SystemExit("No markets available.")

    m = markets[0]
    amount = run_one_market_with_regime_gate(
        client,
        market_id=m.id,
        p_win=0.62,
        market_price=m.current_probability,
        bankroll=1000.0,
        regime_strategy="range_bound",
    )
    print(f"sized amount: {amount:.2f}")

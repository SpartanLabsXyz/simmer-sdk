"""Unit tests for the math + parsing functions of weather-ev-port.

Math functions are ported line-for-line from alteregoeth-ai/weatherbot (MIT).
These tests lock in the exact numerical behavior so we can refactor or extend
without drifting from upstream.
"""

import importlib
import math
import os
import sys
from pathlib import Path

# Make the skill importable without installing it as a package
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))

# Stub out simmer_sdk.skill.load_config so the module imports cleanly in CI
# without the SDK present. The math module never calls SimmerClient at import time.
if "simmer_sdk" not in sys.modules:
    import types
    simmer_sdk = types.ModuleType("simmer_sdk")
    simmer_sdk_skill = types.ModuleType("simmer_sdk.skill")
    def _load_config(schema, _file, slug=None):
        out = {}
        for k, meta in schema.items():
            raw = os.environ.get(meta.get("env", ""))
            if raw is None:
                out[k] = meta["default"]
            else:
                t = meta.get("type", str)
                try:
                    out[k] = t(raw) if t is not bool else (raw.lower() in ("1", "true", "yes"))
                except Exception:
                    out[k] = meta["default"]
        return out
    simmer_sdk_skill.load_config = _load_config
    sys.modules["simmer_sdk"] = simmer_sdk
    sys.modules["simmer_sdk.skill"] = simmer_sdk_skill

wep = importlib.import_module("weather_ev_port")


# ---------------------------------------------------------------------------
# norm_cdf
# ---------------------------------------------------------------------------

def test_norm_cdf_zero():
    assert wep.norm_cdf(0) == 0.5

def test_norm_cdf_monotonic():
    assert wep.norm_cdf(-2) < wep.norm_cdf(-1) < wep.norm_cdf(0) < wep.norm_cdf(1) < wep.norm_cdf(2)

def test_norm_cdf_tails():
    # Classic 68-95-99.7 — crude bounds
    assert 0.15 < wep.norm_cdf(-1) < 0.17
    assert 0.83 < wep.norm_cdf(1) < 0.85
    assert 0.975 < wep.norm_cdf(2) < 0.978


# ---------------------------------------------------------------------------
# calc_ev — EV = p * (1/price - 1) - (1 - p)
# ---------------------------------------------------------------------------

def test_calc_ev_fair_coin():
    # p=0.5, price=0.5 → EV = 0.5*(1) - 0.5 = 0.0
    assert wep.calc_ev(0.5, 0.5) == 0.0

def test_calc_ev_positive_edge():
    # p=0.7, price=0.5: EV = 0.7 * 1.0 - 0.3 = 0.4
    assert wep.calc_ev(0.7, 0.5) == 0.4

def test_calc_ev_negative_edge():
    # p=0.3, price=0.5: EV = 0.3 * 1.0 - 0.7 = -0.4
    assert wep.calc_ev(0.3, 0.5) == -0.4

def test_calc_ev_guard_prices():
    assert wep.calc_ev(0.5, 0.0) == 0.0
    assert wep.calc_ev(0.5, 1.0) == 0.0
    assert wep.calc_ev(0.5, -0.1) == 0.0


# ---------------------------------------------------------------------------
# calc_kelly — fractional Kelly (KELLY_FRACTION=0.25 default)
# ---------------------------------------------------------------------------

def test_calc_kelly_zero_edge():
    # Fair coin, fair price → Kelly = 0
    assert wep.calc_kelly(0.5, 0.5) == 0.0

def test_calc_kelly_positive():
    # p=0.6, price=0.5: b=1.0, f = (0.6*1 - 0.4) / 1 = 0.2; fractional = 0.2 * 0.25 = 0.05
    assert wep.calc_kelly(0.6, 0.5) == 0.05

def test_calc_kelly_negative_edge_clamped():
    # Negative edge → Kelly=0 (don't short)
    assert wep.calc_kelly(0.3, 0.5) == 0.0

def test_calc_kelly_capped_at_one():
    # Extreme: p=0.99, price=0.01 → raw Kelly > 1, should be capped
    assert wep.calc_kelly(0.99, 0.01) <= 1.0


# ---------------------------------------------------------------------------
# bet_size
# ---------------------------------------------------------------------------

def test_bet_size_basic():
    # MAX_BET default = 20.0
    # kelly=0.05, balance=100 → 5.00
    assert wep.bet_size(0.05, 100) == 5.00

def test_bet_size_capped_at_max():
    # kelly=0.5, balance=1000 → raw=500, capped at MAX_BET
    assert wep.bet_size(0.5, 1000) == wep.MAX_BET


# ---------------------------------------------------------------------------
# in_bucket + bucket_prob
# ---------------------------------------------------------------------------

def test_in_bucket_range():
    assert wep.in_bucket(45, 40, 50) is True
    assert wep.in_bucket(50, 40, 50) is True  # inclusive upper
    assert wep.in_bucket(40, 40, 50) is True  # inclusive lower
    assert wep.in_bucket(55, 40, 50) is False

def test_in_bucket_single_value():
    # t_low == t_high means "exact value"
    assert wep.in_bucket(42.0, 42, 42) is True
    assert wep.in_bucket(42.4, 42, 42) is True  # rounds to 42
    assert wep.in_bucket(43, 42, 42) is False

def test_bucket_prob_regular():
    # Regular bucket, forecast inside → 1.0
    assert wep.bucket_prob(45, 40, 50) == 1.0
    # Regular bucket, forecast outside → 0.0
    assert wep.bucket_prob(55, 40, 50) == 0.0

def test_bucket_prob_edge_below():
    # "or below" bucket: p = CDF((upper - forecast) / sigma)
    # forecast=30, upper=35, sigma=2 → CDF(2.5) ≈ 0.9938
    p = wep.bucket_prob(30, -999, 35, sigma=2.0)
    assert 0.99 < p < 1.0

def test_bucket_prob_edge_above():
    # "or higher" bucket: p = 1 - CDF((lower - forecast) / sigma)
    # forecast=90, lower=85, sigma=2 → 1 - CDF(-2.5) ≈ 0.9938
    p = wep.bucket_prob(90, 85, 999, sigma=2.0)
    assert 0.99 < p < 1.0

def test_bucket_prob_edge_adverse():
    # Edge bucket with forecast on the wrong side
    p = wep.bucket_prob(50, 85, 999, sigma=2.0)
    assert p < 0.01


# ---------------------------------------------------------------------------
# parse_temp_range
# ---------------------------------------------------------------------------

def test_parse_temp_range_between():
    assert wep.parse_temp_range("Highest temperature between 40-45°F") == (40.0, 45.0)

def test_parse_temp_range_or_below():
    assert wep.parse_temp_range("Highest temperature 35°F or below") == (-999.0, 35.0)

def test_parse_temp_range_or_higher():
    assert wep.parse_temp_range("Highest temperature 95°F or higher") == (95.0, 999.0)

def test_parse_temp_range_exact():
    assert wep.parse_temp_range("Will the highest temperature be 72°F on March 7") == (72.0, 72.0)

def test_parse_temp_range_celsius():
    assert wep.parse_temp_range("Temperature between 12-13°C") == (12.0, 13.0)

def test_parse_temp_range_unparseable():
    assert wep.parse_temp_range("") is None
    assert wep.parse_temp_range("Will it rain tomorrow") is None


# ---------------------------------------------------------------------------
# Sanity: entry stack math composes correctly
# ---------------------------------------------------------------------------

def test_entry_stack_profitable():
    """Canonical example: forecast hits bucket, cheap ask → accept."""
    p = wep.bucket_prob(45, 40, 50)           # = 1.0
    assert p == 1.0
    ev = wep.calc_ev(p, 0.30)                  # huge EV
    assert ev > wep.MIN_EV
    kelly = wep.calc_kelly(p, 0.30)
    assert kelly > 0

def test_entry_stack_rejects_expensive():
    """Forecast hits bucket but ask too high (> MAX_PRICE) — caller rejects."""
    p = wep.bucket_prob(45, 40, 50)
    ev = wep.calc_ev(p, 0.95)
    # p=1, price=0.95: EV = 1 * (1/0.95 - 1) - 0 ≈ 0.0526
    assert ev > 0  # technically positive EV, but caller should reject on MAX_PRICE
    assert 0.95 >= wep.MAX_PRICE  # confirm the guard kicks in

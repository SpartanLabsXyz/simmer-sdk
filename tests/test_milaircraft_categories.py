"""Unit tests for classify_market_category() in milaircraft_tracker.

Locks in keyword coverage for the SIM-2564 category gate.  Tests run with the
skill module imported stand-alone — no SimmerClient or env-var dependencies.
"""
import sys
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "polymarket-mil-aircraft-tracker"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# Stub load_config so importing milaircraft_tracker needs no env vars.
import simmer_sdk.skill as _sk

_orig_load_config = _sk.load_config


def _stub_load_config(schema, f, **kw):
    return {k: v["default"] for k, v in schema.items()}


# Apply stub for the module import, then restore.
_sk.load_config = _stub_load_config
try:
    from milaircraft_tracker import classify_market_category, INVASION_TAIL_RISK_KEYWORDS
finally:
    _sk.load_config = _orig_load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _market(question="", event_name=None, resolution_criteria=None, description=None):
    return {
        "question": question,
        "event_name": event_name,
        "resolution_criteria": resolution_criteria,
        "description": description,
    }


# ---------------------------------------------------------------------------
# Tail-risk (alert-only) markets
# ---------------------------------------------------------------------------

def test_invade_keyword_is_tail_risk():
    """Korean Peninsula invasion market → invasion_tail_risk."""
    m = _market("Will North Korea invade South Korea before 2027?", event_name="Korean Peninsula")
    assert classify_market_category(m) == "invasion_tail_risk"


def test_invasion_keyword_is_tail_risk():
    """Market containing 'invasion' → invasion_tail_risk."""
    m = _market("Will a Russian invasion of Moldova occur in 2025?")
    assert classify_market_category(m) == "invasion_tail_risk"


def test_regime_change_keyword_is_tail_risk():
    """Regime change market → invasion_tail_risk."""
    m = _market("Will Iran undergo regime change in 2025?")
    assert classify_market_category(m) == "invasion_tail_risk"


def test_annex_keyword_is_tail_risk():
    """Annexation market → invasion_tail_risk."""
    m = _market("Will Russia annex another Ukrainian oblast this year?")
    assert classify_market_category(m) == "invasion_tail_risk"


def test_declare_war_keyword_is_tail_risk():
    """War declaration market → invasion_tail_risk."""
    m = _market("Will China declare war on Taiwan before 2026?")
    assert classify_market_category(m) == "invasion_tail_risk"


def test_tail_risk_keyword_in_description_field():
    """Tail-risk keyword in description is matched even if question is neutral."""
    m = _market(
        question="Korean Peninsula escalation market",
        description="Resolves YES if North Korea invades South Korea by end of 2026.",
    )
    assert classify_market_category(m) == "invasion_tail_risk"


def test_tail_risk_keyword_in_event_name():
    """Tail-risk keyword in event_name is matched."""
    m = _market(
        question="Will this escalate?",
        event_name="North Korea invasion scenario",
    )
    assert classify_market_category(m) == "invasion_tail_risk"


def test_tail_risk_keyword_in_resolution_criteria():
    """Tail-risk keyword in resolution_criteria is matched."""
    m = _market(
        question="Military escalation",
        resolution_criteria="Resolves YES if armed forces annex territory before July 2025.",
    )
    assert classify_market_category(m) == "invasion_tail_risk"


def test_case_insensitivity_upper():
    """Classification is case-insensitive (UPPERCASE)."""
    m = _market("Will North Korea INVADE South Korea in 2025?")
    assert classify_market_category(m) == "invasion_tail_risk"


# ---------------------------------------------------------------------------
# Strike / activity markets (should trade as before)
# ---------------------------------------------------------------------------

def test_airstrike_keyword_is_strike_activity():
    """Yemen airstrike market → strike_activity."""
    m = _market("Will the US conduct an airstrike in Yemen this week?", event_name="Yemen Conflict")
    assert classify_market_category(m) == "strike_activity"


def test_missile_attack_is_strike_activity():
    """Missile attack market → strike_activity."""
    m = _market("Will Russia conduct a missile attack on Kyiv this week?")
    assert classify_market_category(m) == "strike_activity"


def test_bombing_is_strike_activity():
    """Bombing market → strike_activity."""
    m = _market("Will Israel bomb a target in Syria this month?")
    assert classify_market_category(m) == "strike_activity"


def test_military_strike_is_strike_activity():
    """Military strike market → strike_activity."""
    m = _market("Will the US conduct a military strike in Iran this quarter?")
    assert classify_market_category(m) == "strike_activity"


def test_attack_only_is_strike_activity():
    """Generic attack market with no tail-risk keywords → strike_activity."""
    m = _market("Will Houthi forces attack a US vessel in the Red Sea this week?")
    assert classify_market_category(m) == "strike_activity"


# ---------------------------------------------------------------------------
# INVASION_TAIL_RISK_KEYWORDS constant coverage
# ---------------------------------------------------------------------------

def test_all_tail_risk_keywords_defined():
    """Every required tail-risk keyword from the spec is present in the constant."""
    required = {"invasion", "invade", "regime change", "annex", "declare war"}
    assert required.issubset(set(INVASION_TAIL_RISK_KEYWORDS))

"""Integration tests — require PREF_API_KEY. Skipped in CI."""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PREF_API_KEY"),
    reason="PREF_API_KEY not set — skipping integration tests",
)

SKILL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
    "polymarket-mil-aircraft-tracker",
)


@pytest.fixture(autouse=True)
def _skill_on_path():
    if SKILL_PATH not in sys.path:
        sys.path.insert(0, SKILL_PATH)
    yield
    if SKILL_PATH in sys.path:
        sys.path.remove(SKILL_PATH)


def test_pref_get_military_aircraft_live():
    """Live call to pref returns aircraft list."""
    from pref_client import get_military_aircraft

    aircraft = get_military_aircraft()
    assert isinstance(aircraft, list)
    # Globally there should always be some mil aircraft broadcasting
    assert len(aircraft) > 0, "Expected >0 military aircraft from pref — API may be down"
    ac = aircraft[0]
    assert "hex" in ac or "lat" in ac


def test_pref_account_status_live():
    """preference_account_status returns auth + quota info."""
    from pref_client import get_account_status

    status = get_account_status()
    assert status is not None
    assert "auth_state" in status or "daily_included_credits" in status
    credits = status.get("daily_included_credits", {})
    if credits:
        assert "remaining" in credits


def test_full_cycle_dry_run():
    """Full strategy cycle completes without error in dry-run mode."""
    os.environ.setdefault("SIMMER_API_KEY", "sk_test_fake")
    os.environ["TRADING_VENUE"] = "sim"

    from milaircraft_tracker import run_strategy

    # Should not raise
    run_strategy(dry_run=True, positions_only=False, show_config=False, use_safeguards=True)

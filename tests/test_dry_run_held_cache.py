"""A dry run must not enter the client-side held-markets cache.

``trade(dry_run=True)`` places nothing, but it used to update
``_held_markets_cache`` exactly like a fill. The next real buy on the same
market was then refused client-side with "Already hold position on this
market" while ``get_positions()`` was empty -- and the sequence that triggers
it is the preview-then-place snippet documented in skills/simmer/SKILL.md
(Grok Bot dogfood, 2026-09-05).
"""

import time
from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


def _sim_client() -> SimmerClient:
    """Minimal client on the sim venue with a warm (empty) held-markets cache."""
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = "test-agent"
    client._ows_wallet = None
    client._wallet_address = None
    client._private_key = None
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = "sim"
    # Warm and fresh, so the real _get_held_markets() serves from the cache
    # rather than reaching for /positions. Do NOT mock _get_held_markets here:
    # the rebuy guard reads through it, so mocking it would hide the bug.
    client._held_markets_cache = {}
    client._held_markets_ts = time.time()
    client._clob_creds_registered = True
    client._request = MagicMock(
        return_value={
            "success": True,
            "market_id": "m1",
            "side": "yes",
            "shares_bought": 5.0,
            "cost": 2.0,
        }
    )
    client._ensure_clob_credentials = MagicMock()
    client._ensure_wallet_linked = MagicMock()
    client._warn_approvals_once = MagicMock()
    client._build_signed_order = MagicMock(return_value=None)
    client._is_agent_wallet_registered = MagicMock(return_value=False)
    return client


def test_dry_run_buy_leaves_held_cache_empty():
    client = _sim_client()

    result = client.trade("m1", "yes", 10.0, dry_run=True)

    assert result.success
    assert client._held_markets_cache == {}, (
        "dry run poisoned the held-markets cache; the next real buy on m1 "
        "would be refused with 'Already hold position'"
    )


def test_real_buy_still_enters_held_cache():
    """Control: the cache update must still happen for an actual placement."""
    client = _sim_client()

    result = client.trade("m1", "yes", 10.0)

    assert result.success
    assert "m1" in client._held_markets_cache


def test_preview_then_place_is_not_blocked():
    """The documented snippet: dry_run to size, then place for real."""
    client = _sim_client()

    client.trade("m1", "yes", 10.0, dry_run=True)
    result = client.trade("m1", "yes", 10.0)

    assert result.success
    assert result.error is None

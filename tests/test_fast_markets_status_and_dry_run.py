"""Tests for get_fast_markets(market_status=) and trade(dry_run=).

Both parameters existed on the server long before the helpers passed them, so
callers had to drop to ``client._request()``. These lock in the pass-through
and the one behaviour that is not pass-through: a dry run must not sign an
order locally, because signing costs a direct Polymarket credential derivation
to produce a signature the server never submits — the exact call an operator
behind a Polymarket block cannot make.
"""

from unittest.mock import MagicMock

import pytest

from simmer_sdk.client import SimmerClient


def _client(**attrs) -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client.venue = "polymarket"
    client.live = True
    client._private_key = None
    client._ows_wallet = None
    client._wallet_address = None
    client._readonly = False
    client._held_markets_cache = None
    for key, value in attrs.items():
        setattr(client, key, value)
    return client


# --- get_fast_markets(market_status=) ---------------------------------------


@pytest.mark.parametrize("status", ["live", "upcoming"])
def test_market_status_is_forwarded(status):
    client = _client()
    client._request = MagicMock(return_value={"markets": []})

    client.get_fast_markets(asset="BTC", window="5m", market_status=status)

    params = client._request.call_args.kwargs["params"]
    assert params["market_status"] == status
    assert params["asset"] == "BTC"
    assert params["window"] == "5m"


def test_market_status_omitted_by_default():
    """Omitting it must return both live and upcoming — that is the default a
    caller wants when reading every upcoming window for an asset at once."""
    client = _client()
    client._request = MagicMock(return_value={"markets": []})

    client.get_fast_markets(asset="BTC")

    assert "market_status" not in client._request.call_args.kwargs["params"]


# --- trade(dry_run=) --------------------------------------------------------


def test_dry_run_is_forwarded():
    client = _client()
    client._request = MagicMock(return_value={"success": True})
    client._get_held_markets = MagicMock(return_value={})

    client.trade("mkt-1", "yes", amount=5.0, venue="polymarket", dry_run=True)

    assert client._request.call_args.kwargs["json"]["dry_run"] is True


def test_dry_run_absent_from_payload_by_default():
    """A live trade must not carry the flag at all — an explicit false would
    still be a behaviour change on any server that keys on presence."""
    client = _client()
    client._request = MagicMock(return_value={"success": True})
    client._get_held_markets = MagicMock(return_value={})

    client.trade("mkt-1", "yes", amount=5.0, venue="polymarket")

    assert "dry_run" not in client._request.call_args.kwargs["json"]


def test_dry_run_does_not_sign_locally():
    """With a signing key configured, a dry run must skip _build_signed_order.

    Signing reaches clob.polymarket.com to derive credentials on first use. The
    server returns from its dry-run branch before it would ever consume a
    signed order, so that call buys nothing.
    """
    client = _client(_private_key="0x" + "11" * 32, _wallet_address="0xabc")
    client._request = MagicMock(return_value={"success": True})
    client._get_held_markets = MagicMock(return_value={})
    client._build_signed_order = MagicMock()
    client._ensure_wallet_linked = MagicMock()
    client._is_agent_wallet_registered = MagicMock(return_value=False)
    client._warn_approvals_once = MagicMock()

    client.trade("mkt-1", "yes", amount=5.0, venue="polymarket", dry_run=True)

    client._build_signed_order.assert_not_called()
    assert "signed_order" not in client._request.call_args.kwargs["json"]


def test_live_trade_still_signs_locally():
    """The control: same client, dry_run off, signing must still happen."""
    client = _client(_private_key="0x" + "11" * 32, _wallet_address="0xabc")
    client._request = MagicMock(return_value={"success": True})
    client._get_held_markets = MagicMock(return_value={})
    client._build_signed_order = MagicMock(return_value={"salt": "1"})
    client._ensure_wallet_linked = MagicMock()
    client._is_agent_wallet_registered = MagicMock(return_value=False)
    client._warn_approvals_once = MagicMock()

    client.trade("mkt-1", "yes", amount=5.0, venue="polymarket")

    client._build_signed_order.assert_called_once()
    assert client._request.call_args.kwargs["json"]["signed_order"] == {"salt": "1"}


def test_dry_run_is_keyword_only():
    """Money path: dry_run must never be settable by argument position."""
    client = _client()
    client._request = MagicMock(return_value={"success": True})
    client._get_held_markets = MagicMock(return_value={})

    with pytest.raises(TypeError):
        client.trade("mkt-1", "yes", 5.0, 0, "buy", "polymarket", None, None,
                     None, None, None, False, None, True)

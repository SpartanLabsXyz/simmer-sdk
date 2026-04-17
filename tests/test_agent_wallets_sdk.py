"""Tests for per-agent wallet SDK methods."""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from simmer_sdk.client import SimmerClient


def _make_client(**overrides):
    """Create a SimmerClient with mocked internals for testing."""
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = overrides.get("agent_id", "test-agent-uuid")
    client._ows_wallet = overrides.get("ows_wallet", None)
    client._wallet_address = overrides.get("wallet_address", None)
    client._private_key = overrides.get("private_key", None)
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = "sim"

    # Attributes accessed by trade() post-request cache update
    client._held_markets_cache = None

    # Mock _request to capture calls
    client._request = MagicMock()
    return client


class TestRegisterAgentWallet:

    @patch("simmer_sdk.ows_utils.get_ows_wallet_address")
    def test_register_resolves_address_and_posts(self, mock_addr):
        mock_addr.return_value = "0x1234567890abcdef1234567890abcdef12345678"
        client = _make_client()
        client._request.return_value = {
            "id": "wallet-uuid",
            "agent_id": "test-agent-uuid",
            "ows_wallet_name": "agent-test",
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "approvals_set": False,
            "is_active": True,
        }

        result = client.register_agent_wallet("agent-test")

        mock_addr.assert_called_once_with("agent-test")
        client._request.assert_called_once_with(
            "POST", "/api/sdk/agent-wallet/register",
            json={
                "agent_id": "test-agent-uuid",
                "ows_wallet_name": "agent-test",
                "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            }
        )
        assert result["ows_wallet_name"] == "agent-test"
        assert result["approvals_set"] is False


class TestGetAgentWallets:

    def test_returns_wallet_list(self):
        client = _make_client()
        client._request.return_value = {
            "wallets": [
                {
                    "id": "w1",
                    "agent_id": "a1",
                    "ows_wallet_name": "agent-bot",
                    "wallet_address": "0xabc",
                    "approvals_set": True,
                    "is_active": True,
                    "agent_name": "my-bot",
                }
            ]
        }

        wallets = client.get_agent_wallets()

        client._request.assert_called_once_with("GET", "/api/sdk/agent-wallets")
        assert len(wallets) == 1
        assert wallets[0]["ows_wallet_name"] == "agent-bot"

    def test_empty_list(self):
        client = _make_client()
        client._request.return_value = {"wallets": []}

        wallets = client.get_agent_wallets()
        assert wallets == []


class TestGetAgentWalletPnl:

    def test_uses_default_agent_id(self):
        client = _make_client(agent_id="my-agent")
        client._request.return_value = {
            "agent_id": "my-agent",
            "realized_pnl": 10.0,
            "unrealized_pnl": -2.0,
            "total_cost": 50.0,
            "positions": [],
        }

        pnl = client.get_agent_wallet_pnl()

        client._request.assert_called_once_with("GET", "/api/sdk/agent-wallet/my-agent/pnl")
        assert pnl["realized_pnl"] == 10.0

    def test_accepts_custom_agent_id(self):
        client = _make_client(agent_id="default-agent")
        client._request.return_value = {"agent_id": "other", "realized_pnl": 0, "unrealized_pnl": 0, "total_cost": 0, "positions": []}

        client.get_agent_wallet_pnl("other-agent")

        client._request.assert_called_once_with("GET", "/api/sdk/agent-wallet/other-agent/pnl")

    def test_none_unrealized_pnl_coerced_to_zero(self):
        """Backend returns unrealized_pnl=None on the PolyNode path (SIM-854).
        SDK must coerce to 0.0 so callers doing arithmetic don't crash."""
        client = _make_client(agent_id="my-agent")
        client._request.return_value = {
            "agent_id": "my-agent",
            "realized_pnl": 5.0,
            "unrealized_pnl": None,
            "total_cost": 20.0,
            "positions": [],
        }

        pnl = client.get_agent_wallet_pnl()

        assert pnl["unrealized_pnl"] == 0.0
        assert isinstance(pnl["unrealized_pnl"], float)

    def test_missing_unrealized_pnl_key_coerced_to_zero(self):
        """If the backend omits the key entirely, SDK must still be safe."""
        client = _make_client(agent_id="my-agent")
        client._request.return_value = {
            "agent_id": "my-agent",
            "realized_pnl": 5.0,
            "total_cost": 20.0,
            "positions": [],
        }

        pnl = client.get_agent_wallet_pnl()

        assert pnl["unrealized_pnl"] == 0.0


class TestUpdateAgentWalletCreds:

    @patch("simmer_sdk.ows_utils.ows_derive_clob_creds")
    @patch("simmer_sdk.ows_utils.get_ows_wallet_address")
    def test_derives_creds_and_posts(self, mock_addr, mock_creds):
        mock_addr.return_value = "0xabcdef1234567890abcdef1234567890abcdef12"
        mock_creds_obj = MagicMock()
        mock_creds_obj.api_key = "key123"
        mock_creds_obj.api_secret = "secret456"
        mock_creds_obj.api_passphrase = "pass789"
        mock_creds.return_value = mock_creds_obj

        client = _make_client()
        client._request.return_value = {"wallet_address": "0xabc", "approvals_set": True}

        result = client.update_agent_wallet_creds("agent-test")

        mock_addr.assert_called_once_with("agent-test")
        mock_creds.assert_called_once_with("agent-test")
        client._request.assert_called_once_with(
            "POST", "/api/sdk/agent-wallet/update-creds",
            json={
                "wallet_address": "0xabcdef1234567890abcdef1234567890abcdef12",
                "clob_api_creds": {
                    "api_key": "key123",
                    "api_secret": "secret456",
                    "api_passphrase": "pass789",
                },
                "approvals_set": True,
            }
        )


class TestTradeWalletAddress:

    def test_includes_wallet_address_when_ows_wallet_set(self):
        """Trade payload includes wallet_address when OWS wallet is configured."""
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        # Mock the _request to return a successful trade
        client._request.return_value = {
            "success": True,
            "market_id": "test-market",
            "side": "yes",
            "shares_bought": 10.0,
            "cost": 5.0,
            "new_price": 0.5,
            "fill_status": "filled",
        }
        # Need to mock _get_held_markets to avoid side effects
        client._get_held_markets = MagicMock(return_value={})

        from simmer_sdk.client import TradeResult
        result = client.trade("test-market", "yes", amount=10.0)

        # Verify wallet_address was in the payload
        call_args = client._request.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload.get("wallet_address") == "0x1234567890abcdef1234567890abcdef12345678"

    def test_no_wallet_address_without_ows(self):
        """Trade payload does NOT include wallet_address when no OWS wallet."""
        client = _make_client()  # No OWS wallet
        client._request.return_value = {
            "success": True,
            "market_id": "test-market",
            "side": "yes",
            "shares_bought": 10.0,
            "cost": 5.0,
            "new_price": 0.5,
            "fill_status": "filled",
        }
        client._get_held_markets = MagicMock(return_value={})

        result = client.trade("test-market", "yes", amount=10.0)

        call_args = client._request.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "wallet_address" not in payload

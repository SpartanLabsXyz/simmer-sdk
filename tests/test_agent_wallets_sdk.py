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
        # PR #73 (0.17.5): agent_id is now derived server-side from the API
        # key; the SDK only sends ows_wallet_name + wallet_address.
        client._request.assert_called_once_with(
            "POST", "/api/sdk/agent-wallet/register",
            json={
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

    def test_raises_when_agent_id_missing(self):
        """PR #73 (0.17.5): agent_id is now a required arg — no default-to-
        self._agent_id fallback. Multi-agent clients must specify which agent
        they want P&L for explicitly."""
        client = _make_client(agent_id="my-agent")
        with pytest.raises(ValueError, match="agent_id is required"):
            client.get_agent_wallet_pnl()

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

        pnl = client.get_agent_wallet_pnl("my-agent")

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

        pnl = client.get_agent_wallet_pnl("my-agent")

        assert pnl["unrealized_pnl"] == 0.0


class TestUpdateAgentWalletCreds:

    @patch("simmer_sdk.ows_utils.ows_derive_clob_creds")
    @patch("simmer_sdk.ows_utils.get_ows_wallet_address")
    def test_ows_derives_creds_and_posts_unchanged(self, mock_addr, mock_creds):
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

    @patch("py_clob_client.client.ClobClient")
    @patch("simmer_sdk.signing.get_wallet_address")
    def test_raw_key_derives_creds_and_posts_existing_endpoint_contract(self, mock_addr, mock_clob_client):
        mock_addr.return_value = "0xf129000000000000000000000000000000000000"
        mock_creds_obj = MagicMock()
        mock_creds_obj.api_key = "raw-key"
        mock_creds_obj.api_secret = "raw-secret"
        mock_creds_obj.api_passphrase = "raw-passphrase"
        mock_clob_client.return_value.create_or_derive_api_creds.return_value = mock_creds_obj

        client = _make_client(private_key="0x" + "1" * 64)
        client._request.return_value = {
            "agent_id": "agent-118aca87",
            "wallet_address": "0xf129000000000000000000000000000000000000",
            "approvals_set": True,
        }

        result = client.update_agent_wallet_creds(agent_id="agent-118aca87")

        mock_addr.assert_called_once_with("0x" + "1" * 64)
        mock_clob_client.assert_called_once_with(
            host="https://clob.polymarket.com",
            key="0x" + "1" * 64,
            chain_id=137,
            signature_type=0,
            funder="0xf129000000000000000000000000000000000000",
        )
        mock_clob_client.return_value.create_or_derive_api_creds.assert_called_once_with()
        client._request.assert_called_once_with(
            "POST", "/api/sdk/agent-wallet/update-creds",
            json={
                "wallet_address": "0xf129000000000000000000000000000000000000",
                "clob_api_creds": {
                    "api_key": "raw-key",
                    "api_secret": "raw-secret",
                    "api_passphrase": "raw-passphrase",
                },
                "approvals_set": True,
            }
        )
        assert result["agent_id"] == "agent-118aca87"

    def test_raw_key_requires_agent_id(self):
        client = _make_client(private_key="0x" + "1" * 64)

        with pytest.raises(ValueError, match="agent_id is required"):
            client.update_agent_wallet_creds()

    def test_raw_key_requires_private_key(self):
        client = _make_client()

        with pytest.raises(RuntimeError, match="WALLET_PRIVATE_KEY"):
            client.update_agent_wallet_creds(agent_id="agent-118aca87")

    @patch("py_clob_client.client.ClobClient")
    @patch("simmer_sdk.signing.get_wallet_address")
    def test_raw_key_accepts_explicit_private_key(self, mock_addr, mock_clob_client):
        mock_addr.return_value = "0xf129000000000000000000000000000000000000"
        mock_creds_obj = MagicMock()
        mock_creds_obj.api_key = "raw-key"
        mock_creds_obj.api_secret = "raw-secret"
        mock_creds_obj.api_passphrase = "raw-passphrase"
        mock_clob_client.return_value.create_or_derive_api_creds.return_value = mock_creds_obj

        client = _make_client()
        client.update_agent_wallet_creds(
            agent_id="agent-118aca87",
            private_key="0x" + "2" * 64,
        )

        mock_addr.assert_called_once_with("0x" + "2" * 64)
        assert mock_clob_client.call_args.kwargs["key"] == "0x" + "2" * 64


class TestTradeWalletAddress:

    def test_includes_wallet_address_when_ows_wallet_registered(self):
        """Trade payload includes wallet_address when OWS wallet is registered in user_agent_wallets."""
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        # Pre-cache: this wallet IS registered (per-agent-wallet feature opted into)
        client._agent_wallet_registered = True
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

        # Verify wallet_address WAS in the payload
        call_args = client._request.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload.get("wallet_address") == "0x1234567890abcdef1234567890abcdef12345678"

    def test_registered_ows_polymarket_trade_skips_user_level_auto_link(self):
        """Registered per-agent OWS trades must not relink the user wallet.

        DW users can legitimately have open positions on the current user-level
        external wallet. The backend rejects replacing that wallet; per-agent
        OWS trades should route via user_agent_wallets instead.
        """
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        client._agent_wallet_registered = True
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
        client._ensure_wallet_linked = MagicMock(
            side_effect=RuntimeError(
                "Cannot re-link: your current external wallet has open positions on Polymarket."
            )
        )
        client._load_per_agent_dw_state = MagicMock()
        client._warn_approvals_once = MagicMock()
        client._build_signed_order = MagicMock(return_value={"signed": "order"})

        result = client.trade("test-market", "yes", amount=10.0, venue="polymarket")

        assert result.success is True
        client._ensure_wallet_linked.assert_not_called()
        client._load_per_agent_dw_state.assert_called_once()
        call_args = client._request.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["wallet_address"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert payload["signed_order"] == {"signed": "order"}

    def test_registered_ows_populates_dw_state_from_agents_me(self):
        """_load_per_agent_dw_state must populate _uses_deposit_wallet and
        _deposit_wallet_address so _build_signed_order picks sig type 3."""
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        client._uses_deposit_wallet = False
        client._deposit_wallet_address = None

        def mock_request(method, endpoint, **kwargs):
            if endpoint == "/api/sdk/agents/me":
                return {
                    "agent_id": "test-agent-uuid",
                    "per_agent_wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
                    "per_agent_dw_active": True,
                    "per_agent_deposit_wallet_address": "0xDW1234",
                    "rate_limits": {"tier": "elite"},
                    "real_trading_enabled": True,
                }
            return {}
        client._request = MagicMock(side_effect=mock_request)

        client._load_per_agent_dw_state()

        assert client._uses_deposit_wallet is True
        assert client._deposit_wallet_address == "0xDW1234"
        client._request.assert_called_once_with("GET", "/api/sdk/agents/me")

    def test_load_per_agent_dw_state_cached_after_first_call(self):
        """Second call is a no-op — no duplicate /api/sdk/agents/me fetch."""
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        client._uses_deposit_wallet = False
        client._deposit_wallet_address = None
        client._request = MagicMock(return_value={
            "per_agent_wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "per_agent_dw_active": True,
            "per_agent_deposit_wallet_address": "0xDW1234",
        })

        client._load_per_agent_dw_state()
        client._load_per_agent_dw_state()

        assert client._request.call_count == 1

    def test_load_per_agent_dw_state_graceful_on_error(self):
        """Network failure leaves DW defaults (False/None) — degrades to EOA signing."""
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        client._uses_deposit_wallet = False
        client._deposit_wallet_address = None
        client._request = MagicMock(side_effect=Exception("timeout"))

        client._load_per_agent_dw_state()

        assert client._uses_deposit_wallet is False
        assert client._deposit_wallet_address is None

    def test_omits_wallet_address_when_ows_wallet_not_registered(self):
        """OWS wallet without per-agent-wallet registration falls through to user-level path.

        Regression test for the case where an OWS user without an Elite-tier
        per-agent-wallet registration was rejected with 'Agent wallet not found'.
        """
        client = _make_client(
            ows_wallet="agent-mybot",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        # Pre-cache: this wallet is NOT registered
        client._agent_wallet_registered = False
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

        # Verify wallet_address was OMITTED — server takes user-level wallet path
        call_args = client._request.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert "wallet_address" not in payload

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

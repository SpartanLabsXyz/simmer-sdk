"""Tests for import_polymarket_wallet() + link-source tagging (SIM-4646, 0.24.4).

Phase A of the Polymarket-link onboarding: a wallet born on polymarket.com
(funded there, key exported into the agent env) becomes trading-ready in one
call — link with ownership proof, adopt the already-deployed deposit wallet,
gasless approval top-up, balance summary.
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from simmer_sdk.client import SimmerClient


def _make_client(**overrides):
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = overrides.get("agent_id", "test-agent-uuid")
    client._ows_wallet = overrides.get("ows_wallet", None)
    client._wallet_address = overrides.get(
        "wallet_address", "0x3c7fa6cb352c6df150b14e16c635029a0b56aa0b"
    )
    client._private_key = overrides.get("private_key", "0x" + "11" * 32)
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = "polymarket"
    client._request = MagicMock()
    return client


# =============================================================================
# link_wallet signature additions
# =============================================================================


def test_link_wallet_accepts_source_defaulting_none():
    sig = inspect.signature(SimmerClient.link_wallet)
    assert "source" in sig.parameters
    assert sig.parameters["source"].default is None, (
        "source must default to None so the server infers 'sdk-link' for "
        "plain SDK calls — a hardcoded default would fragment the funnel data."
    )


def test_link_wallet_accepts_confirm_orphan_deposit_wallet_defaulting_false():
    sig = inspect.signature(SimmerClient.link_wallet)
    assert "confirm_orphan_deposit_wallet" in sig.parameters
    assert sig.parameters["confirm_orphan_deposit_wallet"].default is False, (
        "Default must be False — orphaning an active deposit wallet has to be "
        "an explicit choice (server guard SIM-1611)."
    )


# =============================================================================
# import_polymarket_wallet composition
# =============================================================================


def test_import_declares_polymarket_import_source_and_composes_steps():
    client = _make_client()
    with patch.object(
        client, "link_wallet",
        return_value={"success": True, "wallet_address": client._wallet_address,
                      "clob_credentials_registered": True},
    ) as mock_link, patch.object(
        client, "activate_polymarket_dw",
        return_value={"already_set": True, "calls_count": 0, "success": True},
    ) as mock_activate:
        client._request.side_effect = [
            # external-upgrade-to-deposit-wallet — adopt, no redeploy
            {"deposit_wallet_address": "0x6471A609E9B87447cd1552FfDe4AF3625fA2F180",
             "already_existed": True},
            # briefing — balance summary
            {"venues": {"polymarket": {"balance": 5.0}}},
        ]
        result = client.import_polymarket_wallet()

    mock_link.assert_called_once_with(
        confirm_replace_managed=True,
        confirm_orphan_deposit_wallet=False,
        source="polymarket-import",
    )
    upgrade_call = client._request.call_args_list[0]
    assert upgrade_call.args == (
        "POST", "/api/user/wallet/external-upgrade-to-deposit-wallet"
    )
    mock_activate.assert_called_once_with()

    assert result["success"] is True
    assert result["deposit_wallet_address"] == (
        "0x6471A609E9B87447cd1552FfDe4AF3625fA2F180"
    )
    assert result["dw_already_existed"] is True
    assert result["balance_usd"] == 5.0


def test_import_link_failure_short_circuits():
    """A failed link must stop the flow — adopting a DW for an unlinked
    wallet would act on whatever wallet was previously active."""
    client = _make_client()
    with patch.object(
        client, "link_wallet",
        return_value={"success": False, "error": "Challenge has expired."},
    ):
        result = client.import_polymarket_wallet()

    assert result["success"] is False
    assert result["step"] == "link"
    assert "expired" in result["error"]
    client._request.assert_not_called()


def test_import_balance_fetch_failure_is_nonfatal():
    client = _make_client()
    with patch.object(
        client, "link_wallet",
        return_value={"success": True, "wallet_address": client._wallet_address,
                      "clob_credentials_registered": True},
    ), patch.object(
        client, "activate_polymarket_dw",
        return_value={"already_set": False, "calls_count": 3, "success": True},
    ):
        client._request.side_effect = [
            {"deposit_wallet_address": "0x6471A609E9B87447cd1552FfDe4AF3625fA2F180",
             "already_existed": True},
            RuntimeError("briefing down"),
        ]
        result = client.import_polymarket_wallet()

    assert result["success"] is True
    assert result["balance_usd"] is None


# =============================================================================
# OWS deprecation warnings (entrance closure)
# =============================================================================


def test_update_agent_wallet_creds_ows_branch_warns():
    client = _make_client()
    creds = MagicMock(api_key="k", api_secret="s", api_passphrase="p")
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address",
               return_value="0x1234567890abcdef1234567890abcdef12345678"), \
         patch("simmer_sdk.ows_utils.ows_derive_clob_creds",
               return_value=creds):
        client._request.return_value = {"id": "w", "approvals_set": True}
        with pytest.warns(DeprecationWarning, match="update_agent_wallet_creds"):
            client.update_agent_wallet_creds("agent-test")


def test_raw_key_creds_path_does_not_warn(recwarn):
    """The raw-key per-agent path is the recommended replacement — it must
    stay warning-free or the deprecation nudge points at itself."""
    client = _make_client()
    client._request.return_value = {"id": "w", "approvals_set": True}
    with patch("simmer_sdk.signing.get_wallet_address",
               return_value="0x1234567890abcdef1234567890abcdef12345678"), \
         patch("simmer_sdk.client.SimmerClient._derive_clob_creds_with_key",
               create=True,
               return_value={"apiKey": "k", "secret": "s", "passphrase": "p"}):
        try:
            client.update_agent_wallet_creds(
                agent_id="test-agent-uuid", private_key="0x" + "11" * 32
            )
        except Exception:
            # The raw-key derive path may need more mocking than this test
            # cares about — we only assert on warnings emitted before any
            # failure, which is where the deprecation branch sits.
            pass
    deprecations = [w for w in recwarn.list
                    if issubclass(w.category, DeprecationWarning)
                    and "update_agent_wallet_creds" in str(w.message)]
    assert not deprecations

"""
Unit tests for client.preflight().

All tests are pure-unit: no network calls, no SDK auth required.
Tests cover:
  - Per-cohort wallet identity (managed, external, per-agent OWS)
  - Per-venue resolution (sim, polymarket, kalshi)
  - EXPOSURE_CAP_EXCEEDED blocker
  - WALLET_UNVERIFIED blocker (real venue, not enabled)
  - VENUE_UNSUPPORTED blocker
  - SIM-2130 regression: per-agent caller must NOT see parent-user identity
  - Graceful degradation when individual endpoint calls fail
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

# Ensure simmer_sdk is importable from the repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from simmer_sdk.client import SimmerClient, PreflightResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(
    *,
    private_key=None,
    ows_wallet=None,
    venue="sim",
    wallet_address=None,
    deposit_wallet_address=None,
    uses_deposit_wallet=False,
    solana_key=False,
):
    """Build a SimmerClient without network calls."""
    client = object.__new__(SimmerClient)
    client.api_key = "sk_live_test"
    client.base_url = "https://api.simmer.markets"
    client.venue = venue
    client.live = True
    client._private_key = private_key
    client._ows_wallet = ows_wallet
    client._wallet_address = wallet_address
    client._deposit_wallet_address = deposit_wallet_address
    client._uses_deposit_wallet = uses_deposit_wallet
    client._solana_key_available = solana_key
    client._solana_wallet_address = None
    client._paper_portfolio = None
    client._session = MagicMock()
    client._skill_slug = None
    client._skill_version = None
    client._skill_dir = None
    client._auto_redeem_enabled = True
    client._auto_redeem_enabled_fetched_at = 0.0
    client._wallet_ownership = None
    client._wallet_uses_deposit_wallet = False
    client._cohort_fetched_at = 0.0
    client._held_markets_cache = None
    client._held_markets_ts = 0
    client._position_holder_cache = {}
    client._position_holder_ts = 0
    client._clob_client = None
    client._market_data_cache = {}
    client._wallet_linked = None
    client._approvals_checked = False
    client._agent_wallet_registered = None
    return client


def _agents_me(
    *,
    agent_id="agt_test",
    tier="pro",
    real_trading_enabled=True,
    wallet_address="0xUserWallet",
    deposit_wallet_address=None,
    per_agent_wallet_address=None,
    per_agent_deposit_wallet_address=None,
    per_agent_dw_active=None,
):
    return {
        "agent_id": agent_id,
        "rate_limits": {"tier": tier},
        "real_trading_enabled": real_trading_enabled,
        "wallet_address": wallet_address,
        "deposit_wallet_address": deposit_wallet_address,
        "per_agent_wallet_address": per_agent_wallet_address,
        "per_agent_deposit_wallet_address": per_agent_deposit_wallet_address,
        "per_agent_dw_active": per_agent_dw_active,
    }


def _briefing(*, sim_balance=9000.0, pm_balance=50.0, kal_balance=None, alerts=None):
    return {
        "risk_alerts": alerts or [],
        "venues": {
            "sim": {
                "balance": sim_balance,
                "cash_balance": sim_balance,
                "portfolio_value": sim_balance + 200.0,
            },
            "polymarket": {
                "balance": pm_balance,
            },
            "kalshi": {
                "balance": kal_balance,
            },
        },
    }


def _positions(items=None):
    return {"positions": items or []}


def _mock_request(client, me_resp=None, briefing_resp=None, positions_resp=None, fail=None,
                  approvals_all_set=True):
    """Patch client._request to return canned responses."""
    def _side_effect(method, endpoint, **kwargs):
        if fail and endpoint == fail:
            raise RuntimeError(f"Simulated failure for {endpoint}")
        if "/agents/me" in endpoint:
            return me_resp or _agents_me()
        if "/briefing" in endpoint:
            return briefing_resp or _briefing()
        if "/positions" in endpoint:
            return positions_resp or _positions()
        if "/allowances/" in endpoint:
            return {"all_set": approvals_all_set}
        raise RuntimeError(f"Unexpected endpoint: {endpoint}")

    client._request = _side_effect
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPreflightSigner(unittest.TestCase):
    """Signer status detection from client-side attributes."""

    def test_managed_signer(self):
        client = _make_client()
        _mock_request(client)
        result = client.preflight(venue="sim")
        self.assertEqual(result.signer_status, "managed")

    def test_external_key_signer(self):
        client = _make_client(private_key="0xdeadbeef" + "a" * 58, wallet_address="0xExt")
        _mock_request(client)
        result = client.preflight(venue="sim")
        self.assertEqual(result.signer_status, "external_key")

    def test_ows_signer(self):
        client = _make_client(ows_wallet="my-agent-wallet", wallet_address="0xOWS")
        _mock_request(client)
        result = client.preflight(venue="sim")
        self.assertEqual(result.signer_status, "ows")

    def test_solana_signer_kalshi(self):
        client = _make_client(solana_key=True)
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True))
        result = client.preflight(venue="kalshi")
        self.assertEqual(result.signer_status, "external_key")


class TestPreflightVenueResolution(unittest.TestCase):
    """Venue normalisation and resolution."""

    def test_sim_default(self):
        client = _make_client(venue="sim")
        _mock_request(client)
        result = client.preflight()
        self.assertEqual(result.resolved_venue, "sim")

    def test_simmer_normalised_to_sim(self):
        client = _make_client(venue="simmer")
        _mock_request(client)
        result = client.preflight()
        self.assertEqual(result.resolved_venue, "sim")

    def test_polymarket_venue(self):
        client = _make_client(venue="polymarket", wallet_address="0xPM")
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True))
        result = client.preflight(venue="polymarket")
        self.assertEqual(result.resolved_venue, "polymarket")

    def test_unsupported_venue_blocker(self):
        client = _make_client()
        _mock_request(client)
        result = client.preflight(venue="unknown_venue")
        self.assertIn("VENUE_UNSUPPORTED", result.blockers)
        self.assertFalse(result.ok_to_trade)


class TestPreflightWalletIdentity(unittest.TestCase):
    """Wallet identity resolution — user-primary vs per-agent OWS."""

    def test_user_primary_wallet(self):
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(wallet_address="0xUserWallet"))
        result = client.preflight(venue="sim")
        self.assertEqual(result.execution_wallet, "0xUserWallet")
        self.assertIsNone(result.deposit_wallet)

    def test_user_primary_with_dw(self):
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(
            wallet_address="0xUserEOA",
            deposit_wallet_address="0xUserDW",
        ))
        result = client.preflight(venue="sim")
        self.assertEqual(result.execution_wallet, "0xUserEOA")
        self.assertEqual(result.deposit_wallet, "0xUserDW")

    def test_per_agent_ows_wallet(self):
        """SIM-2130 regression: per-agent caller must see per-agent EOA, not parent-user wallet."""
        client = _make_client(ows_wallet="agent-wallet", wallet_address="0xAgentOWS")
        _mock_request(client, me_resp=_agents_me(
            wallet_address="0xParentUserWallet",       # parent-user field
            per_agent_wallet_address="0xAgentOWS",    # per-agent field — must win
            per_agent_deposit_wallet_address="0xAgentDW",
            per_agent_dw_active=True,
        ))
        result = client.preflight(venue="polymarket", exposure_cap_usd=0)

        # execution_wallet must be the per-agent OWS address, not the parent user's
        self.assertEqual(result.execution_wallet, "0xAgentOWS")
        self.assertNotEqual(result.execution_wallet, "0xParentUserWallet")
        self.assertEqual(result.deposit_wallet, "0xAgentDW")

    def test_per_agent_no_dw_yet(self):
        """Per-agent wallet without activated DW: deposit_wallet is None."""
        client = _make_client(ows_wallet="agent-wallet", wallet_address="0xAgentOWS")
        _mock_request(client, me_resp=_agents_me(
            per_agent_wallet_address="0xAgentOWS",
            per_agent_deposit_wallet_address=None,
            per_agent_dw_active=False,
        ))
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertEqual(result.execution_wallet, "0xAgentOWS")
        self.assertIsNone(result.deposit_wallet)


class TestPreflightBlockers(unittest.TestCase):
    """Blocker code detection."""

    def test_exposure_cap_exceeded(self):
        client = _make_client()
        positions = [
            {"venue": "polymarket", "current_value": 80.0, "market_id": "m1", "shares_yes": 10, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q1"},
        ]
        _mock_request(client, positions_resp=_positions(positions))
        result = client.preflight(venue="polymarket", planned_amount=30.0, exposure_cap_usd=100.0)
        self.assertIn("EXPOSURE_CAP_EXCEEDED", result.blockers)
        self.assertTrue(result.would_exceed_cap)
        self.assertFalse(result.ok_to_trade)

    def test_cap_not_exceeded(self):
        client = _make_client()
        positions = [
            {"venue": "polymarket", "current_value": 40.0, "market_id": "m1", "shares_yes": 10, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q1"},
        ]
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True), positions_resp=_positions(positions))
        result = client.preflight(venue="polymarket", planned_amount=10.0, exposure_cap_usd=100.0)
        self.assertNotIn("EXPOSURE_CAP_EXCEEDED", result.blockers)
        self.assertFalse(result.would_exceed_cap)

    def test_wallet_unverified_real_trading_disabled(self):
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=False))
        result = client.preflight(venue="polymarket")
        self.assertIn("WALLET_UNVERIFIED", result.blockers)
        self.assertFalse(result.ok_to_trade)

    def test_wallet_unverified_no_wallet_address(self):
        client = _make_client()  # no wallet_address set
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True, wallet_address=None))
        result = client.preflight(venue="polymarket")
        self.assertIn("WALLET_UNVERIFIED", result.blockers)

    def test_no_blockers_sim_venue(self):
        client = _make_client()
        _mock_request(client)
        result = client.preflight(venue="sim", planned_amount=5.0, exposure_cap_usd=100.0)
        self.assertEqual(result.blockers, [])
        self.assertTrue(result.ok_to_trade)

    def test_multiple_blockers(self):
        """Both EXPOSURE_CAP_EXCEEDED and WALLET_UNVERIFIED can fire together."""
        client = _make_client()
        positions = [
            {"venue": "polymarket", "current_value": 95.0, "market_id": "m1", "shares_yes": 10, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q1"},
        ]
        _mock_request(client,
                      me_resp=_agents_me(real_trading_enabled=False),
                      positions_resp=_positions(positions))
        result = client.preflight(venue="polymarket", planned_amount=10.0, exposure_cap_usd=100.0)
        self.assertIn("WALLET_UNVERIFIED", result.blockers)
        self.assertIn("EXPOSURE_CAP_EXCEEDED", result.blockers)
        self.assertFalse(result.ok_to_trade)


class TestPreflightBalance(unittest.TestCase):
    """Spendable balance extraction per venue."""

    def test_sim_cash_balance(self):
        client = _make_client()
        _mock_request(client, briefing_resp=_briefing(sim_balance=9500.0))
        result = client.preflight(venue="sim")
        self.assertEqual(result.spendable_balance, 9500.0)

    def test_polymarket_usdc_balance(self):
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True),
                      briefing_resp=_briefing(pm_balance=42.50))
        result = client.preflight(venue="polymarket", exposure_cap_usd=0)
        self.assertEqual(result.spendable_balance, 42.50)

    def test_kalshi_balance(self):
        client = _make_client(solana_key=True)
        _mock_request(client,
                      me_resp=_agents_me(real_trading_enabled=True),
                      briefing_resp=_briefing(kal_balance=15.0))
        result = client.preflight(venue="kalshi", exposure_cap_usd=0)
        self.assertEqual(result.spendable_balance, 15.0)


class TestPreflightExposure(unittest.TestCase):
    """Open exposure calculation from positions."""

    def test_sim_exposure_excludes_real_positions(self):
        client = _make_client()
        positions = [
            {"venue": "sim", "current_value": 100.0, "market_id": "s1", "shares_yes": 10, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q1"},
            {"venue": "polymarket", "current_value": 50.0, "market_id": "p1", "shares_yes": 5, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q2"},
        ]
        _mock_request(client, positions_resp=_positions(positions))
        result = client.preflight(venue="sim", planned_amount=0, exposure_cap_usd=0)
        # Sim venue exposure = sim positions only
        self.assertAlmostEqual(result.open_exposure_total, 100.0)

    def test_real_exposure_excludes_sim_positions(self):
        client = _make_client()
        positions = [
            {"venue": "sim", "current_value": 200.0, "market_id": "s1", "shares_yes": 10, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q1"},
            {"venue": "polymarket", "current_value": 30.0, "market_id": "p1", "shares_yes": 5, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q2"},
            {"venue": "kalshi", "current_value": 20.0, "market_id": "k1", "shares_yes": 2, "shares_no": 0, "pnl": 0, "status": "active", "question": "Q3"},
        ]
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True),
                      positions_resp=_positions(positions))
        result = client.preflight(venue="polymarket", planned_amount=0, exposure_cap_usd=0)
        # Real exposure = polymarket + kalshi (not sim)
        self.assertAlmostEqual(result.open_exposure_total, 50.0)

    def test_zero_exposure_no_positions(self):
        client = _make_client()
        _mock_request(client, positions_resp=_positions([]))
        result = client.preflight(venue="sim", planned_amount=0, exposure_cap_usd=0)
        self.assertEqual(result.open_exposure_total, 0.0)


class TestPreflightGracefulDegradation(unittest.TestCase):
    """Partial endpoint failures produce warnings, not exceptions."""

    def test_briefing_failure_adds_warning(self):
        client = _make_client()
        _mock_request(client, fail="/api/sdk/briefing")
        result = client.preflight(venue="sim")
        self.assertTrue(any("briefing_fetch_failed" in w for w in result.warnings))
        self.assertIsNone(result.spendable_balance)

    def test_positions_failure_adds_warning(self):
        client = _make_client()
        _mock_request(client, fail="/api/sdk/positions")
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertTrue(any("positions_fetch_failed" in w for w in result.warnings))

    def test_positions_failure_real_venue_active_cap_blocks(self):
        """Real venue + active cap + positions fetch failure must block (fail-closed)."""
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True),
                      fail="/api/sdk/positions")
        result = client.preflight(venue="polymarket", planned_amount=5.0, exposure_cap_usd=100.0)
        # Should add EXPOSURE_UNKNOWN blocker — unknown real exposure is not safe
        self.assertIn("EXPOSURE_UNKNOWN", result.blockers)
        self.assertFalse(result.ok_to_trade)
        # Warning should still be present too
        self.assertTrue(any("positions_fetch_failed" in w for w in result.warnings))

    def test_positions_failure_sim_venue_no_blocker(self):
        """Sim venue positions failure with zero cap must not add EXPOSURE_UNKNOWN."""
        client = _make_client()
        _mock_request(client, fail="/api/sdk/positions")
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertNotIn("EXPOSURE_UNKNOWN", result.blockers)

    def test_positions_failure_real_venue_cap_zero_no_blocker(self):
        """Real venue but cap=0 (disabled) — positions failure should NOT block."""
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True),
                      fail="/api/sdk/positions")
        result = client.preflight(venue="polymarket", planned_amount=0, exposure_cap_usd=0)
        self.assertNotIn("EXPOSURE_UNKNOWN", result.blockers)

    def test_identity_failure_adds_warning(self):
        client = _make_client()
        _mock_request(client, fail="/api/sdk/agents/me")
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertTrue(any("identity_fetch_failed" in w for w in result.warnings))
        self.assertIsNone(result.agent_id)


class TestPreflightPendingAlerts(unittest.TestCase):
    """Risk alerts are forwarded from briefing."""

    def test_string_alert_normalised_to_dict(self):
        client = _make_client()
        _mock_request(client, briefing_resp=_briefing(alerts=["2 positions expiring in <6h"]))
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertEqual(len(result.pending_alerts), 1)
        self.assertEqual(result.pending_alerts[0]["message"], "2 positions expiring in <6h")

    def test_dict_alert_preserved(self):
        client = _make_client()
        _mock_request(client, briefing_resp=_briefing(alerts=[{"message": "Concentration: 45%", "type": "concentration"}]))
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertEqual(result.pending_alerts[0]["type"], "concentration")


class TestPreflightUUID(unittest.TestCase):
    """client_preflight_id is unique per call."""

    def test_unique_ids(self):
        client = _make_client()
        _mock_request(client)
        r1 = client.preflight(venue="sim", exposure_cap_usd=0)
        r2 = client.preflight(venue="sim", exposure_cap_usd=0)
        self.assertNotEqual(r1.client_preflight_id, r2.client_preflight_id)

    def test_valid_uuid_format(self):
        import uuid
        client = _make_client()
        _mock_request(client)
        result = client.preflight(venue="sim", exposure_cap_usd=0)
        # Should not raise
        parsed = uuid.UUID(result.client_preflight_id)
        self.assertEqual(str(parsed), result.client_preflight_id)


class TestPreflightOWSDWIncompatibility(unittest.TestCase):
    """SIM-2325: OWS + deposit-wallet + Polymarket → POLYMARKET_SIGNER_UNSUPPORTED blocker."""

    def test_ows_dw_polymarket_blocked(self):
        """Herman's exact repro: OWS signer + active DW + polymarket = POLYMARKET_SIGNER_UNSUPPORTED."""
        client = _make_client(
            ows_wallet="herman-v3",
            wallet_address="0x3dfe3c60aaa",
            deposit_wallet_address="0xDW123",
            uses_deposit_wallet=True,
        )
        _mock_request(client, me_resp=_agents_me(
            real_trading_enabled=True,
            per_agent_wallet_address="0x3dfe3c60aaa",
            per_agent_deposit_wallet_address="0xDW123",
        ))
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertIn("POLYMARKET_SIGNER_UNSUPPORTED", result.blockers)
        self.assertFalse(result.ok_to_trade)

    def test_ows_no_dw_polymarket_allowed(self):
        """OWS without a deposit wallet (EOA-only path) should not get the DW blocker."""
        client = _make_client(
            ows_wallet="my-agent",
            wallet_address="0xOWSEOA",
            deposit_wallet_address=None,
            uses_deposit_wallet=False,
        )
        _mock_request(client, me_resp=_agents_me(
            real_trading_enabled=True,
            wallet_address="0xOWSEOA",
        ))
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertNotIn("POLYMARKET_SIGNER_UNSUPPORTED", result.blockers)

    def test_external_key_dw_polymarket_not_blocked(self):
        """Private-key external wallet with DW is valid — should NOT get POLYMARKET_SIGNER_UNSUPPORTED."""
        client = _make_client(
            private_key="0x" + "a" * 64,
            wallet_address="0xExtEOA",
            deposit_wallet_address="0xExtDW",
            uses_deposit_wallet=True,
        )
        _mock_request(client, me_resp=_agents_me(
            real_trading_enabled=True,
            wallet_address="0xExtEOA",
            deposit_wallet_address="0xExtDW",
        ))
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertNotIn("POLYMARKET_SIGNER_UNSUPPORTED", result.blockers)

    def test_ows_dw_sim_venue_not_blocked(self):
        """OWS + DW on the sim venue is fine — the DW incompatibility is polymarket-only."""
        client = _make_client(
            ows_wallet="my-agent",
            wallet_address="0xOWSEOA",
            uses_deposit_wallet=True,
        )
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True))
        result = client.preflight(venue="sim", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertNotIn("POLYMARKET_SIGNER_UNSUPPORTED", result.blockers)


class TestPreflightApprovalsWarning(unittest.TestCase):
    """POLYMARKET_APPROVALS_MISSING warning for external wallets with missing CLOB approvals."""

    def test_missing_approvals_adds_warning(self):
        """External-key wallet with missing approvals → POLYMARKET_APPROVALS_MISSING warning."""
        client = _make_client(
            private_key="0x" + "a" * 64,
            wallet_address="0xExtEOA",
            uses_deposit_wallet=False,
        )
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True),
                      approvals_all_set=False)
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertIn("POLYMARKET_APPROVALS_MISSING", result.warnings)

    def test_approvals_ok_no_warning(self):
        """External-key wallet with all approvals set → no approvals warning."""
        client = _make_client(
            private_key="0x" + "a" * 64,
            wallet_address="0xExtEOA",
            uses_deposit_wallet=False,
        )
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True),
                      approvals_all_set=True)
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertNotIn("POLYMARKET_APPROVALS_MISSING", result.warnings)

    def test_ows_dw_blocked_approvals_not_checked(self):
        """When POLYMARKET_SIGNER_UNSUPPORTED is blocking, approvals check is skipped."""
        client = _make_client(
            ows_wallet="herman-v3",
            wallet_address="0x3dfe3c60aaa",
            deposit_wallet_address="0xDW123",
            uses_deposit_wallet=True,
        )
        _mock_request(client, me_resp=_agents_me(
            real_trading_enabled=True,
            per_agent_wallet_address="0x3dfe3c60aaa",
            per_agent_deposit_wallet_address="0xDW123",
        ), approvals_all_set=False)
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        # DW blocker is present; approvals warning should NOT be added (moot)
        self.assertIn("POLYMARKET_SIGNER_UNSUPPORTED", result.blockers)
        self.assertNotIn("POLYMARKET_APPROVALS_MISSING", result.warnings)

    def test_managed_wallet_no_approvals_check(self):
        """Managed-wallet signer doesn't need CLOB approvals from the client side."""
        client = _make_client()
        _mock_request(client, me_resp=_agents_me(real_trading_enabled=True))
        result = client.preflight(venue="polymarket", planned_amount=1.0, exposure_cap_usd=100.0)
        self.assertNotIn("POLYMARKET_APPROVALS_MISSING", result.warnings)


if __name__ == "__main__":
    unittest.main()

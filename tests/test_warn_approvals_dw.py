"""Tests for the once-per-session approvals warning address selection."""

from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


EOA = "0xAAAA000000000000000000000000000000000001"
DW = "0xDEAD000000000000000000000000000000000001"


def _make_client(*, uses_dw: bool) -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client._wallet_address = EOA
    client._uses_deposit_wallet = uses_dw
    client._deposit_wallet_address = DW if uses_dw else None
    client._approvals_checked = False
    client.check_approvals = MagicMock(return_value={"all_set": True})
    return client


def test_warn_approvals_checks_deposit_wallet_for_dw_users():
    client = _make_client(uses_dw=True)

    client._warn_approvals_once()

    client.check_approvals.assert_called_once_with(address=DW)


def test_warn_approvals_checks_eoa_for_non_dw_users():
    client = _make_client(uses_dw=False)

    client._warn_approvals_once()

    client.check_approvals.assert_called_once_with(address=EOA)

"""Tests for the once-per-session approvals warning address selection."""

import logging
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


def test_warn_approvals_points_dw_users_at_activate_polymarket_dw(caplog):
    """DW users must NOT be told to run set_approvals().

    `set_approvals()` signs from the EOA, which holds nothing for a
    deposit-wallet user — the warning would send them to a no-op while they
    are debugging a real rejection (SIM-4344 / SIM-4351).
    """
    client = _make_client(uses_dw=True)
    client.check_approvals = MagicMock(return_value={"all_set": False})

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        client._warn_approvals_once()

    msg = caplog.text
    assert "activate_polymarket_dw()" in msg
    assert "set_approvals()" not in msg


def test_warn_approvals_points_eoa_users_at_set_approvals(caplog):
    client = _make_client(uses_dw=False)
    client.check_approvals = MagicMock(return_value={"all_set": False})

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        client._warn_approvals_once()

    msg = caplog.text
    assert "set_approvals()" in msg
    assert "activate_polymarket_dw()" not in msg

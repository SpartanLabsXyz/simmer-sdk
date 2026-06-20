"""Self-test for the SDK skill-example bind checker.

Runs against the editable-installed simmer-sdk so it both exercises the checker's
logic and confirms the checker stays correct as the SDK evolves.
"""
from pathlib import Path

import pytest

import check_skill_examples as chk  # scripts/ is on sys.path when pytest runs this file

SimmerClient = pytest.importorskip("simmer_sdk").SimmerClient
CLASSES = {"SimmerClient": SimmerClient}


def _violations(code: str, *, seed_defaults: bool = True):
    block = chk.Block(path=Path("test.md"), start_line=1, code=code, floor_override=None)
    return chk.check_block(block, CLASSES, "local", seed_defaults)


def test_good_trade_binds():
    assert _violations('client.trade(market_id="x", side="yes", amount=1.0)') == []


def test_unexpected_kwarg_is_flagged():
    v = _violations('client.trade(market_id="x", side="yes", outcome="YES", amount=1.0)')
    assert len(v) == 1
    assert "outcome" in v[0].reason


def test_dry_run_kwarg_is_flagged():
    v = _violations('client.trade(market_id="x", side="yes", amount=1.0, dry_run=True)')
    assert len(v) == 1
    assert "dry_run" in v[0].reason


def test_missing_method_is_flagged():
    v = _violations("client.no_such_method()")
    assert len(v) == 1
    assert "no attribute" in v[0].reason


def test_foreign_client_block_is_suppressed():
    code = "from py_clob_client_v2.clob_types import OrderArgs\nclient.create_order(args)"
    assert _violations(code, seed_defaults=True) == []


def test_non_simmer_file_is_not_seeded():
    assert _violations("client.create_order(args)", seed_defaults=False) == []


def test_explicit_assignment_always_maps():
    code = 'c = SimmerClient(api_key="x")\nc.trade(market_id="m", side="yes", bogus=1)'
    v = _violations(code, seed_defaults=False)
    assert len(v) == 1
    assert "bogus" in v[0].reason


def test_kwargs_splat_is_not_flagged():
    assert _violations("client.trade(**params)") == []


def test_syntax_fragment_is_skipped():
    assert _violations("client.trade(market_id=") == []

"""Tests for the Shock Ladder trader I/O layer (dry-run flow, no network).

Uses a FakeClient to exercise poll/filter/plan/dry-run-place/delete without the
SDK or a live server. Live execution (real order placement, fill polling) is not
unit-tested here — that needs a market and is validated in the live test.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shock_ladder import DEFAULT_ALLOWED_FAVORITISM  # noqa: E402
import shock_ladder_trader as t  # noqa: E402


def _args(**over):
    base = dict(live=False, once=True, venue="polymarket", stake=15.0, ttl=60.0,
                exit_cents=4.0, interval=2.0, buckets=None)
    base.update(over)
    return argparse.Namespace(**base)


def _signal(**over):
    s = {
        "type": "shock_ladder", "market_id": "mkt-1", "polymarket_token_id": "0xtok",
        "pre_price": 0.82, "side": "YES",
        "bucket_key": "deep|moderate|top-heavy|late|close",
        "percentile_depths": {"p50": 6.0, "p75": 9.0, "p90": 13.0, "p95": 18.0},
        "depth_source": "observed", "observed_count": 7,
        "ts": 1750000000.0, "shock_id": "mkt-1:1750000000.0",
    }
    s.update(over)
    return s


class FakeClient:
    """Records _request calls; returns canned pending signals; never trades."""
    def __init__(self, pending=None):
        self._pending = pending or []
        self.deleted = []
        self.traded = []

    def _request(self, method, path, **kw):
        if method == "GET" and path == "/api/sdk/reactor/pending":
            return {"reactor_signals": self._pending}
        if method == "DELETE" and "/pending/" in path:
            self.deleted.append(path.rsplit("/", 1)[-1])
            return {"ok": True}
        return {}

    def trade(self, **kw):  # must NOT be called in dry-run
        self.traded.append(kw)
        raise AssertionError("trade() called in dry-run")


# --- config ---

def test_config_defaults_moderate_filter_and_stake():
    cfg = t.Config(_args())
    assert cfg.allowed_favoritism == DEFAULT_ALLOWED_FAVORITISM
    assert cfg.stake == 15.0 and cfg.mode == "DRY-RUN"
    assert cfg.live is False


def test_config_empty_buckets_disables_filter():
    cfg = t.Config(_args(buckets=""))
    assert cfg.allowed_favoritism == frozenset()  # act on all


def test_config_custom_buckets_and_live():
    cfg = t.Config(_args(buckets="moderate,slight", live=True, stake=25.0))
    assert cfg.allowed_favoritism == frozenset({"moderate", "slight"})
    assert cfg.mode == "LIVE" and cfg.stake == 25.0


# --- poll filter ---

def test_poll_pending_filters_to_shock_ladder_type():
    client = FakeClient(pending=[
        _signal(),
        {"type": "copytrade", "tx_hash": "0xabc"},          # must be filtered out
        _signal(shock_id="mkt-2:2", market_id="mkt-2"),
    ])
    out = t.poll_pending(client)
    assert len(out) == 2
    assert all(s["type"] == "shock_ladder" for s in out)


# --- dry-run flow (no trades, signal deleted) ---

def test_process_signal_dry_run_no_trades_and_deletes():
    client = FakeClient()
    cfg = t.Config(_args())
    out = t.process_signal(client, _signal(), cfg)
    assert out.startswith("handled:")
    assert client.traded == []                       # dry-run never trades
    assert client.deleted == ["mkt-1:1750000000.0"]  # signal cleaned up


def test_process_signal_skips_filtered_bucket_and_deletes():
    client = FakeClient()
    cfg = t.Config(_args())  # default moderate-only
    out = t.process_signal(client, _signal(bucket_key="deep|heavy|top-heavy|late|close"), cfg)
    assert out == "skipped:filtered_bucket"
    assert client.traded == []
    assert client.deleted == ["mkt-1:1750000000.0"]  # skipped signals are deleted, not retried


def test_process_signal_skips_malformed_and_deletes():
    client = FakeClient()
    cfg = t.Config(_args())
    out = t.process_signal(client, _signal(pre_price=None), cfg)
    assert out == "skipped:malformed"
    assert client.deleted == ["mkt-1:1750000000.0"]


def test_run_once_handles_multiple_and_reports_count():
    client = FakeClient(pending=[_signal(), _signal(shock_id="s2", market_id="m2")])
    cfg = t.Config(_args())
    n = t.run_once(client, cfg)
    assert n == 2
    assert set(client.deleted) == {"mkt-1:1750000000.0", "s2"}


def test_run_once_empty_is_noop():
    client = FakeClient(pending=[])
    assert t.run_once(client, t.Config(_args())) == 0
    assert client.deleted == []

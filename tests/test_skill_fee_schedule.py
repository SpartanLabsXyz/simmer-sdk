import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
import socket

import pytest

ROOT = Path(__file__).resolve().parents[1]
BEFORE = datetime(2026, 11, 20, 23, 58, 59, 999999, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)


def load_skill(slug, filename):
    spec = importlib.util.spec_from_file_location('fee_test_' + filename, ROOT / 'skills' / slug / (filename + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('money proof must not use live network')
    monkeypatch.setattr(socket, 'socket', forbidden)


def run_fastloop(monkeypatch, at, *, legacy_bps=0, venue_curve=None, asset='BTC', window='5m'):
    skill = load_skill('polymarket-fast-loop', 'fastloop_trader')
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    monkeypatch.setattr(skill, 'datetime', Clock)
    monkeypatch.setattr(skill, 'ASSET', asset)
    monkeypatch.setattr(skill, 'WINDOW', window)
    monkeypatch.setattr(skill, 'ENTRY_THRESHOLD', .05)
    monkeypatch.setattr(skill, 'USE_FAIR_VALUE', False)
    monkeypatch.setattr(skill, 'ENABLE_NEWS_VETO', False)
    monkeypatch.setattr(skill, 'get_client', lambda **kwargs: Mock(auto_redeem=lambda: []))
    market = {'market_id': 'fake-market', 'question': 'Fake up/down market',
              'end_time': at + timedelta(minutes=4), 'is_live_now': True,
              'spread_cents': 1, 'clob_token_ids': ['fake-yes', 'fake-no'],
              'condition_id': 'fake-condition', 'fee_rate_bps': legacy_bps}
    monkeypatch.setattr(skill, 'discover_fast_market_markets', lambda *args: [market])
    monkeypatch.setattr(skill, 'get_positions', lambda: [])
    monkeypatch.setattr(skill, '_load_daily_spend', lambda *args: {'spent': 0, 'trades': 0})
    monkeypatch.setattr(skill, 'fetch_live_prices', lambda *args: .5)
    monkeypatch.setattr(skill, '_api_request', lambda url, **kwargs: venue_curve if '/clob-markets/' in url else None)
    monkeypatch.setattr(skill, 'get_momentum', lambda *args: {
        'price_now': 101, 'price_then': 100, 'momentum_pct': 1,
        'direction': 'up', 'volume_ratio': 1})
    execute = Mock(return_value={'success': True, 'simulated': True, 'shares_bought': 10})
    monkeypatch.setattr(skill, 'execute_trade', execute)
    skill.run_fast_market_strategy(dry_run=True)
    return execute.call_count


@pytest.mark.parametrize('at,expected', [(BEFORE, 0), (AFTER, 1)])
@pytest.mark.parametrize('legacy_bps', [0, 700, 1000])
def test_real_fastloop_gate_uses_schedule_even_when_legacy_lookup_missing(monkeypatch, at, expected, legacy_bps):
    assert run_fastloop(monkeypatch, at, legacy_bps=legacy_bps) == expected


def test_real_fastloop_gate_prefers_explicit_venue_curve(monkeypatch):
    assert run_fastloop(monkeypatch, AFTER, venue_curve={'fd': {'r': .09, 'e': 1}}) == 0


def test_unclassified_fastloop_market_fails_closed(monkeypatch):
    assert run_fastloop(monkeypatch, AFTER, asset='UNKNOWN', window='unknown') == 0


@pytest.mark.parametrize('slug,filename', [
    ('polymarket-fast-loop', 'fastloop_trader'), ('polymarket-mert-sniper', 'mert_sniper')])
def test_failed_legacy_lookup_is_unknown(monkeypatch, slug, filename):
    skill = load_skill(slug, filename)
    request = '_api_request' if filename == 'fastloop_trader' else '_clob_request'
    monkeypatch.setattr(skill, request, lambda *args, **kwargs: None)
    assert skill._lookup_fee_rate('fake-token') is None
    monkeypatch.setattr(skill, request, lambda *args, **kwargs: {'base_fee': 0})
    assert skill._lookup_fee_rate('fake-token') is None


def run_mert(monkeypatch, at, *, category='crypto', slug='btc-updown-5m-1795219200', curve=None):
    skill = load_skill('polymarket-mert-sniper', 'mert_sniper')
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    monkeypatch.setattr(skill, 'datetime', Clock)
    monkeypatch.setattr(skill, 'MIN_EDGE', .034)
    monkeypatch.setattr(skill, 'FEE_BUFFER', .02)
    monkeypatch.setattr(skill, 'ENABLE_NEWS_VETO', False)
    monkeypatch.setattr(skill, 'get_client', lambda **kwargs: Mock(auto_redeem=lambda: []))
    market = {'id': 'fake-market', 'question': 'Fake market', 'category': category,
              'slug': slug, 'polymarket_id': 'raw-condition-id',
              'polymarket_token_id': 'fake-token',
              'resolves_at': (at + timedelta(minutes=4)).isoformat()}
    monkeypatch.setattr(skill, 'fetch_markets', lambda *args: [market])
    monkeypatch.setattr(skill, 'fetch_live_midpoint', lambda *args: .6)
    monkeypatch.setattr(skill, 'fetch_orderbook_summary', lambda *args: {
        'spread_pct': .02, 'ask_depth_usd': 1000, 'bid_depth_usd': 1000})
    calls = []
    def request(url):
        calls.append(url)
        return curve if '/clob-markets/raw-condition-id' in url else None
    monkeypatch.setattr(skill, '_clob_request', request)
    execute = Mock(return_value={'success': True, 'simulated': True, 'shares_bought': 10})
    monkeypatch.setattr(skill, 'execute_trade', execute)
    skill.run_mert_strategy(dry_run=True, use_safeguards=False)
    return execute.call_count, calls


@pytest.mark.parametrize('at,expected', [(BEFORE, 0), (AFTER, 1)])
def test_mert_entry_fee_gate_changes(monkeypatch, at, expected):
    trades, _ = run_mert(monkeypatch, at)
    assert trades == expected


def test_mert_raw_condition_id_reaches_venue_curve(monkeypatch):
    trades, calls = run_mert(monkeypatch, AFTER, curve={'fd': {'r': .09, 'e': 1}})
    assert trades == 0
    assert any('/clob-markets/raw-condition-id' in url for url in calls)


def test_mert_unknown_category_skips_even_advisory_edge(monkeypatch):
    trades, _ = run_mert(monkeypatch, AFTER, category=None, slug=None)
    assert trades == 0


def test_replay_uses_harness_clock_and_never_live_fee_lookup(monkeypatch):
    monkeypatch.setenv('SIMMER_REPLAY', '1')
    monkeypatch.setenv('SIMMER_REPLAY_NOW', BEFORE.isoformat())
    assert run_fastloop(monkeypatch, AFTER, venue_curve={'fd': {'r': 0, 'e': 1}}) == 0
    trades, calls = run_mert(monkeypatch, AFTER, curve={'fd': {'r': 0, 'e': 1}})
    assert trades == 0
    assert calls == []


def run_scaler(monkeypatch, at, asset='BTC'):
    skill = load_skill('polymarket-fast-scaler', 'fast_scaler')
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    monkeypatch.setattr(skill, 'datetime', Clock)
    monkeypatch.setattr(skill, 'ASSET', asset)
    monkeypatch.setattr(skill, 'WINDOW', '5m')
    monkeypatch.setattr(skill, 'get_client', lambda **kwargs: Mock())
    monkeypatch.setattr(skill, '_should_run_auto_redeem', lambda: False)
    monkeypatch.setattr(skill, 'get_positions', lambda: [])
    monkeypatch.setattr(skill, '_load_daily_spend', lambda: {'spent': 0, 'trades': 0})
    market = {'market_id': 'fake-market', 'question': 'Fake up/down market',
              'end_time': at + timedelta(minutes=4), 'is_live_now': True,
              'spread_cents': 1, 'clob_token_ids': ['fake-yes', 'fake-no']}
    monkeypatch.setattr(skill, 'discover_fast_markets', lambda *args: [market])
    monkeypatch.setattr(skill, 'fetch_live_midpoint', lambda *args: .5)
    monkeypatch.setattr(skill, 'get_binance_1m_momentum', lambda *args: {
        'price_open': 100, 'price_now': 100.15, 'momentum_pct': .15,
        'abs_momentum_pct': .15, 'direction': 'up', 'volume_ratio': 1})
    execute = Mock(return_value={'success': True, 'simulated': True, 'shares_bought': 6})
    monkeypatch.setattr(skill, 'execute_buy', execute)
    skill.run_fast_scaler(dry_run=True)
    return execute.call_count


@pytest.mark.parametrize('at,fee', [(BEFORE, '$0.0175/share'), (AFTER, '$0.0125/share')])
def test_scaler_reports_dated_price_dependent_fee(monkeypatch, capsys, at, fee):
    assert run_scaler(monkeypatch, at) == 1
    assert fee in capsys.readouterr().out


def test_other_crypto_fee_and_unknown_scaler(monkeypatch, capsys):
    assert run_scaler(monkeypatch, AFTER, 'ETH') == 1
    assert '$0.0025/share' in capsys.readouterr().out
    assert run_scaler(monkeypatch, AFTER, 'UNKNOWN') == 0


def test_unsupported_venue_curve_blocks_instead_of_using_schedule(monkeypatch):
    assert run_fastloop(monkeypatch, AFTER, venue_curve={'fd': {'r': .01, 'e': 2}}) == 0


def test_explicit_zero_venue_coefficient_is_known_free(monkeypatch):
    assert run_fastloop(monkeypatch, BEFORE, venue_curve={'fd': {'r': 0, 'e': 1}}) == 1


def test_money_trace_snapshot(monkeypatch, capsys):
    from simmer_sdk.polymarket_fees import fee_rate, weighted_volume, taker_rebate_rate
    from simmer_sdk.backtest.replay.simstate import SimState
    from simmer_sdk.backtest.replay.store import PricePoint, ReplayView

    class FakeTape:
        def price(self, market_id, at):
            return PricePoint(market_id, at, .5)

    lines = ['# Polymarket fee/rebate money trace', '',
             'Fake UTC clock, fake venue, no live HTTP. Mechanism proof only; no trading edge claim.',
             'Same BTC 5m signal, 50-cent price, 5-cent divergence, zero legacy fee sentinel.', '']
    for at in (BEFORE, AFTER):
        monkeypatch.delenv('SIMMER_REPLAY', raising=False)
        loop_trades = run_fastloop(monkeypatch, at)
        loop_output = capsys.readouterr().out
        loop_fee = next(line.strip() for line in loop_output.splitlines() if line.strip().startswith('Fee:'))
        mert_trades, _ = run_mert(monkeypatch, at)
        mert_output = capsys.readouterr().out
        mert_fee = next(line.strip() for line in mert_output.splitlines() if line.strip().startswith('Fee:'))
        scaler_trades = run_scaler(monkeypatch, at)
        scaler_output = capsys.readouterr().out
        scaler_fee = next(line.strip() for line in scaler_output.splitlines() if line.strip().startswith('Fee:'))
        coefficient = fee_rate('crypto_btc_short', at)
        sim = SimState(starting_balance=100, fee_rate=coefficient)
        fill = sim.buy(ReplayView(FakeTape(), at), 'fake-market', 'yes', 50)
        maker = load_skill('polymarket-market-maker', 'market_maker')
        monkeypatch.setattr(maker, 'get_market_mid', lambda _: .5)
        monkeypatch.setattr(maker, 'get_market_info', lambda _: {'category': 'crypto', 'slug': 'btc-updown-5m-1795219200'})
        monkeypatch.setattr(maker, 'get_inventory', lambda _: (0, 0))
        stats = maker.run_market('fake-market', {}, False, .04, 10, .3, .02,
                                 set(), at=at, maker_volume_30d=200_000)
        capsys.readouterr()
        lines += [f'## {at.isoformat()}',
                  f'Fast-loop executions: {loop_trades}. {loop_fee}',
                  f'Mert executions: {mert_trades}. {mert_fee}',
                  f'Scaler executions: {scaler_trades}. {scaler_fee}',
                  f'Replay explicit BTC coefficient: {coefficient:.2f}; 100 shares at $0.50; fee ${fill.fee:.4f}; cash ${sim.cash:.4f}.',
                  f'Taker wV for that fill: {weighted_volume(100, .5, "crypto_btc_short", at, fee_paying=True):.2f}.',
                  f'Known Gold taker rebate: {taker_rebate_rate(at, tier="Gold"):.0%}; unknown account: {taker_rebate_rate(at)}.',
                  f'Maker quoted potential fee-equivalent: ${stats["quoted_fee_equiv_usd"]:.4f}; pool fraction: {stats["maker_pool_fraction"]}; known 200k maker wV tier: {stats["maker_rebate_rate"]}.',
                  'Maker quotes are not fills; no rebate is credited.', '']
    actual = '\n'.join(lines)
    expected = ROOT / 'tests/snapshots/polymarket_fee_rebate_money_trace.md'
    assert actual == expected.read_text()

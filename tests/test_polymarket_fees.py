from datetime import datetime, timedelta, timezone
import math

import pytest

from simmer_sdk.polymarket_fees import (
    CUTOFF, classify_market, fee_rate, fee_coefficient_from_clob,
    taker_rebate_rate, maker_rebate_rate, maker_pool_fraction, weighted_volume,
)

BEFORE = datetime(2026, 11, 20, 23, 58, 59, 999999, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize('category,old,new', [
    ('crypto', .07, None), ('crypto_btc_short', .07, .05),
    ('crypto_other', .07, .01), ('sports', .05, .05), ('finance', .04, .05),
    ('tech', .04, .05), ('politics', .04, None), ('politics_elections', .04, .05),
    ('politics_other', .04, .01), ('economics', .05, .01), ('culture', .05, .01),
    ('weather', .05, .01), ('mentions', .04, .01), ('geopolitics', 0, 0),
    ('combos', .05, .05), ('other', .05, .05), (None, None, None),
    ('unclassified', None, None),
])
def test_schedule_boundary(category, old, new):
    assert CUTOFF == AFTER
    assert fee_rate(category, BEFORE) == old
    assert fee_rate(category, AFTER) == new
    assert fee_rate(category, AFTER + timedelta(days=1)) == new


@pytest.mark.parametrize('asset,window,expected', [
    ('BTC', '5m', 'crypto_btc_short'), ('BTC', '15m', 'crypto_btc_short'),
    ('BTC', '1h', 'crypto_other'), ('ETH', '15m', 'crypto_other'),
    ('SOL', '5m', 'crypto_other'), ('BTC', None, 'crypto'),
])
def test_explicit_fast_market_identity(asset, window, expected):
    assert classify_market('crypto', asset=asset, window=window, up_down=True) == expected


def test_classification_does_not_guess_from_missing_metadata():
    assert classify_market('crypto') == 'crypto'
    assert classify_market('politics') == 'politics'
    assert classify_market('politics', elections=True) == 'politics_elections'
    assert classify_market('politics', elections=False) == 'politics'
    assert classify_market('politics', elections=False, geopolitics=False) == 'politics_other'
    assert classify_market('politics', elections=True, geopolitics=True) is None
    assert classify_market('geopolitics', elections=True) == 'geopolitics'
    assert classify_market('geopolitics', combo=True) == 'combos'
    assert classify_market(None, slug='btc-updown-15m-1795219200') == 'crypto_btc_short'
    assert classify_market(None, slug='will-bitcoin-go-up') is None
    assert classify_market('unknown') is None


@pytest.mark.parametrize('payload', [None, {}, {'base_fee': 0}, {'base_fee': 1000},
    {'fd': {'r': .07}}, {'fd': {'r': .07, 'e': 2}},
    {'fd': {'r': -1, 'e': 1}}, {'fd': {'r': math.nan, 'e': 1}}])
def test_missing_or_legacy_fee_lookup_is_unknown(payload):
    assert fee_coefficient_from_clob(payload) is None


def test_venue_coefficient_has_no_bps_conversion():
    assert fee_coefficient_from_clob({'fd': {'r': .07, 'e': 1}}) == .07
    assert fee_coefficient_from_clob({'fd': {'r': 0, 'e': 1}}) == 0


@pytest.mark.parametrize('price,old,new', [(.05, 10.925, 4.75), (.5, 57.5, 25), (.95, 10.925, 4.75)])
def test_weighted_volume_price_curve(price, old, new):
    assert weighted_volume(100, price, 'crypto_btc_short', BEFORE, fee_paying=True) == pytest.approx(old)
    assert weighted_volume(100, price, 'crypto_btc_short', AFTER, fee_paying=True) == pytest.approx(new)
    assert weighted_volume(100, price, 'crypto_btc_short', AFTER, role='maker', fee_paying=True) == pytest.approx(new)


def test_weighted_volume_eligibility_and_bonuses():
    assert weighted_volume(100, .5, 'crypto', BEFORE, fee_paying=True, bonus=2) == pytest.approx(115)
    assert weighted_volume(100, .5, 'crypto_btc_short', AFTER, fee_paying=True, bonus=2) == 25
    assert weighted_volume(100, .5, 'crypto', BEFORE, fee_paying=False) == 0
    assert weighted_volume(100, .5, 'crypto', BEFORE, fee_paying=None) is None
    assert weighted_volume(100, .5, None, BEFORE, fee_paying=True) is None
    assert weighted_volume(100, .5, 'combos', BEFORE, fee_paying=True) is None
    assert weighted_volume(100, .5, 'combos', AFTER, role='maker', fee_paying=True) == 0
    assert weighted_volume(100, .5, 'geopolitics', AFTER, fee_paying=False) == 0


@pytest.mark.parametrize('volume,old,new', [
    (0, 0, 0), (1999, 0, 0), (2000, .03, 0), (20000, .08, 0),
    (199999, .08, 0), (200000, .18, .10), (1000000, .32, .25),
    (4000000, .44, .40), (10000000, .50, .50), (75000000, .50, .60),
])
def test_taker_tiers(volume, old, new):
    assert taker_rebate_rate(BEFORE, volume_30d=volume) == old
    assert taker_rebate_rate(AFTER, volume_30d=volume) == new


@pytest.mark.parametrize('volume,rate', [(0, 0), (199999, 0), (200000, .03),
    (1000000, .07), (4000000, .10), (10000000, .15)])
def test_maker_tiers_replace_pools(volume, rate):
    assert maker_rebate_rate('crypto_btc_short', BEFORE, volume_30d=volume) is None
    assert maker_pool_fraction('crypto_btc_short', BEFORE) == .2
    assert maker_pool_fraction('sports', BEFORE) == .15
    assert maker_pool_fraction('politics', BEFORE) == .25
    assert maker_pool_fraction('crypto_btc_short', AFTER) is None
    assert maker_rebate_rate('crypto_btc_short', AFTER, volume_30d=volume) == rate


def test_unknown_tiers_are_not_zero_and_removed_tiers_are_unknown():
    for at in (BEFORE, AFTER):
        assert taker_rebate_rate(at) is None
        assert taker_rebate_rate(at, tier='invented') is None
        assert taker_rebate_rate(at, volume_30d=-1) is None
        assert taker_rebate_rate(at, volume_30d=math.nan) is None
        assert maker_rebate_rate('crypto_btc_short', at) is None
        assert maker_rebate_rate('crypto_btc_short', at, tier='invented') is None
    assert taker_rebate_rate(AFTER, tier='Bronze') is None
    assert taker_rebate_rate(AFTER, tier='Silver') is None
    assert taker_rebate_rate(AFTER, tier='Gold') == .10
    assert maker_rebate_rate('crypto_btc_short', AFTER, tier='Tier 4') == .15
    assert maker_rebate_rate('combos', AFTER) == 0
    assert maker_rebate_rate('geopolitics', AFTER) == 0
    assert maker_rebate_rate(None, AFTER, tier='Tier 4') is None
    assert taker_rebate_rate(AFTER, tier='Gold', volume_30d=0) is None


def test_dates_require_timezone():
    with pytest.raises(ValueError, match='timezone'):
        fee_rate('sports', datetime(2026, 11, 20))


def test_committed_unit_evidence_fixture():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/polymarket_fee_parameters.json').read_text())
    for case in fixture['cases']:
        assert fee_coefficient_from_clob(case['payload']) == case['expected_coefficient'], case['name']


def test_contradictory_identity_and_boolean_exponent_are_unknown():
    assert classify_market('crypto', slug='btc-updown-5m-123', asset='ETH', window='5m', up_down=True) is None
    assert classify_market('crypto', slug='btc-updown-5m-123', asset='BTC', window='15m', up_down=True) is None
    assert fee_coefficient_from_clob({'fd': {'r': .07, 'e': True}}) is None


def test_replay_fee_clock_fails_closed(monkeypatch):
    from simmer_sdk.polymarket_fees import fee_time
    monkeypatch.setenv('SIMMER_REPLAY', '1')
    monkeypatch.delenv('SIMMER_REPLAY_NOW', raising=False)
    with pytest.raises(ValueError, match='SIMMER_REPLAY_NOW'):
        fee_time(AFTER)
    monkeypatch.setenv('SIMMER_REPLAY_NOW', '2026-11-20T23:59:00')
    with pytest.raises(ValueError, match='timezone'):
        fee_time(AFTER)


def test_geopolitics_cannot_earn_weighted_volume():
    assert weighted_volume(100, .5, 'geopolitics', AFTER, fee_paying=True) == 0


@pytest.mark.parametrize('category,weight', [
    ('sports', 1), ('politics', 1.3), ('finance', 1.3), ('mentions', 1.3),
    ('tech', 1.3), ('economics', 1.7), ('culture', 1.7), ('weather', 1.7),
    ('other', 1.7), ('crypto', 2.3),
])
def test_current_category_weighted_volume(category, weight):
    assert weighted_volume(100, .5, category, BEFORE, fee_paying=True) == pytest.approx(25 * weight)

"""Exercise a standalone skill without the unpublished SDK fee module."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    ("polymarket-fast-loop", "fastloop_trader.py"),
    ("polymarket-fast-scaler", "fast_scaler.py"),
    ("polymarket-market-maker", "market_maker.py"),
    ("polymarket-mert-sniper", "mert_sniper.py"),
)


def test_bundled_fee_helpers_match_canonical_source():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/sync_skill_fee_helpers.py"), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('slug,entrypoint', SKILLS)
def test_bundled_replay_controls_are_declared(monkeypatch, slug, entrypoint):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    checker = runpy.run_path(str(ROOT / 'scripts/check_skill_compliance.py'))
    findings = checker['check_skill'](ROOT / 'skills' / slug)
    assert not [f for f in findings if 'SIMMER_REPLAY' in f['message']]


@pytest.mark.parametrize("slug,entrypoint", SKILLS)
def test_standalone_skill_without_unpublished_sdk_module(tmp_path, slug, entrypoint):
    bundle = tmp_path / slug
    stage = runpy.run_path(str(ROOT / 'scripts/skill_bundle.py'))['stage_bundle']
    stage(ROOT / "skills" / slug, bundle)
    # Set this to an extracted 0.27.2 release tree to also check that SDK's APIs.
    sdk_source = os.environ.get("SIMMER_TEST_PUBLISHED_SDK", str(ROOT))
    probe = '''
import importlib.abc
import runpy
import socket
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

sys.path.insert(0, sys.argv[2])
class UnpublishedModule(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'simmer_sdk.polymarket_fees':
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, UnpublishedModule())
def no_network(*args, **kwargs):
    raise AssertionError('standalone fee test must not use network')
socket.socket = no_network
skill = runpy.run_path(sys.argv[1])
import simmer_sdk
assert simmer_sdk.__file__.startswith(sys.argv[2])
at = datetime(2026, 11, 21, tzinfo=timezone.utc)
if 'run_market' in skill:
    run = skill['run_market']
    client = Mock()
    client.trade.return_value = Mock(success=True, trade_id='test-order')
    run.__globals__.update(get_client=lambda: client, get_market_mid=lambda _: .5,
                          get_market_info=lambda _: {'category': 'sports'},
                          get_inventory=lambda _: (0, 0))
    stats = run('test-market', {}, True, .04, 10, .3, .02, set(), at=at)
    assert stats['bid_placed'] and stats['ask_placed']
    assert client.trade.call_count == 2
    run.__globals__['get_market_info'] = lambda _: {}
    client.trade.reset_mock()
    assert run('test-market', {}, True, .04, 10, .3, .02, set(), at=at)['skip_reason'] == 'fee_unknown'
    client.trade.assert_not_called()
else:
    category = skill['classify_market']('crypto', asset='BTC', window='5m', up_down=True)
    assert skill['estimate_fee_rate'](category, skill['fee_time'](at)).coefficient == .05
    assert skill['estimate_fee_rate'](None, at) is None
helper = sys.modules['_polymarket_fees']
assert helper.__file__.startswith(str(__import__('pathlib').Path(sys.argv[1]).parent))
assert 'simmer_sdk.polymarket_fees' not in sys.modules
'''
    env = {k: v for k, v in os.environ.items() if k not in ("SIMMER_REPLAY", "SIMMER_REPLAY_NOW")}
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(bundle / entrypoint), sdk_source],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

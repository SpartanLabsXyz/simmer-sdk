"""Tests for copytrading reactor mode SDK version gate (SIM-884).

Reactor mode requires simmer-sdk >= 0.9.17 because it passes `signal_data`
to `client.trade()`. Older SDKs raise TypeError on every signal and trip
the circuit breaker. The gate fails fast with an upgrade message instead.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "polymarket-copytrading"
    / "copytrading_trader.py"
)


def _load_trader_module():
    """Load copytrading_trader.py as a module without executing CLI argparse."""
    spec = importlib.util.spec_from_file_location("copytrading_trader_under_test", SKILL_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Prevent get_client() / config failures from being surfaced at import by
    # providing a dummy SIMMER_API_KEY; the module only reads env at runtime.
    spec.loader.exec_module(mod)
    return mod


def test_version_gate_exits_on_old_sdk(capsys):
    mod = _load_trader_module()
    fake_sdk = type(sys)("simmer_sdk")
    fake_sdk.__version__ = "0.9.16"
    with patch.dict(sys.modules, {"simmer_sdk": fake_sdk}):
        with pytest.raises(SystemExit) as exc:
            mod._assert_reactor_sdk_version()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "too old" in captured.out
    assert "0.9.17" in captured.out
    assert "pip install" in captured.out


def test_version_gate_passes_on_min_version():
    mod = _load_trader_module()
    fake_sdk = type(sys)("simmer_sdk")
    fake_sdk.__version__ = "0.9.17"
    with patch.dict(sys.modules, {"simmer_sdk": fake_sdk}):
        mod._assert_reactor_sdk_version()  # must not raise


def test_version_gate_passes_on_newer_version():
    mod = _load_trader_module()
    fake_sdk = type(sys)("simmer_sdk")
    fake_sdk.__version__ = "0.9.25"
    with patch.dict(sys.modules, {"simmer_sdk": fake_sdk}):
        mod._assert_reactor_sdk_version()  # must not raise


def test_version_gate_tolerates_unparseable_version(capsys):
    """Unparseable versions should warn, not exit — don't break dev installs."""
    mod = _load_trader_module()
    fake_sdk = type(sys)("simmer_sdk")
    fake_sdk.__version__ = "not-a-version"
    with patch.dict(sys.modules, {"simmer_sdk": fake_sdk}):
        mod._assert_reactor_sdk_version()  # must not raise
    captured = capsys.readouterr()
    assert "WARNING" in captured.out or "could not parse" in captured.out

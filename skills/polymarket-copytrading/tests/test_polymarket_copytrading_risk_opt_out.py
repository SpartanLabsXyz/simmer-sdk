"""Hermetic regression tests for constructor risk exits in CLI dry runs."""
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def skill(monkeypatch):
    monkeypatch.setenv("SIMMER_API_KEY", "sk_test_...")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    sdk = types.ModuleType("simmer_sdk")
    sdk.SimmerClient = Mock()
    config = types.ModuleType("simmer_sdk.skill")
    config.load_config = lambda schema, *a, **k: {name: spec["default"] for name, spec in schema.items()}
    config.get_config_path = lambda *a: Path("/tmp/unused-421.json")
    config.update_config = Mock()
    sdk.skill = config
    monkeypatch.setitem(sys.modules, "simmer_sdk", sdk)
    monkeypatch.setitem(sys.modules, "simmer_sdk.skill", config)
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    spec = importlib.util.spec_from_file_location("risk_opt_out_copytrading", Path(__file__).parents[1] / "copytrading_trader.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, sdk.SimmerClient


@pytest.mark.parametrize("live", [False, True])
def test_cli_risk_exits(skill, monkeypatch, live):
    module, constructor = skill
    monkeypatch.setattr(sys, "argv", ["trader"] + (["--live"] if live else []))
    monkeypatch.setattr(module, "run_copytrading", Mock())
    module.main()
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["process_risk_alerts"] is live


def test_reactor_keeps_risk_exits(skill, monkeypatch):
    module, constructor = skill
    monkeypatch.setattr(sys, "argv", ["trader", "--reactor", "--once"])
    monkeypatch.setattr(module, "_assert_reactor_sdk_version", Mock())
    monkeypatch.setattr(module, "_poll_reactor_once", Mock())
    module.main()
    assert constructor.call_args.kwargs["process_risk_alerts"] is True


def test_unspecified_mode_preserves_risk_exits(skill):
    module, constructor = skill
    module.get_client()
    assert constructor.call_args.kwargs["process_risk_alerts"] is True


@pytest.mark.parametrize("flags", [['--positions'], ['--positions', '--venue', 'sim']])
def test_readonly_cli_skips_risk_exits(skill, monkeypatch, flags):
    module, constructor = skill
    module._config["venue"] = "polymarket"
    constructor.return_value._request.return_value = {}
    constructor.return_value.get_positions.return_value = []
    monkeypatch.setattr(sys, "argv", ["trader"] + flags)
    module.main()
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["process_risk_alerts"] is False
    assert constructor.call_args.kwargs["venue"] == ("sim" if "sim" in flags else "polymarket")


def test_config_constructs_no_client(skill, monkeypatch):
    module, constructor = skill
    monkeypatch.setattr(sys, "argv", ["trader", "--config"])
    module.main()
    constructor.assert_not_called()


def test_direct_execute_dry_run_skips_risk_exits(skill):
    module, constructor = skill
    constructor.return_value._request.return_value = {}
    module.execute_copytrading(["0xabc"], dry_run=True, venue="polymarket")
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["process_risk_alerts"] is False
    assert constructor.call_args.kwargs["venue"] == "polymarket"

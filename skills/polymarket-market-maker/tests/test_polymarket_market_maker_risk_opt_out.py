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
    spec = importlib.util.spec_from_file_location("risk_opt_out_market_maker", Path(__file__).parents[1] / "market_maker.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, sdk.SimmerClient


@pytest.mark.parametrize("live", [False, True])
def test_cli_risk_exits(skill, monkeypatch, live):
    module, constructor = skill
    monkeypatch.setattr(sys, "argv", ["trader"] + (["--live"] if live else []))
    monkeypatch.setattr(module, "cmd_run", Mock())
    module.main()
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["process_risk_alerts"] is live


def test_unspecified_mode_preserves_risk_exits(skill):
    module, constructor = skill
    module.get_client()
    assert constructor.call_args.kwargs["process_risk_alerts"] is True


def test_status_skips_risk_exits(skill, monkeypatch, tmp_path):
    import json

    module, constructor = skill
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"markets": {"market1": {}}}))
    module._config["state_file"] = str(state_file)
    constructor.return_value.get_open_orders.return_value = {"orders": []}
    constructor.return_value.get_positions.return_value = []
    monkeypatch.setattr(sys, "argv", ["trader", "--status"])
    module.main()
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["process_risk_alerts"] is False

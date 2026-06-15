"""Offline smoke tests for the Combo Builder skill config loader. No network."""
import importlib.util
import json
import os
import sys

import pytest

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "combo_builder", os.path.join(_SKILL_DIR, "combo_builder.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_cfg(tmp_path, legs, **extra):
    p = tmp_path / "combo_config.json"
    p.write_text(json.dumps({"stake_usd": 1.0, "side": "YES", "legs": legs, **extra}))
    return str(p)


def test_valid_config_loads(tmp_path):
    mod = _load_module()
    mod.CONFIG_PATH = _write_cfg(tmp_path, [
        {"position_id": "111", "label": "A"},
        {"position_id": "222", "label": "B"},
    ])
    cfg = mod._load_config()
    assert len(cfg["legs"]) == 2
    assert cfg["stake_usd"] == 1.0


def test_too_few_legs_exits(tmp_path):
    mod = _load_module()
    mod.CONFIG_PATH = _write_cfg(tmp_path, [{"position_id": "111", "label": "A"}])
    with pytest.raises(SystemExit):
        mod._load_config()


def test_missing_position_id_exits(tmp_path):
    mod = _load_module()
    mod.CONFIG_PATH = _write_cfg(tmp_path, [
        {"position_id": "111", "label": "A"},
        {"label": "B (no id)"},
    ])
    with pytest.raises(SystemExit):
        mod._load_config()


def test_missing_config_file_exits(tmp_path):
    mod = _load_module()
    mod.CONFIG_PATH = str(tmp_path / "does_not_exist.json")
    with pytest.raises(SystemExit):
        mod._load_config()

import json

import pytest

import simmer_sdk.skill as skill


@pytest.fixture
def skill_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_SKILL_CONFIG_VALUE", raising=False)
    return tmp_path / "skill.py"


def schema(type_fn=int, default=7):
    return {
        "value": {
            "env": "TEST_SKILL_CONFIG_VALUE",
            "default": default,
            "type": type_fn,
        }
    }


def test_documented_priority_matches_behavior():
    for docstring in (skill.__doc__, skill.load_config.__doc__):
        assert "env vars > config.json > defaults" in docstring


@pytest.mark.parametrize("type_fn,env_value,expected", [(int, "12", 12), (float, "1.5", 1.5)])
def test_env_wins_and_uses_spec_type(skill_file, monkeypatch, type_fn, env_value, expected):
    skill.get_config_path(skill_file).write_text(json.dumps({"value": 3}))
    monkeypatch.setenv("TEST_SKILL_CONFIG_VALUE", env_value)

    value = skill.load_config(schema(type_fn), skill_file)["value"]

    assert value == expected
    assert type(value) is type_fn


def test_file_wins_over_default_when_env_unset(skill_file):
    skill.get_config_path(skill_file).write_text(json.dumps({"value": 3}))
    assert skill.load_config(schema(), skill_file) == {"value": 3}


def test_default_when_env_and_file_value_absent(skill_file):
    assert skill.load_config(schema(), skill_file) == {"value": 7}


@pytest.mark.parametrize(
    "env_value,expected",
    [("true", True), ("TrUe", True), ("TRUE", True), ("1", True),
     ("yes", True), ("YeS", True), ("YES", True), ("false", False),
     ("0", False), ("no", False), ("abc", False), ("", False)],
)
def test_bool_env(skill_file, monkeypatch, env_value, expected):
    monkeypatch.setenv("TEST_SKILL_CONFIG_VALUE", env_value)
    assert skill.load_config(schema(bool, default=True), skill_file) == {"value": expected}


@pytest.mark.parametrize("type_fn", [int, float])
@pytest.mark.parametrize("file_config,expected", [({"value": 3}, 3), ({}, 7)])
def test_invalid_numeric_env_falls_back(skill_file, monkeypatch, type_fn, file_config, expected):
    skill.get_config_path(skill_file).write_text(json.dumps(file_config))
    monkeypatch.setenv("TEST_SKILL_CONFIG_VALUE", "abc")
    assert skill.load_config(schema(type_fn), skill_file) == {"value": expected}


def test_corrupt_config_is_ignored(skill_file):
    skill.get_config_path(skill_file).write_text("{invalid json")
    assert skill.load_config(schema(), skill_file) == {"value": 7}


def test_update_config_merges_and_preserves_other_keys(skill_file):
    config_path = skill.get_config_path(skill_file)
    config_path.write_text(json.dumps({"value": 3, "other": "preserved"}))

    merged = skill.update_config({"value": 5, "new": 9}, skill_file)

    assert merged == {"value": 5, "other": "preserved", "new": 9}
    assert json.loads(config_path.read_text()) == merged


def test_get_config_path(skill_file):
    assert skill.get_config_path(skill_file) == skill_file.parent / "config.json"

"""Offline coverage for optional top-holder output."""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def research(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    with patch.object(sys, "stdout"):
        return importlib.import_module("research")


class FakeClient:
    def __init__(self, holders):
        self.holders = holders

    def find_markets(self, query):
        return [
            SimpleNamespace(
                question=f"Market {index}",
                volume_24h=30000 - index * 10000,
                current_probability=0.6,
                resolves_at=None,
                divergence=None,
                polymarket_condition_id=f"condition-{index}",
            )
            for index in (1, 2)
        ]

    def get_top_holders(self, condition_id, limit):
        if condition_id == "condition-1":
            if isinstance(self.holders, RuntimeError):
                raise self.holders
            return self.holders
        return []


def test_runtime_error_prints_note_and_continues(research, monkeypatch, capsys):
    monkeypatch.setattr(research, "_client", FakeClient(RuntimeError("holders API unavailable")))

    research.research_topic("topic")

    output = capsys.readouterr().out
    note = "   Top holders unavailable: holders API unavailable"
    assert note in output
    assert output.index("1. Market 1") < output.index(note) < output.index("2. Market 2")
    assert output.count("Top holders: (none available)") == 1
    assert "Retrieved 2 market(s)" in output


def test_success_prints_holders(research, monkeypatch, capsys):
    monkeypatch.setattr(research, "_client", FakeClient([
        {"outcome": "Yes", "display_name": "Alice", "amount": 1234},
        {"outcome": "No", "display_name": "Bob", "amount": 5678},
    ]))

    research.research_topic("topic")

    output = capsys.readouterr().out
    assert "\n   Top holders (YES):\n     Alice: 1,234 shares\n" in output
    assert "\n   Top holders (NO):\n     Bob: 5,678 shares\n" in output
    assert "Top holders unavailable:" not in output


def test_empty_holders_output_unchanged(research, monkeypatch, capsys):
    monkeypatch.setattr(research, "_client", FakeClient([]))

    research.research_topic("topic")

    output = capsys.readouterr().out
    assert output.count("\n   Top holders: (none available)\n") == 2
    assert "Top holders (YES):" not in output
    assert "Top holders (NO):" not in output
    assert "Top holders unavailable:" not in output

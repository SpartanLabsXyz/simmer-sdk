"""Offline storage tests; all journal files live in pytest's temporary directory."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "tradejournal", Path(__file__).resolve().parents[1] / "tradejournal.py"
)
tradejournal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tradejournal)


@pytest.fixture
def journal(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(tradejournal, "DATA_DIR", data_dir)
    monkeypatch.setattr(tradejournal, "TRADES_FILE", data_dir / "trades.json")
    monkeypatch.setattr(tradejournal, "CONTEXT_FILE", data_dir / "context.json")
    return tradejournal


@pytest.mark.parametrize("corrupt", [False, True], ids=["missing", "corrupt"])
def test_load_trades_defaults(journal, capsys, corrupt):
    if corrupt:
        journal.DATA_DIR.mkdir()
        journal.TRADES_FILE.write_text("{invalid json", encoding="utf-8")

    before = datetime.now(timezone.utc)
    data = journal.load_trades()
    after = datetime.now(timezone.utc)

    assert data == {
        "trades": [],
        "metadata": {
            "created_at": data["metadata"]["created_at"],
            "last_sync": None,
            "total_trades": 0,
        },
    }
    assert before <= datetime.fromisoformat(data["metadata"]["created_at"]) <= after
    if corrupt:
        output = capsys.readouterr().out
        assert "Warning: Could not load" in output
        assert str(journal.TRADES_FILE) in output


def test_trades_round_trip(journal):
    trades = [{"id": "trade-1", "side": "yes"}, {"id": "trade-2", "side": "no"}]
    metadata = {"created_at": "2026-01-01T00:00:00+00:00", "last_sync": None}
    data = {"trades": trades, "metadata": {**metadata, "total_trades": 99}}

    journal.save_trades(data)

    expected = {"trades": trades, "metadata": {**metadata, "total_trades": 2}}
    assert json.loads(journal.TRADES_FILE.read_text(encoding="utf-8")) == expected
    assert journal.load_trades() == expected


@pytest.mark.parametrize("corrupt", [False, True], ids=["missing", "corrupt"])
def test_load_context_defaults(journal, capsys, corrupt):
    if corrupt:
        journal.DATA_DIR.mkdir()
        journal.CONTEXT_FILE.write_text("{invalid json", encoding="utf-8")

    assert journal.load_context() == {}
    if corrupt:
        output = capsys.readouterr().out
        assert "Warning: Could not load" in output
        assert str(journal.CONTEXT_FILE) in output


def test_context_round_trip(journal):
    context = {
        "trade-1": {"source": "weather", "thesis": "Forecast", "confidence": 0.7},
        "trade-2": {"source": "manual", "thesis": None, "confidence": None},
    }

    journal.save_context(context)

    assert json.loads(journal.CONTEXT_FILE.read_text(encoding="utf-8")) == context
    assert journal.load_context() == context

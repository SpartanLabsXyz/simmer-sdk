"""Offline contracts for signal sniper's non-trading helpers."""

from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import re
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sniper(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    skill = MagicMock()
    skill.load_config.side_effect = lambda schema, file, slug=None: {
        key: spec["default"] for key, spec in schema.items()
    }
    monkeypatch.setitem(sys.modules, "simmer_sdk", MagicMock())
    monkeypatch.setitem(sys.modules, "simmer_sdk.skill", skill)
    # The CLI configures stdout at import; pytest's capture stream may not support it.
    with patch.object(sys, "stdout"):
        module = importlib.import_module("signal_sniper")

    monkeypatch.setattr(module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(module, "PROCESSED_FILE", tmp_path / "processed.json")

    def forbidden(*args, **kwargs):
        pytest.fail("Helper tests must not use network or trading paths")

    for name in (
        "fetch_rss", "discover_markets", "get_market_context", "run_scan",
        "get_client", "check_safeguards",
    ):
        monkeypatch.setattr(module, name, forbidden)
    return module


@pytest.mark.parametrize("url", [
    "http://example.com/feed",
    "https://example.com/feed",
    "https://172.32.0.1/feed",
])
def test_validate_url_accepts_public_hosts(sniper, url):
    assert sniper.validate_url(url) is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/feed",
    "https:///feed",
    "http://localhost/feed",
    "http://127.0.0.1/feed",
    "http://0.0.0.0/feed",
    "http://169.254.169.254/feed",
    "http://[::1]/feed",
    "http://10.1.2.3/feed",
    "http://192.168.1.1/feed",
    *[f"http://172.{octet}.0.1/feed" for octet in range(16, 32)],
])
def test_validate_url_rejects_unsafe_urls(sniper, url):
    assert sniper.validate_url(url) is False


@pytest.mark.parametrize(("article", "keywords", "expected"), [
    ({"title": "Unrelated news", "summary": "Other events"}, [], True),
    ({"title": "BITCOIN rises", "summary": "Other events"}, ["bitcoin"], True),
    ({"title": "Market news", "summary": "BiTcOiN rises"}, ["bitcoin"], True),
    ({"title": "Market news", "summary": "Other events"}, ["bitcoin"], False),
])
def test_matches_keywords(sniper, article, keywords, expected):
    assert sniper.matches_keywords(article, keywords) is expected


def test_article_hash_is_stable_hex_and_distinguishes_titles(sniper):
    url = "https://example.com/article"
    result = sniper.article_hash(url, "Bitcoin rises")

    assert re.fullmatch(r"[0-9a-f]{16}", result)
    assert sniper.article_hash(url, "Bitcoin rises") == result
    assert sniper.article_hash(url, "Bitcoin falls") != result


def test_load_processed_missing_file(sniper):
    assert sniper.load_processed() == {}


def test_load_processed_corrupt_json(sniper):
    sniper.PROCESSED_FILE.write_text("{invalid json", encoding="utf-8")

    assert sniper.load_processed() == {}


def test_processed_round_trip_prunes_stale_entries(sniper, tmp_path):
    now = datetime.now(timezone.utc)
    fresh = {"title": "Fresh news", "processed_at": now.isoformat()}
    stale = {"title": "Old news", "processed_at": (now - timedelta(days=8)).isoformat()}
    unparseable = {"title": "Unknown age", "processed_at": "not-a-timestamp"}

    sniper.save_processed({"fresh": fresh, "stale": stale, "unparseable": unparseable})

    assert sniper.load_processed() == {"fresh": fresh, "unparseable": unparseable}
    assert not list(tmp_path.glob("*.tmp"))

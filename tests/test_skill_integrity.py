from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from simmer_sdk.client import SimmerClient


class _Resp:
    def __init__(self, status_code: int, *, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _Session:
    def __init__(self, backend_hash: str):
        self.backend_hash = backend_hash
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("/api/sdk/skills"):
            return _Resp(
                200,
                payload={
                    "skills": [
                        {
                            "id": "polymarket-combo-builder",
                            "content_hash": self.backend_hash,
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected session request: {url}")


def _write_skill(tmp_path: Path, entrypoint_text: str) -> Path:
    skill_dir = tmp_path / "polymarket-combo-builder"
    skill_dir.mkdir()
    (skill_dir / "clawhub.json").write_text(
        json.dumps({"automaton": {"entrypoint": "combo_builder.py"}}),
        encoding="utf-8",
    )
    (skill_dir / "combo_builder.py").write_text(entrypoint_text, encoding="utf-8")
    return skill_dir


def _client(session) -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client.base_url = "https://api.simmer.example.com"
    client._skill_slug = "polymarket-combo-builder"
    client._session = session
    return client


def test_skill_integrity_allows_backend_hash_lag_when_clawhub_live_matches(
    tmp_path, monkeypatch
):
    entrypoint = "print('official current skill')\n"
    stale_hash = hashlib.sha256(b"old published content").hexdigest()
    session = _Session(backend_hash=stale_hash)
    external_requests = []

    def fake_requests_get(url, **kwargs):
        external_requests.append((url, kwargs))
        return _Resp(200, text=entrypoint)

    monkeypatch.setattr("simmer_sdk.client.requests.get", fake_requests_get)

    _client(session)._verify_skill_integrity(_write_skill(tmp_path, entrypoint))

    assert session.urls == ["https://api.simmer.example.com/api/sdk/skills"]
    assert len(external_requests) == 1
    live_url, kwargs = external_requests[0]
    assert "clawhub.ai/api/v1/skills/polymarket-combo-builder/file" in live_url
    assert kwargs == {"timeout": 5}


def test_skill_integrity_still_rejects_local_file_that_matches_neither_hash(
    tmp_path, monkeypatch
):
    entrypoint = "print('locally modified')\n"
    stale_hash = hashlib.sha256(b"old published content").hexdigest()
    session = _Session(backend_hash=stale_hash)

    def fake_requests_get(url, **kwargs):
        return _Resp(200, text="print('official current skill')\n")

    monkeypatch.setattr("simmer_sdk.client.requests.get", fake_requests_get)

    with pytest.raises(RuntimeError, match="entrypoint integrity check failed"):
        _client(session)._verify_skill_integrity(_write_skill(tmp_path, entrypoint))

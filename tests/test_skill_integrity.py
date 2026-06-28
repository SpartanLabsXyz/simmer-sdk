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
    def __init__(self, backend_hash: str, live_text: str):
        self.backend_hash = backend_hash
        self.live_text = live_text
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
        return _Resp(200, text=self.live_text)


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


def test_skill_integrity_allows_backend_hash_lag_when_clawhub_live_matches(tmp_path):
    entrypoint = "print('official current skill')\n"
    stale_hash = hashlib.sha256(b"old published content").hexdigest()
    session = _Session(backend_hash=stale_hash, live_text=entrypoint)

    _client(session)._verify_skill_integrity(_write_skill(tmp_path, entrypoint))

    assert any("clawhub.ai/api/v1/skills/polymarket-combo-builder/file" in u for u in session.urls)


def test_skill_integrity_still_rejects_local_file_that_matches_neither_hash(tmp_path):
    entrypoint = "print('locally modified')\n"
    stale_hash = hashlib.sha256(b"old published content").hexdigest()
    session = _Session(backend_hash=stale_hash, live_text="print('official current skill')\n")

    with pytest.raises(RuntimeError, match="entrypoint integrity check failed"):
        _client(session)._verify_skill_integrity(_write_skill(tmp_path, entrypoint))

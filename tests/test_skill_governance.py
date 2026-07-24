from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_skill_governance", ROOT / "scripts" / "check-skill-governance.py"
)
assert SPEC is not None
assert SPEC.loader is not None
check_skill_governance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_skill_governance
SPEC.loader.exec_module(check_skill_governance)


def skill_md(version: str) -> str:
    return f"""---
name: weather
metadata:
  author: Simmer
  version: "{version}"
---
# Weather
"""


def write_skill(tmp_path: Path, version: str) -> None:
    skill_dir = tmp_path / "skills" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md(version), encoding="utf-8")


def patch_repo(monkeypatch, tmp_path: Path, previous_version: str | None) -> None:
    monkeypatch.setattr(check_skill_governance, "ROOT", tmp_path)
    monkeypatch.setattr(check_skill_governance, "SKILLS_DIR", tmp_path / "skills")

    def fake_run_git(args: list[str]) -> str:
        if args[:1] == ["show"]:
            if previous_version is None:
                raise subprocess.CalledProcessError(128, ["git", *args])
            return skill_md(previous_version)
        raise AssertionError(f"unexpected git command: {args}")

    monkeypatch.setattr(check_skill_governance, "run_git", fake_run_git)


def test_skill_file_change_requires_metadata_version_bump(tmp_path, monkeypatch) -> None:
    write_skill(tmp_path, "1.2.3")
    patch_repo(monkeypatch, tmp_path, previous_version="1.2.3")

    errors = check_skill_governance.validate_skill_version_bumps(
        ["skills/weather/weather_trader.py"],
        "base-ref",
    )

    assert errors == [
        "skills/weather changed but SKILL.md metadata.version did not change from 1.2.3; "
        "bump the skill version so ClawHub can publish it"
    ]


def test_skill_file_change_allows_metadata_version_bump(tmp_path, monkeypatch) -> None:
    write_skill(tmp_path, "1.2.4")
    patch_repo(monkeypatch, tmp_path, previous_version="1.2.3")

    assert (
        check_skill_governance.validate_skill_version_bumps(
            ["skills/weather/weather_trader.py", "skills/weather/SKILL.md"],
            "base-ref",
        )
        == []
    )


def test_new_skill_does_not_need_prior_version_bump(tmp_path, monkeypatch) -> None:
    write_skill(tmp_path, "0.1.0")
    patch_repo(monkeypatch, tmp_path, previous_version=None)

    assert (
        check_skill_governance.validate_skill_version_bumps(
            ["skills/weather/SKILL.md", "skills/weather/clawhub.json"],
            "base-ref",
        )
        == []
    )


def test_non_skill_file_does_not_require_version_bump(tmp_path, monkeypatch) -> None:
    write_skill(tmp_path, "1.2.3")
    patch_repo(monkeypatch, tmp_path, previous_version="1.2.3")

    assert (
        check_skill_governance.validate_skill_version_bumps(
            ["README.md", "src/simmer_sdk/client.py"],
            "base-ref",
        )
        == []
    )


def test_changed_skill_requires_current_metadata_version(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "skills" / "weather"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: weather\n---\n", encoding="utf-8")
    patch_repo(monkeypatch, tmp_path, previous_version="1.2.3")

    errors = check_skill_governance.validate_skill_version_bumps(
        ["skills/weather/weather_trader.py"],
        "base-ref",
    )

    assert errors == [
        "skills/weather/SKILL.md is missing metadata.version; add a skill version "
        "so ClawHub can publish it"
    ]

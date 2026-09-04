from pathlib import Path

import check_skill_publish_parity as parity


def write_skill(root: Path, folder: str, frontmatter: str, manifest: str = "{}") -> None:
    skill_dir = root / "skills" / folder
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n# Skill\n", encoding="utf-8")
    (skill_dir / "clawhub.json").write_text(manifest, encoding="utf-8")


def test_reads_metadata_version_without_strict_yaml(tmp_path):
    write_skill(
        tmp_path,
        "market-maker",
        """name: polymarket-market-maker
description: Akey et al. (2026): market-making reduces loss probability.
metadata:
  version: "0.9.0"
""",
    )

    skills = parity.read_skills(tmp_path)

    assert skills[0].slug == "polymarket-market-maker"
    assert skills[0].version == "0.9.0"


def test_manifest_publish_false_skips_skill(tmp_path, capsys):
    write_skill(
        tmp_path,
        "research-only",
        """name: research-only
version: "0.1.0"
""",
        '{"publish": false, "publish_reason": "research-only"}',
    )
    skill = parity.read_skills(tmp_path)[0]

    assert parity.check_skill(skill, None) is True
    assert "skipped (research-only)" in capsys.readouterr().out


def test_repo_ahead_fails(tmp_path, capsys):
    write_skill(
        tmp_path,
        "preflight",
        """name: simmer-preflight
version: "0.3.2"
published: true
""",
    )
    skill = parity.read_skills(tmp_path)[0]

    assert parity.check_skill(skill, "0.3.1") is False
    assert "repo version 0.3.2 is ahead" in capsys.readouterr().out


def test_clawhub_ahead_passes(tmp_path, capsys):
    write_skill(
        tmp_path,
        "trade-journal",
        """name: prediction-trade-journal
metadata:
  version: "1.1.8"
""",
    )
    skill = parity.read_skills(tmp_path)[0]

    assert parity.check_skill(skill, "1.1.13") is True
    assert "treating as already published" in capsys.readouterr().out

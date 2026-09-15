import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "check_skill_publish_parity", ROOT / "scripts" / "check_skill_publish_parity.py"
)
assert SPEC is not None
assert SPEC.loader is not None
check_skill_publish_parity = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_skill_publish_parity
SPEC.loader.exec_module(check_skill_publish_parity)


def write_skill(root: Path, slug: str, version: str, clawhub_json: str = "{}") -> None:
    skill_dir = root / "skills" / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {slug}\nversion: {version}\n---\n\n# {slug}\n",
        encoding="utf-8",
    )
    (skill_dir / "clawhub.json").write_text(clawhub_json, encoding="utf-8")


def write_named_skill(
    root: Path, folder: str, name: str, version: str, clawhub_json: str = "{}"
) -> None:
    skill_dir = root / "skills" / folder
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\nversion: {version}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill_dir / "clawhub.json").write_text(clawhub_json, encoding="utf-8")


def test_discover_skills_skips_explicit_unpublished(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "draft-skill",
        "0.1.0",
        '{"published": false, "publish_reason": "not ready"}',
    )

    [skill] = check_skill_publish_parity.discover_skills(tmp_path)

    assert skill.slug == "draft-skill"
    assert skill.path == "skills/draft-skill"
    assert skill.version == "0.1.0"
    assert skill.published is False
    assert skill.publish_reason == "not ready"


def test_discover_skills_uses_frontmatter_name_as_clawhub_slug(tmp_path: Path) -> None:
    write_named_skill(tmp_path, "preflight", "simmer-preflight", "0.3.3")

    [skill] = check_skill_publish_parity.discover_skills(tmp_path)

    assert skill.slug == "simmer-preflight"
    assert skill.path == "skills/preflight"


def test_discover_skills_ignores_nested_metadata_name(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "shock-ladder"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: shock-ladder\n"
        "metadata:\n"
        "  name: '@source-handle'\n"
        "  version: 0.1.5\n"
        "---\n\n"
        "# Shock Ladder\n",
        encoding="utf-8",
    )
    (skill_dir / "clawhub.json").write_text("{}", encoding="utf-8")

    [skill] = check_skill_publish_parity.discover_skills(tmp_path)

    assert skill.slug == "shock-ladder"
    assert skill.version == "0.1.5"


def test_check_skills_reports_repo_ahead(monkeypatch) -> None:
    skill = check_skill_publish_parity.Skill(
        slug="demo",
        path="skills/demo",
        version="1.0.1",
        published=True,
        publish_reason=None,
    )
    monkeypatch.setattr(check_skill_publish_parity, "fetch_clawhub_latest", lambda slug: "1.0.0")

    assert check_skill_publish_parity.check_skills([skill]) == [
        "skills/demo (demo): repo 1.0.1 > ClawHub 1.0.0"
    ]


def test_check_skills_reports_registry_ahead(monkeypatch) -> None:
    skill = check_skill_publish_parity.Skill(
        slug="demo",
        path="skills/demo",
        version="1.0.0",
        published=True,
        publish_reason=None,
    )
    monkeypatch.setattr(check_skill_publish_parity, "fetch_clawhub_latest", lambda slug: "1.0.1")

    assert check_skill_publish_parity.check_skills([skill]) == [
        "skills/demo (demo): ClawHub 1.0.1 > repo 1.0.0"
    ]


def test_check_skills_reports_missing_skill(monkeypatch) -> None:
    skill = check_skill_publish_parity.Skill(
        slug="demo",
        path="skills/demo",
        version="1.0.0",
        published=True,
        publish_reason=None,
    )
    monkeypatch.setattr(check_skill_publish_parity, "fetch_clawhub_latest", lambda slug: None)

    assert check_skill_publish_parity.check_skills([skill]) == [
        "skills/demo (demo): not found on ClawHub"
    ]


def test_check_skills_ignores_unpublished(monkeypatch) -> None:
    skill = check_skill_publish_parity.Skill(
        slug="draft",
        path="skills/draft",
        version="1.0.0",
        published=False,
        publish_reason="not ready",
    )

    def fail_fetch(slug):
        raise AssertionError("unpublished skill should not be fetched")

    monkeypatch.setattr(check_skill_publish_parity, "fetch_clawhub_latest", fail_fetch)

    assert check_skill_publish_parity.check_skills([skill]) == []

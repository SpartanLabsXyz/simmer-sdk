import importlib.util
import json
import urllib.error

import pytest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK_SPEC = importlib.util.spec_from_file_location(
    "check_publish_lag", ROOT / "scripts" / "check_publish_lag.py"
)
assert CHECK_SPEC is not None
assert CHECK_SPEC.loader is not None
check_publish_lag = importlib.util.module_from_spec(CHECK_SPEC)
sys.modules[CHECK_SPEC.name] = check_publish_lag
CHECK_SPEC.loader.exec_module(check_publish_lag)

PLAN_SPEC = importlib.util.spec_from_file_location(
    "plan_package_publish", ROOT / "scripts" / "plan_package_publish.py"
)
assert PLAN_SPEC is not None
assert PLAN_SPEC.loader is not None
plan_package_publish = importlib.util.module_from_spec(PLAN_SPEC)
sys.modules[PLAN_SPEC.name] = plan_package_publish
PLAN_SPEC.loader.exec_module(plan_package_publish)


def write_package_files(root: Path, npm_version: str, pypi_version: str) -> None:
    (root / "mcp").mkdir()
    (root / "mcp" / "package.json").write_text(
        f'{{"name": "simmer-mcp", "version": "{npm_version}"}}',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "simmer-sdk"\nversion = "{pypi_version}"\n',
        encoding="utf-8",
    )


def make_args(**kwargs):
    defaults = {
        "root": None,
        "npm_published_version": None,
        "pypi_published_version": None,
    }
    defaults.update(kwargs)
    return type("Args", (), defaults)()


def test_main_outputs_package_publish_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    write_package_files(tmp_path, npm_version="3.5.2", pypi_version="0.25.9")
    monkeypatch.setattr(
        plan_package_publish,
        "parse_args",
        lambda: make_args(
            root=tmp_path,
            npm_published_version="3.5.1",
            pypi_published_version="0.25.9",
        ),
    )

    assert plan_package_publish.main() == 0

    output = capsys.readouterr().out
    assert "npm_publish_needed=true" in output
    assert "pypi_publish_needed=false" in output
    assert "clawhub_publish_needed" not in output


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not access the network")
    monkeypatch.setattr(check_publish_lag.urllib.request, "urlopen", blocked)


def write_skill(root, name="example", frontmatter="", config=None, version="1.2.0"):
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\n{frontmatter}metadata:\n  version: {version}\n---\n"
    )
    (directory / "clawhub.json").write_text(json.dumps(config or {}))
    return directory


@pytest.mark.parametrize(
    "latest,frontmatter,config,needed",
    [
        ("1.1.0", "", {}, True),
        ("1.2.0", "", {}, False),
        ("1.3.0", "", {}, False),
        ("1.1.0", "published: false\n", {}, False),
        ("1.1.0", "", {"publish": False}, False),
        ("1.1.0", "", {"published": False}, False),
        (None, "", {}, False),
        (None, "", {"first_publish": True}, True),
    ],
)
def test_skill_plan(tmp_path, monkeypatch, capsys, latest, frontmatter, config, needed):
    write_package_files(tmp_path, "1.0.0", "1.0.0")
    write_skill(tmp_path, frontmatter=frontmatter, config=config)
    versions = tmp_path / "versions.json"
    versions.write_text(json.dumps({"example": latest}))
    output_file = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setattr(sys, "argv", [
        "plan_package_publish.py", "--root", str(tmp_path),
        "--npm-published-version", "1.0.0",
        "--pypi-published-version", "1.0.0",
        "--clawhub-versions-json", str(versions),
    ])
    assert plan_package_publish.main() == 0
    outputs = dict(line.split("=", 1) for line in output_file.read_text().splitlines())
    assert outputs["skills_publish_needed"] == ("true" if needed else "false")
    assert outputs["skills_to_publish"] == (
        '["skills/example"]' if needed else "[]"
    )
    log = capsys.readouterr().out
    assert "skills/example:" in log
    if frontmatter or config.get("publish") is False or config.get("published") is False:
        assert "skipped hold flag" in log


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("https://example.invalid", 503, "unavailable", {}, None),
    urllib.error.URLError("offline"),
    TimeoutError("timed out"),
])
def test_fetch_error_skips_only_affected_skill(tmp_path, monkeypatch, capsys, error):
    write_skill(tmp_path, name="broken")
    write_skill(tmp_path, name="healthy")
    def fetch(slug):
        if slug == "broken":
            raise error
        return "1.0.0"
    monkeypatch.setattr(check_publish_lag, "fetch_clawhub_latest", fetch)
    assert plan_package_publish.plan_skills(tmp_path) == ["skills/healthy"]
    assert "::warning::" in capsys.readouterr().out


def test_404_allows_first_publish(tmp_path, monkeypatch):
    write_skill(tmp_path, config={"first_publish": True})
    def fetch(slug):
        raise urllib.error.HTTPError("https://example.invalid", 404, "missing", {}, None)
    monkeypatch.setattr(check_publish_lag, "fetch_clawhub_latest", fetch)
    assert plan_package_publish.plan_skills(tmp_path) == ["skills/example"]


@pytest.mark.parametrize("malformed", ["missing-frontmatter", "invalid-json", "invalid-version", "non-object"])
def test_malformed_skill_does_not_break_package_plan(tmp_path, monkeypatch, capsys, malformed):
    write_package_files(tmp_path, "1.0.0", "1.0.0")
    directory = write_skill(tmp_path)
    if malformed == "missing-frontmatter":
        (directory / "SKILL.md").write_text("no metadata")
    elif malformed == "invalid-json":
        (directory / "clawhub.json").write_text("{")
    elif malformed == "non-object":
        (directory / "clawhub.json").write_text("[]")
    else:
        write = directory / "SKILL.md"
        write.write_text(write.read_text().replace("1.2.0", "invalid"))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(plan_package_publish, "parse_args", lambda: make_args(
        root=tmp_path, npm_published_version="1.0.0", pypi_published_version="1.0.0",
    ))
    assert plan_package_publish.main() == 0
    output = capsys.readouterr().out
    assert "::warning::" in output
    assert "npm_publish_needed=false" in output
    assert "skills_to_publish=[]" in output

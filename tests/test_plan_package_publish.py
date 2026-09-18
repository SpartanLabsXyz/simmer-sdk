import importlib.util
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


def write_skill(root: Path, folder: str, name: str, version: str, clawhub_json: str = "{}") -> None:
    skill_dir = root / "skills" / folder
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\nmetadata:\n  version: {version}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill_dir / "clawhub.json").write_text(clawhub_json, encoding="utf-8")


def make_args(**kwargs):
    defaults = {
        "root": None,
        "npm_published_version": None,
        "pypi_published_version": None,
    }
    defaults.update(kwargs)
    return type("Args", (), defaults)()


def test_main_outputs_package_and_clawhub_publish_plan(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    write_package_files(tmp_path, npm_version="3.5.2", pypi_version="0.25.9")
    write_skill(tmp_path, "preflight", "simmer-preflight", "0.3.3")
    monkeypatch.setattr(plan_package_publish, "fetch_clawhub_latest", lambda slug: "0.3.2")
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
    assert "clawhub_publish_needed=true" in output
    assert '"slug":"simmer-preflight"' in output
    assert '"path":"skills/preflight"' in output
    assert (
        "npx -y clawhub skill publish <tmp> --owner simmer "
        "--slug simmer-preflight --version 0.3.3"
    ) in output


def test_main_skips_clawhub_hold_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    write_package_files(tmp_path, npm_version="3.5.2", pypi_version="0.25.9")
    write_skill(
        tmp_path,
        "polymarket-nothing-ever-happens",
        "polymarket-nothing-ever-happens",
        "1.1.2",
        '{"publish": false, "publish_reason": "SIM-5443 backtest hold"}',
    )

    def fail_fetch(slug):
        raise AssertionError("held skill should not query ClawHub")

    monkeypatch.setattr(plan_package_publish, "fetch_clawhub_latest", fail_fetch)
    monkeypatch.setattr(
        plan_package_publish,
        "parse_args",
        lambda: make_args(
            root=tmp_path,
            npm_published_version="3.5.2",
            pypi_published_version="0.25.9",
        ),
    )

    assert plan_package_publish.main() == 0

    output = capsys.readouterr().out
    assert "publish skipped; hold flag set (SIM-5443 backtest hold)" in output
    assert "clawhub_publish_needed=false" in output
    assert 'clawhub_publish_matrix={"include":[]}' in output

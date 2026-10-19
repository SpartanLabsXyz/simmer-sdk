"""Paper-first governance regressions; included in CI's pytest tests/ run."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "skill_governance", ROOT / "scripts/check-skill-governance.py"
)
governance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(governance)


def write_skill(root, code, path="skills/new-skill/strategy.py"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    return target


@pytest.mark.parametrize("signature", [
    "def get_client(live=True):",
    "def get_client(other=0, live: bool=True):",
    "def get_client(live=True, /):",
    "async def get_client(*, live=True):",
])
def test_reject_live_default(tmp_path, signature):
    write_skill(tmp_path, signature + "\n    pass\n")
    errors = governance.validate_live_defaults(tmp_path)
    assert len(errors) == 1
    assert "skills/new-skill/strategy.py:1" in errors[0]
    assert "default live=False; use --live" in errors[0]


@pytest.mark.parametrize("default", ["False", "1", "None"])
def test_accept_other_defaults(tmp_path, default):
    write_skill(tmp_path, f"def get_client(live={default}):\n    pass\n")
    assert governance.validate_live_defaults(tmp_path) == []


def test_updated_template():
    assert governance.validate_live_defaults(ROOT) == []
    template = (ROOT / governance.SKILL_TEMPLATE_PATH).read_text()
    assert "def get_client(live=False):" in template
    assert 'parser.add_argument("--live", action="store_true"' in template
    assert "Always default to dry-run" in template


def test_grandfathered_file(tmp_path):
    path = sorted(governance.LIVE_DEFAULT_ALLOWLIST)[0]
    write_skill(tmp_path, "def get_client(live=True):\n    pass\n", path)
    assert governance.validate_live_defaults(tmp_path) == []


def test_template_block_line_number(tmp_path):
    write_skill(tmp_path, "# Template\n\n```python\ndef get_client(live=True):\n    pass\n```\n",
                governance.SKILL_TEMPLATE_PATH)
    errors = governance.validate_live_defaults(tmp_path)
    assert len(errors) == 1
    assert f"{governance.SKILL_TEMPLATE_PATH}:4" in errors[0]


def test_syntax_error_fails_closed(tmp_path):
    write_skill(tmp_path, "def get_client(live=True):\n")
    assert "cannot parse Python" in governance.validate_live_defaults(tmp_path)[0]

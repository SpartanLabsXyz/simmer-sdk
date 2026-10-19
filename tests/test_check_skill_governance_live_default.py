"""Paper-first governance regressions; included in CI's pytest tests/ run."""
import importlib.util
from collections import Counter
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


@pytest.fixture(autouse=True)
def template(tmp_path):
    write_skill(tmp_path, "# Template\n", governance.SKILL_TEMPLATE_PATH)


@pytest.mark.parametrize("signature", [
    "def get_client(live=True):", "def get_client(other=0, live: bool=True):",
    "def get_client(live=True, /):", "async def get_client(*, live=True):",
    "def get_client(live=1):", "def get_client(live=not False):",
    "def get_client(live=bool(True)):", "def get_client(live=None):",
    "def _get_client(live=True):", "def make_client(*, live=True):",
    "def make_client(live='x'):", "def make_client(live=default):",
])
def test_reject_live_default(tmp_path, signature):
    write_skill(tmp_path, signature + "\n    pass\n")
    errors = governance.validate_live_defaults(tmp_path)
    assert len(errors) == 1
    assert "skills/new-skill/strategy.py:1" in errors[0]
    assert "live=False" in errors[0] and "--live" in errors[0]


@pytest.mark.parametrize("code", [
    "SimmerClient(api_key=k)", 'SimmerClient(api_key=k, venue="polymarket")',
    'SimmerClient.from_env(venue="polymarket")', "SimmerClient.with_ows_wallet(wallet=w)",
    "SimmerClient(**kw)", "simmer_sdk.SimmerClient(api_key=k)",
    "simmer_sdk.SimmerClient.from_env()",
])
def test_constructor_requires_explicit_live(tmp_path, code):
    write_skill(tmp_path, code)
    errors = governance.validate_live_defaults(tmp_path)
    assert len(errors) == 1
    assert "must pass live= explicitly" in errors[0]


@pytest.mark.parametrize("callee", ["SimmerClient", "get_client", "_get_client", "other"])
@pytest.mark.parametrize("value", ["True", "1", "not False", "bool(True)",
                                       "int(True)", "str(False)", "bool(int(not False))"])
def test_truthy_call(tmp_path, callee, value):
    write_skill(tmp_path, f"{callee}(live={value})")
    assert governance.validate_live_defaults(tmp_path)


def test_lambda_default(tmp_path):
    write_skill(tmp_path, "factory = lambda live=None: live")
    assert governance.validate_live_defaults(tmp_path)


@pytest.mark.parametrize("code", [
    "def get_client(live=False): pass", "def get_client(live): pass",
    "async def make_client(*, live): pass", "factory = lambda live=False: live",
    "SimmerClient(api_key=k, live=False)", "SimmerClient(api_key=k, live=args.live)",
    "get_client(live=not dry_run)", 'SimmerClient.readonly(api_key=k, venue="polymarket")',
    "SimmerClient.from_env(live=live)", "get_client(live=bool(0))",
])
def test_accept_paper_or_runtime_opt_in(tmp_path, code):
    write_skill(tmp_path, code)
    assert governance.validate_live_defaults(tmp_path) == []


def test_updated_template():
    assert governance.validate_live_defaults(ROOT) == []
    template = (ROOT / governance.SKILL_TEMPLATE_PATH).read_text()
    assert "def get_client(live=False):" in template
    assert 'parser.add_argument("--live", action="store_true"' in template
    assert "Always default to dry-run" in template


@pytest.mark.parametrize("tag", ["python", "py", "python3"])
def test_template_block_line_number(tmp_path, tag):
    write_skill(tmp_path, f"# Template\n\n```{tag}\ndef get_client(live=True):\n    pass\n```\n",
                governance.SKILL_TEMPLATE_PATH)
    errors = governance.validate_live_defaults(tmp_path)
    assert len(errors) == 1
    assert f"{governance.SKILL_TEMPLATE_PATH}:4" in errors[0]


def test_missing_template(tmp_path):
    (tmp_path / governance.SKILL_TEMPLATE_PATH).unlink()
    assert any("missing" in e for e in governance.validate_live_defaults(tmp_path))


def test_other_builder_markdown(tmp_path):
    write_skill(tmp_path, "```python\nSimmerClient(live=True)\n```\n",
                "skills/simmer-skill-builder/references/simmer-api.md")
    assert governance.validate_live_defaults(tmp_path)


def test_grandfathered_function_only(tmp_path):
    path = "skills/kalshi-weather-trader/weather_trader.py"
    write_skill(tmp_path, "def get_client(live=True): pass\n", path)
    assert governance.validate_live_defaults(tmp_path) == []
    write_skill(tmp_path, "def get_client(live=True): pass\ndef new_client(live=True): pass\n", path)
    assert len(governance.validate_live_defaults(tmp_path)) == 1


def test_grandfathered_count_cannot_grow(tmp_path):
    path = "skills/kalshi-weather-trader/weather_trader.py"
    write_skill(tmp_path, "def get_client(live=True): pass\ndef get_client(live=True): pass\n", path)
    assert len(governance.validate_live_defaults(tmp_path)) == 1


def test_allowlist_shrink_only():
    counts = Counter(key for key, _ in governance.collect_live_violations(ROOT)[0])
    for key, allowed in governance.LIVE_DEFAULT_ALLOWLIST.items():
        assert counts[key] >= allowed, f"{key}: shrink the allowlist to {counts[key]}"
        assert not key[0].startswith("skills/simmer-skill-builder/")


def test_allowlist_total():
    # This ceiling may only go down as existing violations are migrated.
    assert sum(governance.LIVE_DEFAULT_ALLOWLIST.values()) <= 32


def test_syntax_error_fails_closed(tmp_path):
    write_skill(tmp_path, "def get_client(live=True):\n")
    assert "cannot parse Python" in governance.validate_live_defaults(tmp_path)[0]


@pytest.mark.parametrize("code", [
    "from simmer_sdk import SimmerClient as C\nC(api_key='k')\n",
    "from simmer_sdk import SimmerClient\nMake = SimmerClient\nMake(api_key='k')\n",
    "from simmer_sdk import SimmerClient as C\nC.from_env(venue='polymarket')\n",
])
def test_aliased_constructor_requires_explicit_live(tmp_path, code):
    write_skill(tmp_path, code)
    errors = governance.validate_live_defaults(tmp_path)
    assert any("must pass live= explicitly" in e for e in errors), errors

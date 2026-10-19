#!/usr/bin/env python3
"""Governance checks for ClawHub skill PRs."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
APPROVAL_MARKERS = {
    "sensitive-skill-approved",
    "skill-sensitive-approved",
}
TRADING_KEYWORDS = {
    "trade",
    "trader",
    "trading",
    "copytrading",
    "market-maker",
    "sniper",
    "scaler",
    "dca",
}
WALLET_ENV_HINTS = {
    "WALLET_PRIVATE_KEY",
    "SOLANA_PRIVATE_KEY",
    "EVM_PRIVATE_KEY",
}
SIMMER_SKILL_PATH = "skills/simmer/SKILL.md"
SIMMER_WEBSITE_REPO = "SupaFund/simmer"
SIMMER_WEBSITE_SKILL_PATH = "website/public/skill.md"
SIMMER_WEBSITE_SYNC_WAIVER_MARKERS = {
    "simmer-website-sync-waived",
    "website-skill-sync-waived",
}


SKILL_TEMPLATE_PATH = "skills/simmer-skill-builder/references/skill-template.md"
# Generated once from existing violations; shrink entries when migrating them.
# Keys are (relative path, enclosing qualified function or <module>, rule ID).
LIVE_DEFAULT_ALLOWLIST = {
    ('skills/kalshi-weather-trader/weather_trader.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-ai-divergence/ai_divergence.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-btc-up-down-trader/strategy.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-btc-up-down-trader/tests/test_exits.py', 'TestLiveTradeCalls.test_entry_scan_buys_with_client_trade', 'truthy-live'): 1,
    ('skills/polymarket-btc-up-down-trader/tests/test_exits.py', 'TestLiveTradeCalls.test_exit_monitor_sells_with_client_trade', 'truthy-live'): 1,
    ('skills/polymarket-btc-up-down-trader/tests/test_replay_plane.py', 'TestReplayEntryAndSpend.test_failed_trade_does_not_burn_daily_spend', 'truthy-live'): 1,
    ('skills/polymarket-btc-up-down-trader/tests/test_replay_plane.py', 'TestReplayEntryAndSpend.test_replay_entry_uses_tape_price_without_skip_preflight', 'truthy-live'): 1,
    ('skills/polymarket-combo-builder/combo_builder.py', 'activate_combo', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/copytrading_trader.py', 'get_client', 'explicit-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', 'TestVenueRouting.test_client_repins_when_venue_overrides_config', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', 'TestVenueRouting.test_env_venue_polymarket_without_cli_flag_used_in_trade', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', 'TestVenueRouting.test_polymarket_config_venue_sim_arg_trade_gets_sim', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', 'TestVenueRouting.test_polymarket_venue_preflight_receives_venue_kwarg', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', 'TestVenueRouting.test_sim_config_venue_polymarket_arg_trade_gets_polymarket', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', 'TestVenueRouting.test_sim_venue_no_polymarket_preflight', 'truthy-live'): 1,
    ('skills/polymarket-copytrading/tests/test_venue_routing.py', '_run_copytrading', 'live-default'): 1,
    ('skills/polymarket-dca-eval-trader/dca_eval_trader.py', 'run', 'truthy-live'): 1,
    ('skills/polymarket-elon-tweets/elon_tweets.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-fast-loop/fastloop_trader.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-fast-scaler/fast_scaler.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-market-maker/market_maker.py', 'get_client', 'explicit-live'): 1,
    ('skills/polymarket-mert-sniper/mert_sniper.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-mil-aircraft-tracker/milaircraft_tracker.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-nothing-ever-happens/nothing_ever_happens.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-nothing-ever-happens/tests/test_paper_mode_venue.py', 'VenueThreadingTests.test_explicit_polymarket_venue', 'truthy-live'): 1,
    ('skills/polymarket-nothing-ever-happens/tests/test_paper_mode_venue.py', 'VenueThreadingTests.test_sim_venue_constructs_client_with_sim', 'truthy-live'): 1,
    ('skills/polymarket-nothing-ever-happens/tests/test_paper_mode_venue.py', 'VenueThreadingTests.test_unset_venue_defaults_to_polymarket', 'truthy-live'): 1,
    ('skills/polymarket-soccer-shock-ladder/shock_ladder_trader.py', 'get_client', 'explicit-live'): 1,
    ('skills/polymarket-soccer-shock-ladder/tests/test_shock_ladder_trader.py', 'test_config_custom_buckets_and_live', 'truthy-live'): 1,
    ('skills/polymarket-soccer-shock-ladder/tests/test_shock_ladder_trader.py', 'test_venue_sim_no_trades_no_open_orders_and_deletes', 'truthy-live'): 1,
    ('skills/polymarket-weather-trader/weather_trader.py', 'get_client', 'live-default'): 1,
    ('skills/polymarket-worldcup-copytrader/copytrader.py', 'get_client', 'explicit-live'): 1,
}


def constant_value(node: ast.AST) -> tuple[bool, Any]:
    """Fold literals, not, and single-argument bool/int/str conversions only."""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        known, value = constant_value(node.operand)
        return (True, not value) if known else (False, None)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"bool", "int", "str"}
            and len(node.args) == 1 and not node.keywords):
        known, value = constant_value(node.args[0])
        if known:
            try:
                return True, {"bool": bool, "int": int, "str": str}[node.func.id](value)
            except (ValueError, TypeError, OverflowError):
                pass
    return False, None


CLIENT_FACTORIES = {"from_env", "with_ows_wallet", "readonly"}


def is_client_name(node: ast.AST) -> bool:
    return ((isinstance(node, ast.Name) and node.id == "SimmerClient")
            or (isinstance(node, ast.Attribute) and node.attr == "SimmerClient"))


def is_constant_expression(node: ast.AST) -> bool:
    """Constant calls may use only bool/int/str as function names."""
    parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
    return all(
        isinstance(parents.get(part), ast.Call)
        and parents[part].func is part and part.id in {"bool", "int", "str"}
        for part in ast.walk(node) if isinstance(part, ast.Name)
    )


def unchanged_parameters(node: ast.AST) -> set[str]:
    """Find parameters without writes in this function, excluding nested scopes."""
    args = node.args
    names = {arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs}

    class Writes(ast.NodeVisitor):
        def visit_Name(self, child):
            if isinstance(child.ctx, (ast.Store, ast.Del)):
                names.discard(child.id)

        def visit_FunctionDef(self, child):
            names.discard(child.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, child):
            names.discard(child.name)

        def visit_Lambda(self, child):
            pass

    visitor = Writes()
    for child in ([node.body] if isinstance(node, ast.Lambda) else node.body):
        visitor.visit(child)
    return names


def collect_live_violations(root: Path) -> tuple[list[tuple[tuple[str, str, str], str]], list[str]]:
    """Collect findings before grandfathering, preserving Markdown line numbers."""
    errors: list[str] = []
    violations = []
    sources = [(path, 0, path.read_text(encoding="utf-8"))
               for path in sorted((root / "skills").rglob("*.py"))]
    if not (root / SKILL_TEMPLATE_PATH).is_file():
        errors.append(f"{SKILL_TEMPLATE_PATH}:1: missing required paper-first template; restore it")
    for path in sorted((root / "skills/simmer-skill-builder").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"^[ \t]*```(?:python3|python|py)[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
            text, re.MULTILINE | re.DOTALL | re.IGNORECASE,
        ):
            sources.append((path, text.count("\n", 0, match.start(1)),
                            textwrap.dedent(match.group(1))))

    for path, offset, code in sources:
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(code, filename=relative)
        except SyntaxError as exc:
            errors.append(f"{relative}:{offset + (exc.lineno or 1)}: cannot parse Python: {exc.msg}")
            continue

        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        check_aliases = ("tests" not in path.relative_to(root).parts
                         and not path.name.startswith("test_")
                         and not path.name.endswith("_test.py"))

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scope = []
                self.parameters = []

            def report(self, node, rule, message):
                key = (relative, ".".join(self.scope) or "<module>", rule)
                violations.append((key, f"{relative}:{offset + node.lineno}: {message}"))

            def visit(self, node):
                if check_aliases and is_client_name(node) and isinstance(node.ctx, ast.Load):
                    parent = parents.get(node)
                    direct = isinstance(parent, ast.Call) and parent.func is node
                    method = (isinstance(parent, ast.Attribute) and parent.value is node
                              and parent.attr in CLIENT_FACTORIES
                              and isinstance(parents.get(parent), ast.Call)
                              and parents[parent].func is parent)
                    if not direct and not method:
                        subclass = isinstance(parent, ast.ClassDef) and node in parent.bases
                        self.report(node, "client-alias", "do not subclass SimmerClient" if subclass
                                    else "do not alias or wrap SimmerClient")
                return super().visit(node)

            def visit_ImportFrom(self, node):
                if check_aliases and any(a.name == "SimmerClient" and a.asname
                                         and a.asname != "SimmerClient" for a in node.names):
                    self.report(node, "client-alias", "do not alias SimmerClient")

            def visit_ClassDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node):
                self.scope.append("<lambda>" if isinstance(node, ast.Lambda) else node.name)
                positional = node.args.posonlyargs + node.args.args
                defaults = list(zip(positional[len(positional) - len(node.args.defaults):],
                                    node.args.defaults))
                defaults.extend(zip(node.args.kwonlyargs, node.args.kw_defaults))
                for arg, default in defaults:
                    if (arg.arg == "live" and default is not None
                            and not (isinstance(default, ast.Constant) and default.value is False)):
                        self.report(node, "live-default", "pass live=False by default and opt in with --live")
                self.parameters.append(unchanged_parameters(node))
                self.generic_visit(node)
                self.parameters.pop()
                self.scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef
            visit_Lambda = visit_FunctionDef

            def visit_Call(self, node):
                constructor = is_client_name(node.func) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"from_env", "with_ows_wallet"}
                    and is_client_name(node.func.value))
                live = next((kw for kw in node.keywords if kw.arg == "live"), None)
                if constructor:
                    splat = any(kw.arg is None for kw in node.keywords)
                    if live is None or splat:
                        message = "SimmerClient(...) must pass live= explicitly; pass live=False by default and opt in with --live"
                        if splat:
                            message += "; no ** splat into SimmerClient"
                        self.report(node, "explicit-live", message)
                if check_aliases and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                    if (len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                            and node.args[1].value == "SimmerClient"):
                        self.report(node, "client-alias", "do not alias or wrap SimmerClient with getattr")
                if live is not None:
                    literal_false = isinstance(live.value, ast.Constant) and live.value.value is False
                    known, value = constant_value(live.value)
                    parameter = (isinstance(live.value, ast.Name) and self.parameters
                                 and live.value.id in self.parameters[-1])
                    if not literal_false:
                        if (known and bool(value)) or (not constructor and is_constant_expression(live.value)):
                            self.report(node, "truthy-live", "pass live=False by default and opt in with --live using a runtime expression")
                        elif constructor and not parameter:
                            self.report(node, "live-allowlist", "pass live=False or an unchanged function parameter; opt in with --live")
                self.generic_visit(node)

        Visitor().visit(tree)
    return violations, errors


def validate_live_defaults(root: Path = ROOT) -> list[str]:
    """AST-only paper-first check, independent of git's changed-file selection."""
    violations, errors = collect_live_violations(root)
    counts: dict[tuple[str, str, str], int] = {}
    for key, message in violations:
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > LIVE_DEFAULT_ALLOWLIST.get(key, 0):
            errors.append(message)
    return errors


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return json.load(fh)


def diff_base_ref() -> str:
    base = os.environ.get("GITHUB_BASE_REF")
    if base:
        base_ref = f"origin/{base}"
        try:
            return run_git(["merge-base", "HEAD", base_ref])
        except subprocess.CalledProcessError:
            return "HEAD~1"

    return "HEAD~1"


def changed_paths() -> list[str]:
    base_ref = diff_base_ref()
    if os.environ.get("GITHUB_BASE_REF"):
        diff = run_git(["diff", "--name-only", f"{base_ref}..HEAD"])
    else:
        diff = run_git(["diff", "--name-only", "--cached"])
        if not diff:
            diff = run_git(["diff", "--name-only", f"{base_ref}..HEAD"])

    return [line for line in diff.splitlines() if line]


def changed_skill_dirs(paths: list[str] | None = None) -> set[Path]:
    paths = changed_paths() if paths is None else paths

    dirs: set[Path] = set()
    for line in paths:
        parts = Path(line).parts
        if len(parts) >= 2 and parts[0] == "skills":
            dirs.add(SKILLS_DIR / parts[1])
    return dirs


def extract_skill_metadata_version(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    in_metadata = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None
        if not stripped or stripped.startswith("#"):
            continue

        is_indented = line[:1].isspace()
        if not is_indented:
            in_metadata = stripped == "metadata:"
            continue

        if in_metadata:
            key, sep, value = stripped.partition(":")
            if sep and key == "version":
                return value.strip().strip("\"'")

    return None


def git_file_at_ref(ref: str, path: str) -> str | None:
    try:
        return run_git(["show", f"{ref}:{path}"])
    except subprocess.CalledProcessError:
        return None


def current_pull_request() -> dict[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}

    try:
        event = load_json(Path(event_path))
    except (OSError, json.JSONDecodeError):
        return {}

    pr = event.get("pull_request")
    return pr if isinstance(pr, dict) else {}


def pr_has_simmer_website_sync_waiver() -> bool:
    body = str(current_pull_request().get("body") or "").lower()
    return any(
        f"{marker}:" in body or f"{marker}=true" in body
        for marker in SIMMER_WEBSITE_SYNC_WAIVER_MARKERS
    )


def github_json(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "simmer-sdk-skill-governance",
    }
    token = os.environ.get("SIMMER_WEBSITE_SYNC_GITHUB_TOKEN") or os.environ.get(
        "GITHUB_TOKEN"
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def open_simmer_website_sync_pr(new_version: str) -> str | None:
    pulls = github_json(
        f"https://api.github.com/repos/{SIMMER_WEBSITE_REPO}/pulls?state=open&per_page=100"
    )
    if not isinstance(pulls, list):
        return None

    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if not number:
            continue

        files = github_json(
            f"https://api.github.com/repos/{SIMMER_WEBSITE_REPO}/pulls/{number}/files?per_page=100"
        )
        if not isinstance(files, list):
            continue

        for changed_file in files:
            if not isinstance(changed_file, dict):
                continue
            if changed_file.get("filename") != SIMMER_WEBSITE_SKILL_PATH:
                continue

            patch = str(changed_file.get("patch") or "")
            if new_version in patch:
                return str(pr.get("html_url") or f"{SIMMER_WEBSITE_REPO}#{number}")

    return None


def validate_skill_version_bumps(paths: list[str], base_ref: str) -> list[str]:
    errors: list[str] = []

    changed_slugs = {
        Path(path).parts[1]
        for path in paths
        if len(Path(path).parts) >= 2 and Path(path).parts[0] == "skills"
    }
    for slug in sorted(changed_slugs):
        slug_paths = [
            path
            for path in paths
            if len(Path(path).parts) >= 2
            and Path(path).parts[0] == "skills"
            and Path(path).parts[1] == slug
        ]
        skill_md = SKILLS_DIR / slug / "SKILL.md"
        if not skill_md.exists():
            continue

        clawhub_json = SKILLS_DIR / slug / "clawhub.json"
        if set(slug_paths) == {f"skills/{slug}/clawhub.json"} and clawhub_json.exists():
            config = load_json(clawhub_json)
            if config.get("publish", True) is False or config.get("published", True) is False:
                continue

        current_version = extract_skill_metadata_version(skill_md.read_text(encoding="utf-8"))
        if current_version is None:
            errors.append(
                f"skills/{slug}/SKILL.md is missing metadata.version; add a skill "
                "version so ClawHub can publish it"
            )
            continue

        previous_text = git_file_at_ref(base_ref, f"skills/{slug}/SKILL.md")
        previous_version = (
            extract_skill_metadata_version(previous_text)
            if previous_text is not None
            else None
        )

        if previous_version is None:
            continue

        if current_version == previous_version:
            display_version = current_version or "missing"
            errors.append(
                f"skills/{slug} changed but SKILL.md metadata.version did not change "
                f"from {display_version}; bump the skill version so ClawHub can publish it"
            )

    return errors


def validate_simmer_website_sync_gate(paths: list[str], base_ref: str) -> list[str]:
    if SIMMER_SKILL_PATH not in paths:
        return []

    current_text = (ROOT / SIMMER_SKILL_PATH).read_text(encoding="utf-8")
    current_version = extract_skill_metadata_version(current_text)
    previous_text = git_file_at_ref(base_ref, SIMMER_SKILL_PATH)
    previous_version = (
        extract_skill_metadata_version(previous_text)
        if previous_text is not None
        else None
    )
    if not current_version or not previous_version or current_version == previous_version:
        return []

    if pr_has_simmer_website_sync_waiver():
        return []

    try:
        sync_pr = open_simmer_website_sync_pr(current_version)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return [
            f"{SIMMER_SKILL_PATH} metadata.version changed from {previous_version} "
            f"to {current_version}, but the check could not inspect open "
            f"{SIMMER_WEBSITE_REPO} PRs: {exc}. Open a matching PR that changes "
            f"{SIMMER_WEBSITE_SKILL_PATH}, or add "
            "'simmer-website-sync-waived: <reason>' to this PR body."
        ]

    if sync_pr:
        return []

    return [
        f"{SIMMER_SKILL_PATH} metadata.version changed from {previous_version} "
        f"to {current_version}; open a matching {SIMMER_WEBSITE_REPO} PR that "
        f"changes {SIMMER_WEBSITE_SKILL_PATH} to {current_version}, or add "
        "'simmer-website-sync-waived: <reason>' to this PR body."
    ]


def pr_has_sensitive_approval_marker() -> bool:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return False

    try:
        event = load_json(Path(event_path))
    except (OSError, json.JSONDecodeError):
        return False

    pr = event.get("pull_request") or {}
    labels = {
        str(label.get("name", "")).strip().lower()
        for label in pr.get("labels", [])
    }
    if labels & APPROVAL_MARKERS:
        return True

    if current_pr_has_sensitive_approval_marker(event):
        return True

    body = str(pr.get("body") or "").lower()
    return any(f"{marker}:" in body or f"{marker}=true" in body for marker in APPROVAL_MARKERS)


def current_pr_has_sensitive_approval_marker(event: dict[str, Any]) -> bool:
    """Fetch current labels because rerun pull_request payloads can be stale."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    pr = event.get("pull_request") or {}
    number = pr.get("number")
    if not token or not repo or not number:
        return False

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{number}/labels",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "simmer-sdk-skill-governance",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            labels = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False

    current_labels = {
        str(label.get("name", "")).strip().lower()
        for label in labels
        if isinstance(label, dict)
    }
    return bool(current_labels & APPROVAL_MARKERS)


def manifest_env_names(manifest: dict[str, Any]) -> set[str]:
    names = set()
    for env_var in manifest.get("envVars") or []:
        name = env_var.get("name") if isinstance(env_var, dict) else None
        if name:
            names.add(str(name))
    for name in (manifest.get("requires") or {}).get("env") or []:
        names.add(str(name))
    return names


def has_trading_entrypoint(skill_dir: Path, manifest: dict[str, Any]) -> bool:
    entrypoint = (manifest.get("automaton") or {}).get("entrypoint")
    if not entrypoint:
        return False

    env_names = manifest_env_names(manifest)
    if env_names & WALLET_ENV_HINTS:
        return True

    haystack = " ".join(
        str(value).lower()
        for value in [
            skill_dir.name,
            entrypoint,
            manifest.get("name"),
            manifest.get("description"),
            manifest.get("category"),
            " ".join(manifest.get("tags") or []),
        ]
        if value
    )
    return any(keyword in haystack for keyword in TRADING_KEYWORDS)


def validate_skill(skill_dir: Path, pr_approved: bool) -> list[str]:
    errors: list[str] = []
    manifest_path = skill_dir / "clawhub.json"
    if not manifest_path.exists():
        return errors

    try:
        manifest = load_json(manifest_path)
    except json.JSONDecodeError as exc:
        return [f"{manifest_path.relative_to(ROOT)} is invalid JSON: {exc}"]

    sensitivity = manifest.get("sensitivity", "standard")
    if sensitivity not in {"standard", "sensitive"}:
        errors.append(
            f"{manifest_path.relative_to(ROOT)} sensitivity must be 'standard' or 'sensitive'"
        )

    if sensitivity == "sensitive":
        if not str(manifest.get("sensitivity_reason") or "").strip():
            errors.append(
                f"{manifest_path.relative_to(ROOT)} is sensitive but lacks sensitivity_reason"
            )
        if manifest.get("sensitivity_approved") is not True and not pr_approved:
            errors.append(
                f"{skill_dir.relative_to(ROOT)} is sensitivity=sensitive; add the "
                "'sensitive-skill-approved' PR label or set sensitivity_approved=true "
                "after Adrian/CTO approval"
            )

    if has_trading_entrypoint(skill_dir, manifest) and not (skill_dir / "DISCLAIMER.md").exists():
        errors.append(
            f"{skill_dir.relative_to(ROOT)} has a trading entrypoint but no DISCLAIMER.md"
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="check every skills/*/clawhub.json")
    args = parser.parse_args()

    paths = [] if args.all else changed_paths()
    base_ref = None if args.all else diff_base_ref()
    skill_dirs = (
        {path.parent for path in SKILLS_DIR.glob("*/clawhub.json")}
        if args.all
        else changed_skill_dirs(paths)
    )
    pr_approved = pr_has_sensitive_approval_marker()

    errors = validate_live_defaults()
    if paths and base_ref is not None:
        errors.extend(validate_skill_version_bumps(paths, base_ref))
        errors.extend(validate_simmer_website_sync_gate(paths, base_ref))
    for skill_dir in sorted(skill_dirs):
        errors.extend(validate_skill(skill_dir, pr_approved))

    if errors:
        print("Skill governance check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    checked = ", ".join(str(path.relative_to(ROOT)) for path in sorted(skill_dirs)) or "none"
    print(f"Skill governance check passed: {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

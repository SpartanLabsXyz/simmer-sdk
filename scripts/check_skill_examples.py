#!/usr/bin/env python3
"""Bind documented SDK examples in SKILL.md / README against the shipped SDK source.

Why this exists
---------------
Skill docs and the README ship *with* the SDK, so their ``client.<method>(...)``
examples must match the package in the same commit. When a signature changes and
an example doesn't, a builder who copies the example gets a TypeError. Doc-vs-doc
linting can't catch this; only the real signature can. This gate (run after
``pip install -e .``) binds every documented SDK call against the installed
source — if a kwarg/method the shipped code rejects appears in an example, the
build fails.

It's the SDK-repo sibling of the simmer-docs ``SDK Example Bind`` gate. The
difference: docs pin an external floor version (``.sdk-doc-floor``); here the
relevant target is the source being shipped, so it binds against whatever
``pip install -e .`` put on the path.

Safety
------
NEVER executes example code. Parses each python block with ``ast`` and uses
``inspect.signature(...).bind_partial(...)`` with placeholder values — no API
key, no network, no possibility of a trade.

Conventions
-----------
- Scans ```python / ```py fenced blocks in ``*.md`` / ``*.mdx`` (indented fences
  inside MDX components are handled).
- Tracks ``var = SimmerClient(...)`` / ``.from_env()`` / ``.with_ows_wallet()``
  assignments per file, and seeds the doc convention ``client`` -> SimmerClient,
  ``gamma`` -> GammaClient — but only for files that actually establish a
  SimmerClient, and never inside a block that imports a foreign client lib
  (py_clob_client). Anything uncertain is skipped (bias to no-false-positives).

Escape hatches
--------------
``<!-- bind:skip -->`` before a fence excludes an illustrative/pseudo-code block.
``<!-- bind:floor=X.Y.Z -->`` is accepted for parity but rarely needed here.

Usage
-----
    python scripts/check_skill_examples.py skills/simmer/SKILL.md README.md
    python scripts/check_skill_examples.py            # scan whole repo

Exit code 1 if any example fails to bind.
"""
from __future__ import annotations

import argparse
import ast
import inspect
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

CLIENT_CLASS_NAMES = {"SimmerClient", "GammaClient"}
DEFAULT_VAR_CLASS = {"client": "SimmerClient", "c": "SimmerClient", "gamma": "GammaClient"}

FENCE_RE = re.compile(r"^(\s*)```\s*(\w+)")
SKIP_RE = re.compile(r"bind:skip")
FLOOR_RE = re.compile(r"bind:floor=([0-9][0-9A-Za-z.\-]*)")
FOREIGN_CLIENT_RE = re.compile(r"\bpy_clob_client\w*\b")

_PLACEHOLDER = object()


@dataclass
class Block:
    path: Path
    start_line: int
    code: str
    floor_override: str | None


@dataclass
class Violation:
    path: Path
    line: int
    call: str
    reason: str
    floor: str


def extract_blocks(path: Path) -> list[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    i, n = 0, len(lines)
    while i < n:
        m = FENCE_RE.match(lines[i])
        if not m or m.group(2).lower() not in ("python", "py"):
            i += 1
            continue
        fence_line = i + 1
        skip = False
        floor_override = None
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j >= 0:
            if SKIP_RE.search(lines[j]):
                skip = True
            fm = FLOOR_RE.search(lines[j])
            if fm:
                floor_override = fm.group(1)
        body: list[str] = []
        i += 1
        while i < n and not lines[i].strip().startswith("```"):
            body.append(lines[i])
            i += 1
        i += 1
        if skip:
            continue
        blocks.append(
            Block(path=path, start_line=fence_line, code=textwrap.dedent("\n".join(body)), floor_override=floor_override)
        )
    return blocks


def _rhs_class(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id in CLIENT_CLASS_NAMES:
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in CLIENT_CLASS_NAMES:
        return func.value.id
    return None


def _call_str(var: str, method: str, node: ast.Call) -> str:
    parts = [ast.unparse(a) for a in node.args]
    parts += [f"{kw.arg}=..." if kw.arg else "**..." for kw in node.keywords]
    return f"{var}.{method}({', '.join(parts)})"


def bind_check(cls: type, method: str, node: ast.Call) -> str | None:
    raw = inspect.getattr_static(cls, method, None)
    if raw is None:
        return f"{cls.__name__} has no attribute '{method}'"
    if isinstance(raw, staticmethod):
        func, self_offset = raw.__func__, 0
    elif isinstance(raw, classmethod):
        func, self_offset = getattr(cls, method), 0
    elif inspect.isfunction(raw):
        func, self_offset = raw, 1
    else:
        return None
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return None
    if any(isinstance(a, ast.Starred) for a in node.args):
        return None
    if any(kw.arg is None for kw in node.keywords):
        return None
    pos = [_PLACEHOLDER] * (len(node.args) + self_offset)
    kwargs = {kw.arg: _PLACEHOLDER for kw in node.keywords}
    try:
        sig.bind_partial(*pos, **kwargs)
    except TypeError as e:
        return str(e)
    return None


def check_block(block: Block, classes: dict[str, type], default_floor: str, seed_defaults: bool) -> list[Violation]:
    floor = block.floor_override or default_floor
    try:
        tree = ast.parse(block.code)
    except SyntaxError:
        return []
    var_class: dict[str, str] = {}
    if seed_defaults and not FOREIGN_CLIENT_RE.search(block.code):
        var_class.update(DEFAULT_VAR_CLASS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            cls_name = _rhs_class(node.value)
            if cls_name:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        var_class[tgt.id] = cls_name
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        cls_name = var_class.get(func.value.id)
        if not cls_name:
            continue
        cls = classes.get(cls_name)
        if cls is None:
            continue
        reason = bind_check(cls, func.attr, node)
        if reason:
            violations.append(
                Violation(path=block.path, line=block.start_line + node.lineno, call=_call_str(func.value.id, func.attr, node), reason=reason, floor=floor)
            )
    return violations


def load_classes() -> dict[str, type]:
    classes: dict[str, type] = {}
    try:
        from simmer_sdk import SimmerClient  # type: ignore

        classes["SimmerClient"] = SimmerClient
    except Exception as e:  # pragma: no cover
        print(f"FATAL: could not import SimmerClient from simmer_sdk: {e}", file=sys.stderr)
        sys.exit(2)
    try:
        from simmer_sdk import GammaClient  # type: ignore

        classes["GammaClient"] = GammaClient
    except Exception:
        pass
    return classes


def installed_version() -> str:
    try:
        from importlib.metadata import version

        return version("simmer-sdk")
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="files or dirs to scan (default: repo root)")
    parser.add_argument("--floor", help="bind against this version label (default: installed source version)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    installed = installed_version()
    # The SDK repo binds against the source being shipped, so the floor IS the
    # installed (editable) version unless a .sdk-doc-floor pins one explicitly.
    floor_file = repo_root / ".sdk-doc-floor"
    if args.floor:
        floor = args.floor
    elif floor_file.exists():
        floor = floor_file.read_text().strip()
    else:
        floor = installed

    print(f"checking SDK skill examples — binding against simmer-sdk {floor} (installed {installed})")

    targets = [Path(p) for p in args.paths] if args.paths else [repo_root]
    files: list[Path] = []
    for t in targets:
        if t.is_dir():
            files.extend(sorted(set(t.rglob("*.md")) | set(t.rglob("*.mdx"))))
        elif t.suffix in (".md", ".mdx"):
            files.append(t)

    classes = load_classes()
    all_violations: list[Violation] = []
    n_blocks = 0
    establishes_re = re.compile(r"\bsimmer_sdk\b|SimmerClient\s*[(.]")
    for f in files:
        seed_defaults = bool(establishes_re.search(f.read_text(encoding="utf-8")))
        for block in extract_blocks(f):
            n_blocks += 1
            all_violations.extend(check_block(block, classes, floor, seed_defaults))

    print(f"scanned {len(files)} files, {n_blocks} python blocks")
    if not all_violations:
        print("OK — all documented SDK calls bind against the shipped signature.")
        return 0

    print(f"\nFAIL — {len(all_violations)} example call(s) do not bind against simmer-sdk {floor}:\n")
    for v in all_violations:
        rel = v.path.relative_to(repo_root) if v.path.is_absolute() else v.path
        print(f"  {rel}:{v.line}")
        print(f"    {v.call}")
        print(f"    -> {v.reason}")
    print(
        "\nFix: correct the example, add `<!-- bind:skip -->` if it is intentionally\n"
        "illustrative, or update the SDK if the example documents intended behavior."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

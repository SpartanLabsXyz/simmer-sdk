#!/usr/bin/env python3
"""Offline checks 1, 2, 3, 5, 6, 7, 8 over the publish.sh bundle.

JSON findings go to stdout; GitHub annotations go to stderr. Baselines are JSON
lists of findings with nonempty owner/reason fields. --check-baseline also rejects
stale entries for the selected skills (run all skills to audit the whole file).
Execution exceptions use compliance.allow: [{file, line, rule, reason}]; file is
bundle-relative and rule is exec/eval/compile/shell=True/os.system/base64.
This is a literal/AST audit of common patterns, not a sandbox. Deliberate
obfuscation (dynamic attribute access, getattr strings, exec of built strings) is
out of scope and covered by human review of publish PRs.
"""
from __future__ import annotations

import argparse
import ast
import ipaddress
import json
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
from urllib.parse import urlsplit


FIELDS = ('skill', 'check', 'file', 'line', 'message')
PIN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9][A-Za-z0-9_.+!-]*(?:\s*;.*)?$')
URL = re.compile(r'(?:https?|wss?)://[^\s<>\[\]{}()"\x27`,;]+')
HOST = re.compile(r'(?<![\w.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?![\w.-])')
PERSIST = re.compile(r'(?i)(?:\bcrontab\b|/etc/cron|\.config/systemd|/systemd/system|\blaunchctl\b|\bsystemctl\b|\blaunchd\b|LaunchAgents|LaunchDaemons|(?:^|/)(?:\.bashrc|\.bash_profile|\.bash_login|\.zshrc|\.zprofile|\.zshenv|\.profile)(?:\b|$))')


def stage_bundle(source: Path, destination: Path) -> None:
    """Mirror publish.sh: copy, prune tests/caches/bytecode, remove root dotfiles.

    Kept local until sdk #449 shares this with publishing. Do not additionally
    prune forbidden artifacts: check 1 must see anything publish.sh would ship.
    """
    shutil.copytree(source, destination, symlinks=True)
    for path in sorted(destination.rglob('*'), key=lambda p: len(p.parts)):
        if path.is_symlink() or not path.exists():
            continue
        if path.is_dir() and path.name in {'tests', '__pycache__'}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {'.pyc', '.pyo'}:
            path.unlink()
    for path in destination.glob('.*'):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def frontmatter_env(text: str) -> dict[str, dict]:
    """Read metadata.envVars using the governance checker's indentation convention.

    Supports the repo's list of name mappings, a scalar-name list, inline JSON
    lists, and an env-name mapping. This intentionally is not a YAML evaluator.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}
    metadata = False
    env_indent = None
    names = {}
    current = None
    for line in lines[1:]:
        value = line.strip()
        if value == '---':
            break
        if not value or value.startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            metadata = value == 'metadata:'
            env_indent = None
            continue
        if not metadata:
            continue
        if value.startswith('envVars:'):
            env_indent = indent
            inline = value.partition(':')[2].strip()
            if inline:
                try:
                    entries = json.loads(inline)
                except ValueError:
                    entries = re.findall(r'\b[A-Z][A-Z0-9_]+\b', inline)
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, str):
                            names[entry] = {}
                        elif isinstance(entry, dict) and isinstance(entry.get('name'), str):
                            names[entry['name']] = entry
            continue
        if env_indent is not None:
            if indent <= env_indent:
                env_indent = None
                continue
            match = re.match(r'(?:-\s*(?:name:\s*)?|)[\"\x27]?([A-Z][A-Z0-9_]*)[\"\x27]?(?:\s*:|\s*$)', value.strip('"\x27'))
            if match:
                current = match[1]
                names[current] = {}
            elif current:
                key, _, content = value.partition(':')
                if key in {'purpose', 'description', 'required', 'optional'}:
                    names[current][key] = content.strip().strip('"\x27')
    return names


def qualified(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return qualified(node.value, aliases) + '.' + node.attr
    return ''


def literal(node: ast.AST):
    return node.value if isinstance(node, ast.Constant) else None


def scoped_nodes(tree: ast.AST, aliases: dict[str, str] | None = None):
    """Visit in source order with a separate alias table for each lexical scope."""
    aliases = {} if aliases is None else aliases
    yield tree, aliases
    if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
        # Defaults/decorators/bases belong to the enclosing scope.
        for field, value in ast.iter_fields(tree):
            if field not in {'body', 'args'}:
                for child in value if isinstance(value, list) else [value]:
                    if isinstance(child, ast.AST):
                        yield from scoped_nodes(child, aliases)
        if hasattr(tree, 'args'):
            yield from scoped_nodes(tree.args, aliases)
        local = aliases.copy()

        class Bindings(ast.NodeVisitor):
            def visit_Name(self, node):
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    local[node.id] = ''

            def visit_arg(self, node):
                local[node.arg] = ''

            def visit_Import(self, node):
                for item in node.names:
                    local[item.asname or item.name.split('.')[0]] = ''

            def visit_ImportFrom(self, node):
                for item in node.names:
                    local[item.asname or item.name] = ''

            def visit_FunctionDef(self, node):
                local[node.name] = ''

            visit_AsyncFunctionDef = visit_FunctionDef
            visit_ClassDef = visit_FunctionDef

            def visit_Lambda(self, node):
                pass

        bindings = Bindings()
        if hasattr(tree, 'args'):
            bindings.visit(tree.args)
        body = tree.body if isinstance(tree.body, list) else [tree.body]
        for child in body:
            bindings.visit(child)
        for child in body:
            yield from scoped_nodes(child, local)
        if hasattr(tree, 'name'):
            aliases[tree.name] = ''
        return
    for child in ast.iter_child_nodes(tree):
        yield from scoped_nodes(child, aliases)
    if isinstance(tree, ast.Import):
        for item in tree.names:
            aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
    elif isinstance(tree, ast.ImportFrom):
        for item in tree.names:
            aliases[item.asname or item.name] = f'{tree.module}.{item.name}'
    elif isinstance(tree, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        name = qualified(tree.value, aliases)
        for target in tree.targets if isinstance(tree, ast.Assign) else [tree.target]:
            for node in ast.walk(target):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    aliases[node.id] = name if isinstance(target, ast.Name) else ''
    elif isinstance(tree, (ast.AugAssign, ast.Delete)):
        for target in tree.targets if isinstance(tree, ast.Delete) else [tree.target]:
            for node in ast.walk(target):
                if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    aliases[node.id] = ''


def normalize_host(value: str) -> str:
    try:
        host = ipaddress.ip_address(value).compressed
    except ValueError:
        try:
            host = urlsplit(value if '://' in value else '//' + value).hostname or ''
        except ValueError:
            return value.lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host.lower()


def inline_recipes(value: str) -> str:
    """Keep executable inline spans, excluding prohibitions in their sentence."""
    recipes = []
    for match in re.finditer(r'`+([^`]+)`+', value):
        before = value[:match.start()]
        after = value[match.end():]
        sentence = re.split(r'[.!?]\s+', before)[-1] + match[0] + re.split(r'[.!?](?:\s+|$)', after)[0]
        if not re.search(r"\b(?:don['’]t|do not|never|avoid|no need to)\b", sentence, re.I):
            recipes.append(match[1])
    return ' '.join(recipes)


def check_skill(source: Path) -> list[dict]:
    findings = []

    def add(check, file, line, message):
        findings.append(dict(skill=source.name, check=check,
                             file=(source / file).as_posix(), line=line, message=message))

    if not source.is_dir():
        add(1, 'SKILL.md', 1, 'skill directory is missing')
        return findings
    with tempfile.TemporaryDirectory(prefix='skill-compliance-') as temporary:
        bundle = Path(temporary) / 'bundle'
        try:
            stage_bundle(source, bundle)
        except (OSError, shutil.Error):
            add(1, 'SKILL.md', 1, 'cannot stage skill bundle')
            return findings
        for path in bundle.rglob('*'):
            if path.is_symlink():
                add(1, path.relative_to(bundle).as_posix(), 1, 'published symlink needs review')
        files = sorted(p for p in bundle.rglob('*') if p.is_file() and not p.is_symlink())
        doc_path = 'SKILL.md' if (bundle / 'SKILL.md').exists() else 'skill.md'
        doc = ''
        manifest = {}
        for required in (doc_path, 'clawhub.json'):
            if not (bundle / required).is_file():
                add(1, required, 1, 'required bundle manifest is missing')
        if (bundle / doc_path).is_file() and not (bundle / doc_path).is_symlink():
            doc = (bundle / doc_path).read_text(encoding='utf-8', errors='replace')
            if not doc.startswith('---\n') or '\n---' not in doc[4:]:
                add(1, doc_path, 1, 'SKILL.md frontmatter is missing')
        if (bundle / 'clawhub.json').is_file() and not (bundle / 'clawhub.json').is_symlink():
            try:
                manifest = json.loads((bundle / 'clawhub.json').read_text())
                if not isinstance(manifest, dict):
                    raise ValueError
                if not isinstance(manifest.get('envVars', []), list):
                    raise ValueError
                requires = manifest.get('requires', {})
                automaton = manifest.get('automaton', {})
                if not isinstance(requires, dict) or not isinstance(requires.get('pip', []), list):
                    raise ValueError
                if not isinstance(automaton, dict) or not isinstance(automaton.get('entrypoint', ''), (str, type(None))):
                    raise ValueError
                compliance = manifest.get('compliance', {})
                if not isinstance(compliance, dict) or not isinstance(compliance.get('allow', []), list):
                    raise ValueError
            except (ValueError, OSError):
                add(1, 'clawhub.json', 1, 'invalid clawhub.json manifest')
                manifest = {}
        allows = manifest.get('compliance', {}).get('allow', [])

        def execution(file, line, rule):
            if not any(isinstance(a, dict) and a.get('file') == file
                       and a.get('line') == line and a.get('rule') == rule
                       and isinstance(a.get('reason'), str) and a['reason'].strip()
                       for a in allows):
                add(6, file, line, f'forbidden execution: {rule}')

        env_reads = {}
        trees = {}
        hosts = {normalize_host(host) for host in HOST.findall(doc)}
        hosts.update(normalize_host(match[0]) for match in URL.finditer(doc))
        for candidate in re.findall(r'[0-9a-fA-F:.]+', doc):
            try:
                hosts.add(ipaddress.ip_address(candidate.rstrip('.')).compressed)
            except ValueError:
                pass
        for path in files:
            file = path.relative_to(bundle).as_posix()
            parts = path.relative_to(bundle).parts
            if (any(p in {'tests', '__pycache__', 'fixtures', 'notebooks'} for p in parts)
                    or any(p == '.env' or p.startswith('.env.') for p in parts)
                    or path.suffix in {'.pyc', '.pyo', '.ipynb'}
                    or re.search(r'skill[-_]card', path.name, re.I)):
                add(1, file, 1, 'forbidden artifact in published bundle')
            raw = path.read_bytes()
            if b'\x00' in raw:
                continue
            text = raw.decode('utf-8', errors='replace')
            for match in re.finditer(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}', text):
                execution(file, text.count('\n', 0, match.start()) + 1, 'base64')
            # Python uses AST below; shell scripts have no stdlib parser.
            if path.suffix in {'.sh', '.bash', '.zsh'}:
                for line, value in enumerate(text.splitlines(), 1):
                    if value.lstrip().startswith('#'):
                        continue
                    if re.search(r'\bsudo\b', value) or PERSIST.search(value):
                        add(8, file, line, 'persistence or privilege command/path')
                    if re.search(r'\b(?:eval|exec)\b', value):
                        execution(file, line, 'eval' if re.search(r'\beval\b', value) else 'exec')
                    for match in URL.finditer(value):
                        host = urlsplit(match[0]).hostname
                        if host and normalize_host(host) not in hosts:
                            add(7, file, line, f'undeclared network host: {normalize_host(host)}')
            if path.suffix != '.py':
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError as exc:
                add(1, file, exc.lineno or 1, 'cannot parse bundled Python')
                continue
            trees[file] = tree
            # Keep reporting order stable while resolving aliases in source order.
            scopes = {node: aliases.copy() for node, aliases in scoped_nodes(tree)}
            for node in ast.walk(tree):
                aliases = scopes[node]
                if isinstance(node, ast.Call):
                    name = qualified(node.func, aliases)
                    if name in {'os.getenv', 'os.environ.get', 'os.environ.__getitem__'}:
                        key = node.args[0] if node.args else next(
                            (k.value for k in node.keywords if k.arg == 'key'), None)
                        if isinstance(literal(key), str):
                            env_reads.setdefault(literal(key), (file, node.lineno))
                        else:
                            add(2, file, node.lineno, 'dynamic environment name cannot be audited')
                    if name in {'exec', 'eval', 'compile', 'builtins.exec', 'builtins.eval', 'builtins.compile', 'os.system'}:
                        execution(file, node.lineno, name.removeprefix('builtins.'))
                    if name.startswith('subprocess.'):
                        keywords = list(node.keywords)
                        if name in {'subprocess.Popen', 'subprocess.run', 'subprocess.call',
                                    'subprocess.check_call', 'subprocess.check_output'} and len(node.args) > 8:
                            keywords.append(ast.keyword(arg='shell', value=node.args[8]))
                        while keywords:
                            keyword = keywords.pop()
                            if keyword.arg is None:
                                if isinstance(keyword.value, ast.Dict):
                                    for key, value in zip(keyword.value.keys, keyword.value.values):
                                        if key is None or not isinstance(literal(key), str):
                                            add(6, file, node.lineno, 'subprocess **kwargs need review')
                                        else:
                                            keywords.append(ast.keyword(arg=literal(key), value=value))
                                else:
                                    add(6, file, node.lineno, 'subprocess **kwargs need review')
                            elif keyword.arg == 'shell':
                                if literal(keyword.value) is True:
                                    execution(file, node.lineno, 'shell=True')
                                elif literal(keyword.value) is not False:
                                    add(6, file, node.lineno, 'subprocess shell value needs review')
                    host_arg = None
                    if name in {'http.client.HTTPConnection', 'http.client.HTTPSConnection'}:
                        host_arg = node.args[0] if node.args else next(
                            (k.value for k in node.keywords if k.arg == 'host'), None)
                    elif name == 'socket.create_connection' or isinstance(node.func, ast.Attribute) and node.func.attr in {'connect', 'connect_ex'}:
                        address = node.args[0] if node.args else next(
                            (k.value for k in node.keywords if k.arg == 'address'), None)
                        if isinstance(address, (ast.Tuple, ast.List)) and address.elts:
                            host_arg = address.elts[0]
                    host = literal(host_arg)
                    if isinstance(host, str) and normalize_host(host) not in hosts:
                        add(7, file, node.lineno, f'undeclared network host: {normalize_host(host)}')
                    destination = None
                    if name not in {'open', 'io.open'} and isinstance(node.func, ast.Attribute) and node.func.attr in {'write_text', 'write_bytes', 'open'}:
                        receiver = node.func.value
                        mode = node.args[0] if node.args else next(
                            (k.value for k in node.keywords if k.arg == 'mode'), None)
                        writes = node.func.attr != 'open' or isinstance(literal(mode), str) and any(c in literal(mode) for c in 'wax+')
                        if writes and isinstance(receiver, ast.Call) and qualified(receiver.func, aliases) in {'Path', 'pathlib.Path'} and receiver.args:
                            destination = literal(receiver.args[0])
                    elif name in {'open', 'io.open'}:
                        mode = node.args[1] if len(node.args) > 1 else next(
                            (k.value for k in node.keywords if k.arg == 'mode'), None)
                        if isinstance(literal(mode), str) and any(c in literal(mode) for c in 'wax+'):
                            destination = literal(node.args[0]) if node.args else next(
                                (literal(k.value) for k in node.keywords if k.arg == 'file'), None)
                    if isinstance(destination, str):
                        target = Path(destination)
                        resolved = (source / file).parent / target
                        if not resolved.resolve().is_relative_to(source.resolve()):
                            kind = 'absolute' if target.is_absolute() else 'escaping'
                            add(8, file, node.lineno, f'{kind} write destination needs review: {destination}')
                elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and qualified(node.value, aliases) == 'os.environ':
                    if isinstance(literal(node.slice), str):
                        env_reads.setdefault(literal(node.slice), (file, node.lineno))
                    else:
                        add(2, file, node.lineno, 'dynamic environment name cannot be audited')
                # Literal URLs include f-string constant portions, not comments.
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for match in URL.finditer(node.value):
                        try:
                            host = urlsplit(match[0]).hostname
                        except ValueError:
                            continue
                        if host and normalize_host(host) not in hosts:
                            add(7, file, node.lineno, f'undeclared network host: {normalize_host(host)}')
                    if re.search(r'\bsudo\b', node.value) or PERSIST.search(node.value):
                        add(8, file, node.lineno, 'persistence or privilege command/path')

        declared = {
            doc_path: set(frontmatter_env(doc)),
            'clawhub.json': {item['name'] if isinstance(item, dict) else item
                             for item in manifest.get('envVars', [])
                             if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get('name'), str)},
        }
        env_metadata = {
            doc_path: frontmatter_env(doc),
            'clawhub.json': {item['name']: item
                             for item in manifest.get('envVars', [])
                             if isinstance(item, dict) and isinstance(item.get('name'), str)},
        }
        for location, names in declared.items():
            for name in sorted(names):
                entry = env_metadata[location].get(name, {})
                if not any(isinstance(entry.get(key), str) and entry[key].strip()
                           for key in ('purpose', 'description')):
                    add(2, location, 1, f'{name} declaration missing purpose text; needs review')
            for name in sorted(env_reads.keys() - names):
                file, line = env_reads[name]
                add(2, file, line, f'{name} is read but absent from {location} envVars')
            for name in sorted(names - env_reads.keys()):
                add(2, location, 1, f'{name} is declared but unused by bundled code')

        entrypoint = manifest.get('automaton', {}).get('entrypoint')
        flags = {'--help'}
        if entrypoint:
            if entrypoint not in trees:
                add(3, 'clawhub.json', 1, 'entrypoint is missing or is not parseable Python')
            else:
                for node in ast.walk(trees[entrypoint]):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
                        flags.update(literal(arg) for arg in node.args if isinstance(literal(arg), str) and literal(arg).startswith('--'))
        fence = None
        for line, value in enumerate(doc.splitlines(), 1):
            marker = re.match(r'^\s*(`{3,}|~{3,})', value)
            if marker:
                fence = None if fence and marker[1].startswith(fence) else marker[1]
            code = value if fence else inline_recipes(value)
            if re.search(r'\bsudo\b', code) or PERSIST.search(code):
                add(8, doc_path, line, 'documented persistence or privilege recipe needs review')
            for flag in sorted(set(re.findall(r'(?<![\w-])--[a-zA-Z][a-zA-Z0-9_-]*', value)) - flags):
                add(3, doc_path, line, f'documented flag absent from entrypoint argparse: {flag}')

        requirements = bundle / 'requirements.txt'
        pip = manifest.get('requires', {}).get('pip', [])
        requirement_lines = [(line, value.split(' #', 1)[0].strip()) for line, value in
                             enumerate(requirements.read_text().splitlines(), 1)] if requirements.is_file() and not requirements.is_symlink() else []
        requirement_lines = [(line, value) for line, value in requirement_lines if value and not value.startswith('#')]
        for file, entries in [('requirements.txt', requirement_lines), ('clawhub.json', [(1, p) for p in pip])]:
            for line, package in entries:
                if not isinstance(package, str) or not PIN.fullmatch(package):
                    add(5, file, line, f'dependency must use an exact == pin: {package}')
        normalize = lambda value: re.sub(r'[-_.]+', '-', value.split('==')[0]).lower() + '==' + value.partition('==')[2]
        if {normalize(p) for _, p in requirement_lines} != {normalize(p) for p in pip if isinstance(p, str)}:
            add(5, 'clawhub.json', 1, 'requires.pip and requirements.txt disagree')
        logical_doc = re.sub(r'\\\n\s*', ' ', doc)
        for line, value in enumerate(logical_doc.splitlines(), 1):
            if re.search(r'\bcurl\b[^\n|]*\|\s*(?:(?:/[^\s|]+/)?env\s+(?:[^|\n]*\s+)?)?(?:/[^\s|]+/)?(?:sh|bash|zsh|dash|ksh)\b', value) or 'git+' in value:
                add(5, doc_path, line, 'unsafe install source or curl piped to shell')
            match = re.search(r'\bpip(?:3(?:\.\d+)?)?\s+(?:[^\n`]*?\s+)?install\s+(.+)', value)
            if match:
                try:
                    tokens = shlex.split(match[1].split('#', 1)[0].replace('`', ''))
                except ValueError:
                    tokens = []
                packages = []
                manifests = []
                index = 0
                while index < len(tokens):
                    token = tokens[index]
                    if token in {'-r', '--requirement'}:
                        index += 1
                        manifests.append(tokens[index] if index < len(tokens) else '')
                    elif token.startswith('--requirement='):
                        manifests.append(token.partition('=')[2])
                    elif token.startswith('-r'):
                        manifests.append(token[2:])
                    elif not token.startswith('-'):
                        packages.append(token)
                    index += 1
                pinned = bool(packages or manifests) and all(PIN.fullmatch(t) for t in packages)
                for requirement in manifests:
                    target = bundle / requirement
                    if (not requirement or not target.resolve().is_relative_to(bundle.resolve())
                            or not target.is_file() or target.is_symlink()):
                        add(5, doc_path, line, f'requirement manifest cannot be audited: {requirement}')
                        pinned = False
                        continue
                    entries = [(n, v.split(' #', 1)[0].strip()) for n, v in
                               enumerate(target.read_text().splitlines(), 1)
                               if v.strip() and not v.lstrip().startswith('#')]
                    pinned = pinned and bool(entries)
                    for number, package in entries:
                        if not PIN.fullmatch(package):
                            add(5, requirement, number, f'dependency must use an exact == pin: {package}')
                            pinned = False
                if not pinned:
                    add(5, doc_path, line, 'pip install must use exact == pins: ' + shlex.join(tokens))

    return sorted([dict(zip(FIELDS, key)) for key in {tuple(f[k] for k in FIELDS) for f in findings}],
                  key=lambda f: (f['skill'], f['check'], f['file'], f['line'], f['message']))


def identity(finding):
    return tuple(finding[key] for key in FIELDS)


def escape(value, property=False):
    value = str(value).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    return value.replace(':', '%3A').replace(',', '%2C') if property else value


def annotate(finding):
    print(f"::error file={escape(finding['file'], True)},line={finding['line']}::"
          f"{escape(str(finding['check']) + ': ' + finding['message'])}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('skills', nargs='+', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--check-baseline', action='store_true', help='fail on stale baseline entries for selected skills')
    parser.add_argument('--write-baseline', type=Path, help='write only current findings with owner/reason')
    parser.add_argument('--owner')
    parser.add_argument('--reason')
    args = parser.parse_args()
    if args.write_baseline and (not args.owner or not args.owner.strip() or not args.reason or not args.reason.strip()):
        parser.error('--write-baseline requires nonempty --owner and --reason')
    if args.check_baseline and not args.baseline:
        parser.error('--check-baseline requires --baseline')
    findings = [f for skill in args.skills for f in check_skill(skill)]
    print(json.dumps(findings, indent=2))
    for finding in findings:
        annotate(finding)
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(
            [dict(f, owner=args.owner, reason=args.reason) for f in findings], indent=2) + '\n')
        return 0
    baseline = []
    if args.baseline:
        try:
            baseline = json.loads(args.baseline.read_text())
            if not isinstance(baseline, list) or any(
                not isinstance(f, dict) or not all(k in f for k in FIELDS)
                or not all(isinstance(f.get(k), str) and f[k].strip() for k in ('owner', 'reason'))
                for f in baseline
            ):
                raise ValueError
            known = {identity(f) for f in baseline}
        except (OSError, ValueError, TypeError):
            print('::error::invalid baseline: each finding needs owner and reason', file=sys.stderr)
            return 1
    else:
        known = set()
    current = {identity(f) for f in findings}
    stale = [f for f in baseline if f['skill'] in {p.name for p in args.skills} and identity(f) not in current]
    if args.check_baseline:
        for finding in stale:
            annotate(dict(finding, message='stale baseline entry: ' + finding['message']))
    return int(bool(current - known) or args.check_baseline and bool(stale))


if __name__ == '__main__':
    sys.exit(main())

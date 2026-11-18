# Gate bypass corpus

`tests/test_gate_bypass_corpus.py` runs both production CLIs in subprocesses.
Skill-compliance inputs become temporary bundles. Reward-hack inputs become
base/head revisions in isolated temporary git repositories. Fixture programs
are parsed by the gates, never imported or executed.

Each `<gate>/<id>/case.json` contains its review source and expected finding
message fragments. An empty `expected` list requires a clean exit and no findings.
Skill cases supply `files`, optional `symlinks`, and optional `before_files` for
baseline-identity probes. Reward cases supply complete `before` and `after` file
maps plus the PR `body`. The runner adds minimal bundle manifests and Markdown
frontmatter where omitted.

`known_hole` records an unresolved probe, with a reference and a concrete reason.
These cases still run and assert the intended behavior, under strict xfail:
fixing one causes an unexpected-pass failure until its marker is removed. To
see every live hole as a failing test, use:

```bash
/workspace/wt/venvs/sdk-pn15/bin/python -m pytest -q -p no:cacheprovider --runxfail tests/test_gate_bypass_corpus.py
```

Seven cases remain open: multiline JS `it` and `test` deletions, short and long
workflow display-name changes, interpolated Clark selectors, and namespace keys
passed to `Element.get` and `dict.get`. These refer to #473 and #496. Their
expectations are retained rather than changed to match the gate's current output.

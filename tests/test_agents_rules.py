"""AGENTS.md lint regressions, run by the existing root pytest job."""
import importlib.util
import inspect
from pathlib import Path

import pytest
from simmer_sdk import SimmerClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('governance_rules', ROOT / 'scripts/check-skill-governance.py')
governance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(governance)


@pytest.mark.parametrize('method', ['trade', 'place_combo'])
def test_no_per_trade_live(method):
    signature = inspect.signature(getattr(SimmerClient, method))
    assert 'live' not in signature.parameters
    # Also prevent **kwargs from silently accepting live=.
    assert all(p.kind != inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())


@pytest.mark.parametrize('text', [
    'Balance: $SIM100.00',
    'For $SIM: each market starts with a $10,000 $SIM balance.',
    'Config priority: config.json > environment variables > defaults.',
    'Config priority: `config.json > automaton tuning > env vars > defaults`.',
    'Precedence: defaults > env vars > config.json',
])
def test_reject_bad_docs(tmp_path, text):
    (tmp_path / 'README.md').write_text(text)
    assert governance.validate_doc_rules(tmp_path)


@pytest.mark.parametrize('relative', ['README.md', 'skills/example/SKILL.md',
                                      'skills/example/references/template.md', 'docs/nested/guide.md'])
def test_doc_scope_and_good_spelling(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('100.00 $SIM; real venue $100.00\nConfig priority: env vars > config.json > defaults.')
    assert governance.validate_doc_rules(tmp_path) == []
    path.write_text('$SIM100.00')
    assert governance.validate_doc_rules(tmp_path)


def test_repo_doc_rules():
    assert governance.validate_doc_rules(ROOT) == []


@pytest.mark.parametrize('text', ['$10K $SIM', '$1.5m $SIM', '$2B $SIM', '$100$SIM',
    'env vars > defaults', 'config.json > env variables > defaults'])
def test_additional_bad_docs(tmp_path, text):
    (tmp_path / 'README.md').write_text(text)
    assert governance.validate_doc_rules(tmp_path)


@pytest.mark.parametrize('text', ['10,000 $SIM', '100.00 $SIM', '10K $SIM',
    'config.json > defaults', 'env vars > config.json',
    'env vars > config.json > defaults (config.json is optional)',
    'env variables > config.json > defaults', 'combo_config.json > env vars'])
def test_additional_good_docs(tmp_path, text):
    (tmp_path / 'README.md').write_text(text)
    assert governance.validate_doc_rules(tmp_path) == []

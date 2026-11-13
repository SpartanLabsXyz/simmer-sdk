"""Lock the exact 1.2.1 dependency required by issue #487."""

from importlib import import_module
from importlib.metadata import version
from pathlib import Path


def test_py_clob_client_v2_import_and_version():
    import_module("py_clob_client_v2.client")
    assert version("py-clob-client-v2") == "1.2.1"


def test_pyproject_pins_py_clob_client_v2():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declarations = [line.strip() for line in pyproject.read_text().splitlines()]
    assert '"py-clob-client-v2==1.2.1",' in declarations

"""Regression checks for the public skill documentation cleanup."""
import re
from pathlib import Path

import yaml


SKILLS = Path(__file__).resolve().parents[1] / "skills"


def test_mert_sniper_frontmatter_is_valid_yaml():
    text = (SKILLS / "polymarket-mert-sniper/SKILL.md").read_text()
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert "Defaults: $10" in metadata["description"]


def test_weather_cleanup_has_its_own_release():
    text = (SKILLS / "polymarket-weather-trader/CHANGELOG.md").read_text()
    versions = re.findall(r"^## \[([^]]+)\]", text, re.MULTILINE)
    assert len(versions) == len(set(versions))
    cleanup = text.split("## [1.23.23]", 1)[1].split("\n## [", 1)[0]
    assert "Moved inline version history" in cleanup


def test_weather_safeguards_guidance_preserved():
    text = (SKILLS / "polymarket-weather-trader/SKILL.md").read_text()
    assert "Disable only with `--no-safeguards` (not recommended)." in text
    assert "# Disable safeguards (not recommended)" in text
    assert "Disable the flip-flop / slippage / time-decay / resolved checks with `--no-safeguards` (not recommended)." in text


def test_btc_cleanup_does_not_add_inline_history():
    text = (SKILLS / "polymarket-btc-up-down-trader/SKILL.md").read_text()
    assert "## What's New in v1.2.4" not in text

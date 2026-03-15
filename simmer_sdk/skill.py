"""
Simmer Skill Config — shared config loading for Simmer trading skills.

Usage:
    from simmer_sdk.skill import load_config, update_config, get_config_path

    SKILL_SLUG = "polymarket-weather-trader"
    CONFIG_SCHEMA = {
        "entry_threshold": {"env": "SIMMER_WEATHER_ENTRY", "default": 0.15, "type": float},
        "max_trades_per_run": {"env": "SIMMER_WEATHER_MAX_TRADES", "default": 5, "type": int},
    }
    _config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

Config priority: config.json > env vars > defaults
"""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_config(schema, skill_file, slug=None, config_filename="config.json"):
    """
    Load skill config with priority: config.json > env vars > defaults.

    Args:
        schema: Dict of config keys to specs. Each spec has:
            - env: Environment variable name
            - default: Default value
            - type: Type constructor (float, int, str, bool)
        skill_file: Pass __file__ from the skill script
        slug: Optional skill slug (kept for API compatibility, currently unused)
        config_filename: Config file name (default: "config.json")

    Returns:
        Dict of config key → resolved value
    """

    config_path = Path(skill_file).parent / config_filename
    file_cfg = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    result = {}
    for key, spec in schema.items():
        env_name = spec.get("env")
        env_val = os.environ.get(env_name) if env_name else None
        # Priority: env vars (includes automaton tuning) > config.json > defaults
        if env_val is not None:
            type_fn = spec.get("type", str)
            try:
                if type_fn == bool:
                    result[key] = env_val.lower() in ("true", "1", "yes")
                elif type_fn != str:
                    result[key] = type_fn(env_val)
                else:
                    result[key] = env_val
            except (ValueError, TypeError):
                result[key] = file_cfg.get(key, spec.get("default"))
        elif key in file_cfg:
            result[key] = file_cfg[key]
        else:
            result[key] = spec.get("default")
    return result


def get_config_path(skill_file, config_filename="config.json"):
    """Get path to a skill's config.json file."""
    return Path(skill_file).parent / config_filename


def update_config(updates, skill_file, config_filename="config.json"):
    """Update config values and save to config.json."""
    config_path = Path(skill_file).parent / config_filename
    existing = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    existing.update(updates)
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
    return existing

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

Config priority: config.json > automaton tuning > env vars > defaults

When a slug is provided, the automaton API is queried for tuned config values.
These are applied as env vars before the normal config chain runs, so
config.json still wins for local overrides.
"""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

API_BASE = "https://api.simmer.markets"


def _apply_automaton_config(slug):
    """Fetch tuned config from automaton API and apply as env vars."""
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        return {}
    try:
        from urllib.request import urlopen, Request
        url = f"{API_BASE}/api/sdk/automaton/my-config?skill={slug}"
        req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        config = data.get("config", {})
        if config:
            # Only allow env vars prefixed with SIMMER_, excluding credentials
            _BLOCKED = {"SIMMER_API_KEY", "SIMMER_PRIVATE_KEY", "SIMMER_SECRET", "SIMMER_API_SECRET"}
            safe = {k: str(v) for k, v in config.items() if k.startswith("SIMMER_") and k not in _BLOCKED}
            os.environ.update(safe)
            print(f"[automaton] Config applied for {slug}: {', '.join(f'{k}={v}' for k, v in safe.items())}")
            logger.debug("Applied %d automaton config override(s) for %s", len(config), slug)
        return config
    except Exception:
        return {}


def load_config(schema, skill_file, slug=None, config_filename="config.json"):
    """
    Load skill config with priority: env vars (includes automaton tuning) > config.json > defaults.

    Args:
        schema: Dict of config keys to specs. Each spec has:
            - env: Environment variable name
            - default: Default value
            - type: Type constructor (float, int, str, bool)
        skill_file: Pass __file__ from the skill script
        slug: Optional skill slug (e.g. "polymarket-weather-trader").
              If provided, fetches tuned config from the automaton API.
        config_filename: Config file name (default: "config.json")

    Returns:
        Dict of config key → resolved value
    """
    if slug:
        _apply_automaton_config(slug)

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

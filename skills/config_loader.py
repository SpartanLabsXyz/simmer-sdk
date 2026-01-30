"""
Shared config loader for Simmer skills.

Priority:
1. Local config file (config.json in skill directory)
2. Environment variables
3. Hardcoded defaults

Usage:
    from config_loader import load_config
    
    CONFIG_SCHEMA = {
        "entry_threshold": {"env": "SIMMER_WEATHER_ENTRY", "default": 0.15, "type": float},
        "exit_threshold": {"env": "SIMMER_WEATHER_EXIT", "default": 0.45, "type": float},
        "locations": {"env": "SIMMER_WEATHER_LOCATIONS", "default": "NYC", "type": str},
    }
    
    config = load_config(CONFIG_SCHEMA, __file__)
    print(config["entry_threshold"])  # 0.15 or from config.json or env var
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, Optional


def load_config(
    schema: Dict[str, Dict[str, Any]],
    skill_file: str,
    config_filename: str = "config.json"
) -> Dict[str, Any]:
    """
    Load configuration with priority: config.json > env vars > defaults.
    
    Args:
        schema: Dict mapping config keys to their env var names, defaults, and types.
                Example: {"entry_threshold": {"env": "SIMMER_WEATHER_ENTRY", "default": 0.15, "type": float}}
        skill_file: Pass __file__ from the skill script to locate config.json
        config_filename: Name of config file (default: config.json)
    
    Returns:
        Dict with resolved configuration values
    """
    config = {}
    
    # Find config file in skill directory
    skill_dir = Path(skill_file).parent
    config_path = skill_dir / config_filename
    
    # Load config file if it exists
    file_config = {}
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                file_config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load {config_path}: {e}")
    
    # Resolve each config key
    for key, spec in schema.items():
        env_var = spec.get("env")
        default = spec.get("default")
        type_fn = spec.get("type", str)
        
        # Priority 1: config.json
        if key in file_config:
            value = file_config[key]
        # Priority 2: environment variable
        elif env_var and os.environ.get(env_var):
            value = os.environ.get(env_var)
            # Convert type if needed
            if type_fn and type_fn != str:
                try:
                    value = type_fn(value)
                except (ValueError, TypeError):
                    value = default
        # Priority 3: default
        else:
            value = default
        
        config[key] = value
    
    return config


def save_config(
    config: Dict[str, Any],
    skill_file: str,
    config_filename: str = "config.json"
) -> bool:
    """
    Save configuration to config.json in the skill directory.
    
    Args:
        config: Configuration dict to save
        skill_file: Pass __file__ from the skill script
        config_filename: Name of config file (default: config.json)
    
    Returns:
        True if saved successfully, False otherwise
    """
    skill_dir = Path(skill_file).parent
    config_path = skill_dir / config_filename
    
    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except IOError as e:
        print(f"Error saving config to {config_path}: {e}")
        return False


def get_config_path(skill_file: str, config_filename: str = "config.json") -> Path:
    """Get the path to the config file for a skill."""
    return Path(skill_file).parent / config_filename


def update_config(
    updates: Dict[str, Any],
    skill_file: str,
    config_filename: str = "config.json"
) -> Dict[str, Any]:
    """
    Update specific config values, preserving existing ones.
    
    Args:
        updates: Dict of config values to update
        skill_file: Pass __file__ from the skill script
        config_filename: Name of config file (default: config.json)
    
    Returns:
        The updated full config dict
    """
    config_path = get_config_path(skill_file, config_filename)
    
    # Load existing config
    existing = {}
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    
    # Merge updates
    existing.update(updates)
    
    # Save
    save_config(existing, skill_file, config_filename)
    
    return existing

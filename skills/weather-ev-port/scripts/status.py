#!/usr/bin/env python3
"""Status pass-through for ClawHub/autotune convention.

Delegates to the main skill entrypoint so there's one source of truth.
"""
import os
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))

from weather_ev_port import print_status, print_sdk_positions

if __name__ == "__main__":
    print_status()
    if os.environ.get("SIMMER_API_KEY"):
        print_sdk_positions()

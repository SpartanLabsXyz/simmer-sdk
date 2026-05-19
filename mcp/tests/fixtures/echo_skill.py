#!/usr/bin/env python3
"""Test fixture — echoes env + argv as simmer_managed_output JSON."""
import json
import os
import sys

mode = os.environ.get("ECHO_MODE", "ok")

if mode == "crash":
    raise RuntimeError("fixture crash")
elif mode == "exit1":
    print("an error happened", file=sys.stderr)
    sys.exit(1)
elif mode == "sleep":
    import time
    time.sleep(30)
else:
    payload = {
        "argv": sys.argv[1:],
        "trading_venue": os.environ.get("TRADING_VENUE"),
        "managed_mode": os.environ.get("SIMMER_MANAGED_MODE"),
        "echo_extra": os.environ.get("ECHO_EXTRA"),
    }
    print("starting echo skill")
    print(json.dumps({"simmer_managed_output": payload}))

#!/usr/bin/env python3
"""
Quick status check for AI Divergence Scanner.

Usage:
    python scripts/status.py
"""

import os
import sys
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

SIMMER_API_URL = os.environ.get("SIMMER_API_URL", "https://api.simmer.markets")


def main():
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("❌ SIMMER_API_KEY not set")
        sys.exit(1)
    
    print("📊 Checking AI Divergence status...\n")
    
    try:
        req = Request(
            f"{SIMMER_API_URL}/api/sdk/markets",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        data = json.loads(urlopen(req, timeout=30).read())
        markets = data.get("markets", [])
        
        high_div = [m for m in markets if abs(m.get("divergence") or 0) > 0.10]
        med_div = [m for m in markets if 0.05 < abs(m.get("divergence") or 0) <= 0.10]
        
        bullish = len([m for m in markets if (m.get("divergence") or 0) > 0.05])
        bearish = len([m for m in markets if (m.get("divergence") or 0) < -0.05])
        
        print("=" * 40)
        print("🔮 AI DIVERGENCE STATUS")
        print("=" * 40)
        print(f"  Total markets:     {len(markets)}")
        print(f"  High divergence:   {len(high_div)} (>10%)")
        print(f"  Medium divergence: {len(med_div)} (5-10%)")
        print(f"  Bullish signals:   {bullish}")
        print(f"  Bearish signals:   {bearish}")
        print("=" * 40)
        
        if high_div:
            print("\n💡 Top opportunity:")
            top = max(high_div, key=lambda m: abs(m.get("divergence") or 0))
            q = top.get("question", "")[:50]
            div = top.get("divergence") or 0
            print(f"   {q}...")
            print(f"   Divergence: {div:+.1%}")
        
        print()
        
    except HTTPError as e:
        print(f"❌ API Error: {e.code}")
        sys.exit(1)
    except URLError as e:
        print(f"❌ Connection error: {e.reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()

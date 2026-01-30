#!/usr/bin/env python3
"""
Simmer AI Divergence Scanner

Surfaces markets where Simmer's AI price diverges from Polymarket.
High divergence = potential alpha if AI is right.

Usage:
    python ai_divergence.py              # Show all divergences
    python ai_divergence.py --min 10     # Only >10% divergence
    python ai_divergence.py --bullish    # AI more bullish than market
    python ai_divergence.py --bearish    # AI more bearish than market
    python ai_divergence.py --json       # Machine-readable output
"""

import os
import sys
import json
import argparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SIMMER_API_URL = os.environ.get("SIMMER_API_URL", "https://api.simmer.markets")


def api_request(api_key: str, endpoint: str) -> dict:
    """Make authenticated request to Simmer API."""
    url = f"{SIMMER_API_URL}{endpoint}"
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    })
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"❌ API Error {e.code}: {error_body}")
        sys.exit(1)
    except URLError as e:
        print(f"❌ Connection error: {e.reason}")
        sys.exit(1)


def get_markets(api_key: str) -> list:
    """Fetch all markets with divergence data."""
    data = api_request(api_key, "/api/sdk/markets")
    return data.get("markets", [])


def format_divergence(markets: list, min_div: float = 0, direction: str = None) -> None:
    """Display divergence table."""
    
    filtered = []
    for m in markets:
        div = m.get("divergence") or 0
        if abs(div) < min_div / 100:
            continue
        if direction == "bullish" and div <= 0:
            continue
        if direction == "bearish" and div >= 0:
            continue
        filtered.append(m)
    
    filtered.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)
    
    if not filtered:
        print("No markets match your filters.")
        return
    
    print()
    print("🔮 AI Divergence Scanner")
    print("=" * 75)
    print(f"{'Market':<40} {'Simmer':>8} {'Poly':>8} {'Div':>8} {'Signal':>8}")
    print("-" * 75)
    
    for m in filtered[:20]:
        q = m.get("question", "")[:38]
        simmer = m.get("current_probability") or 0
        poly = m.get("external_price_yes") or 0
        div = m.get("divergence") or 0
        
        if div > 0.05:
            signal = "🟢 BUY"
        elif div < -0.05:
            signal = "🔴 SELL"
        else:
            signal = "⚪ HOLD"
        
        print(f"{q:<40} {simmer:>7.1%} {poly:>7.1%} {div:>+7.1%} {signal:>8}")
    
    print("-" * 75)
    print(f"Showing {len(filtered[:20])} of {len(filtered)} markets with divergence")
    print()
    
    bullish = len([m for m in filtered if (m.get("divergence") or 0) > 0])
    bearish = len([m for m in filtered if (m.get("divergence") or 0) < 0])
    avg_div = sum(abs(m.get("divergence") or 0) for m in filtered) / len(filtered) if filtered else 0
    
    print(f"📊 Summary: {bullish} bullish, {bearish} bearish, avg divergence {avg_div:.1%}")


def show_opportunities(markets: list) -> None:
    """Show actionable high-conviction opportunities."""
    
    print()
    print("💡 Top Opportunities (>10% divergence)")
    print("=" * 75)
    
    opps = [m for m in markets if abs(m.get("divergence") or 0) > 0.10]
    opps.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)
    
    if not opps:
        print("No high-divergence opportunities right now.")
        return
    
    for m in opps[:5]:
        q = m.get("question", "")
        simmer = m.get("current_probability") or 0
        poly = m.get("external_price_yes") or 0
        div = m.get("divergence") or 0
        resolves = m.get("resolves_at", "Unknown")
        
        if div > 0:
            action = f"AI says BUY YES (AI: {simmer:.0%} vs Market: {poly:.0%})"
        else:
            action = f"AI says BUY NO (AI: {simmer:.0%} vs Market: {poly:.0%})"
        
        print(f"\n📌 {q[:70]}")
        print(f"   {action}")
        print(f"   Divergence: {div:+.1%} | Resolves: {resolves[:10] if resolves else 'TBD'}")


def main():
    parser = argparse.ArgumentParser(description="Simmer AI Divergence Scanner")
    parser.add_argument("--min", type=float, default=5, help="Minimum divergence %% (default: 5)")
    parser.add_argument("--bullish", action="store_true", help="Only bullish divergence (Simmer > Poly)")
    parser.add_argument("--bearish", action="store_true", help="Only bearish divergence (Simmer < Poly)")
    parser.add_argument("--opportunities", "-o", action="store_true", help="Show top opportunities only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("❌ SIMMER_API_KEY environment variable not set")
        print("   Get your API key from: https://simmer.markets/dashboard")
        sys.exit(1)
    
    direction = None
    if args.bullish:
        direction = "bullish"
    elif args.bearish:
        direction = "bearish"
    
    markets = get_markets(api_key)
    
    if args.json:
        filtered = [m for m in markets if abs(m.get("divergence") or 0) >= args.min / 100]
        filtered.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)
        print(json.dumps(filtered, indent=2))
        return
    
    if args.opportunities:
        show_opportunities(markets)
    else:
        format_divergence(markets, args.min, direction)
        show_opportunities(markets)


if __name__ == "__main__":
    main()

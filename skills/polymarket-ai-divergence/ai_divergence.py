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
from pathlib import Path

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

def _load_config(schema, skill_file, config_filename="config.json"):
    """Load config with priority: config.json > env vars > defaults."""
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
        if key in file_cfg:
            result[key] = file_cfg[key]
        elif spec.get("env") and os.environ.get(spec["env"]):
            val = os.environ.get(spec["env"])
            type_fn = spec.get("type", str)
            try:
                result[key] = type_fn(val) if type_fn != str else val
            except (ValueError, TypeError):
                result[key] = spec.get("default")
        else:
            result[key] = spec.get("default")
    return result

def _get_config_path(skill_file, config_filename="config.json"):
    """Get path to config file."""
    return Path(skill_file).parent / config_filename

def _update_config(updates, skill_file, config_filename="config.json"):
    """Update config values and save to file."""
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

# Aliases for compatibility
load_config = _load_config
get_config_path = _get_config_path
update_config = _update_config

# Configuration schema
CONFIG_SCHEMA = {
    "min_divergence": {"env": "SIMMER_DIVERGENCE_MIN", "default": 5.0, "type": float},
    "default_direction": {"env": "SIMMER_DIVERGENCE_DIRECTION", "default": "", "type": str},
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__)

DEFAULT_MIN_DIVERGENCE = _config["min_divergence"]
DEFAULT_DIRECTION = _config["default_direction"]

# SimmerClient singleton
_client = None

def get_client():
    """Lazy-init SimmerClient singleton."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue="polymarket")
    return _client


def get_markets() -> list:
    """Fetch all markets with divergence data."""
    data = get_client()._request("GET", "/api/sdk/markets")
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
        
        is_polymarket = m.get("import_source") in ("polymarket", "kalshi")
        if div > 0.05:
            signal = "🟡 AI>MKT" if is_polymarket else "🟢 BUY"
        elif div < -0.05:
            signal = "🟡 AI<MKT" if is_polymarket else "🔴 SELL"
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
        
        is_external = m.get("import_source") in ("polymarket", "kalshi")
        venue_name = "Kalshi" if m.get("import_source") == "kalshi" else "Polymarket"
        if is_external:
            action = f"Simmer AI: {simmer:.0%} vs {venue_name}: {poly:.0%} — do your own research before trading"
        elif div > 0:
            action = f"AI says BUY YES (AI: {simmer:.0%} vs Market: {poly:.0%})"
        else:
            action = f"AI says BUY NO (AI: {simmer:.0%} vs Market: {poly:.0%})"
        
        print(f"\n📌 {q[:70]}")
        print(f"   {action}")
        print(f"   Divergence: {div:+.1%} | Resolves: {resolves[:10] if resolves else 'TBD'}")


def main():
    parser = argparse.ArgumentParser(description="Simmer AI Divergence Scanner")
    parser.add_argument("--min", type=float, default=DEFAULT_MIN_DIVERGENCE, 
                        help=f"Minimum divergence %% (default: {DEFAULT_MIN_DIVERGENCE})")
    parser.add_argument("--bullish", action="store_true", help="Only bullish divergence (Simmer > Poly)")
    parser.add_argument("--bearish", action="store_true", help="Only bearish divergence (Simmer < Poly)")
    parser.add_argument("--opportunities", "-o", action="store_true", help="Show top opportunities only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--config", action="store_true", help="Show configuration")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set min_divergence=10)")
    args = parser.parse_args()
    
    # Handle --set config updates
    if args.set:
        updates = {}
        for item in args.set:
            if "=" in item:
                key, value = item.split("=", 1)
                if key in CONFIG_SCHEMA:
                    type_fn = CONFIG_SCHEMA[key].get("type", str)
                    try:
                        value = type_fn(value)
                    except (ValueError, TypeError):
                        pass
                updates[key] = value
        if updates:
            update_config(updates, __file__)
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")
    
    # Show config
    if args.config:
        config_path = get_config_path(__file__)
        print("🔮 AI Divergence Scanner Configuration")
        print("=" * 40)
        print(f"Min divergence: {DEFAULT_MIN_DIVERGENCE}%")
        print(f"Default direction: {DEFAULT_DIRECTION or '(none)'}")
        print(f"\nConfig file: {config_path}")
        print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
        print("\nTo change settings:")
        print("  --set min_divergence=10")
        print("  --set default_direction=bullish")
        return
    
    # Validate API key by initializing client
    get_client()

    direction = DEFAULT_DIRECTION or None
    if args.bullish:
        direction = "bullish"
    elif args.bearish:
        direction = "bearish"

    markets = get_markets()
    
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

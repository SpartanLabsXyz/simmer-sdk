#!/usr/bin/env python3
"""
World Cup Delta-Pairs — surface implied probability discrepancies across WC markets.

Fetches all World Cup markets via tags="world-cup" (NOT a bulk limit=700 scan), groups
them by team name, then computes the "delta" between markets at different tournament
stages for the same team. A large delta — e.g., a team priced at 0.05 to win the
tournament but 0.80 to advance from their group — signals potential mispricing between
correlated WC markets.

This is a research/analysis skill. It surfaces opportunities; it does not auto-trade.

Usage:
    python world_cup_delta_pairs.py              # show top 20 delta pairs (dry-run)
    python world_cup_delta_pairs.py --top 10     # show top 10 only
    python world_cup_delta_pairs.py --min-delta 0.15  # filter by minimum delta
    python world_cup_delta_pairs.py --venue sim  # use sim venue prices
    python world_cup_delta_pairs.py --json       # machine-readable JSON output
    python world_cup_delta_pairs.py --config     # show configuration

Spec: [Alyna skill] SIM-3205
"""

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.stdout.reconfigure(line_buffering=True)

SKILL_SLUG = "polymarket-world-cup-delta-pairs"
WC_TAG = "world-cup"

# Minimum markets price to consider (filters out near-certain resolved markets)
MIN_PRICE = 0.01
MAX_PRICE = 0.99

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from simmer_sdk.skill import load_config, get_config_path

CONFIG_SCHEMA = {
    "top":       {"env": "WC_DELTA_TOP",       "default": 20,     "type": int},
    "min_delta": {"env": "WC_DELTA_MIN",        "default": 0.10,   "type": float},
    "venue":     {"env": "TRADING_VENUE",        "default": "sim",  "type": str},
    "limit":     {"env": "WC_DELTA_LIMIT",       "default": 200,    "type": int},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

# ---------------------------------------------------------------------------
# SDK client
# ---------------------------------------------------------------------------

_client = None


def get_client(venue: str = None):
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install 'simmer-sdk>=0.17.0'")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY not set. Get yours at simmer.markets/dashboard → SDK tab.")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue=_resolve_venue(venue))
    elif venue and getattr(_client, "venue", None) != venue:
        _client.venue = venue
    return _client


def _resolve_venue(cli_venue: str = None) -> str:
    return cli_venue or _config.get("venue") or "sim"


# ---------------------------------------------------------------------------
# Market fetching — uses tags="world-cup", NOT limit=700
# ---------------------------------------------------------------------------

# Known WC team names — used to normalise extracted tokens to canonical names.
# Keeps the team-grouping logic robust against minor question-text variations.
_WC_TEAMS = [
    "Argentina", "France", "England", "Brazil", "Spain", "Germany", "Portugal",
    "Netherlands", "Uruguay", "Belgium", "Italy", "Mexico", "USA", "Canada",
    "Australia", "Japan", "South Korea", "Morocco", "Senegal", "Colombia",
    "Chile", "Ecuador", "Peru", "Costa Rica", "Panama", "Honduras", "Jamaica",
    "Trinidad", "Cuba", "Haiti", "Suriname", "El Salvador", "Curaçao",
    "Saudi Arabia", "Iran", "Qatar", "Iraq", "Syria", "Jordan", "UAE",
    "Oman", "Bahrain", "Kuwait", "Yemen", "Lebanon", "Palestine",
    "Switzerland", "Poland", "Croatia", "Serbia", "Denmark", "Austria",
    "Czech Republic", "Hungary", "Slovakia", "Romania", "Bulgaria", "Greece",
    "Turkey", "Norway", "Sweden", "Finland", "Scotland", "Wales", "Ireland",
    "Ukraine", "Russia", "Albania", "Kosovo", "Bosnia", "Slovenia", "Iceland",
    "Nigeria", "Ghana", "Cameroon", "Ivory Coast", "Egypt", "Tunisia", "Algeria",
    "Kenya", "Tanzania", "Zambia", "Zimbabwe", "Mozambique", "DR Congo", "Congo",
    "South Africa", "Ethiopia", "Rwanda", "Uganda", "Cape Verde", "Benin",
    "New Zealand", "Fiji", "Tahiti", "Indonesia", "Thailand", "Vietnam",
    "India", "China", "Malaysia", "Philippines",
]
_TEAM_LOWER = {t.lower(): t for t in _WC_TEAMS}
# Ambiguous short tokens that produce false-positive team matches
_SKIP_TOKENS = {"the", "of", "and", "or", "a", "in", "on", "at", "to", "be",
                "win", "wins", "reach", "will", "vs", "world", "cup", "group",
                "final", "round", "stage", "match", "game", "score", "goals",
                "total", "who", "which", "2026", "fifa", "qualify", "qualify",
                "advance", "advance", "knockout", "semi", "quarter", "from"}


def _extract_teams(question: str) -> List[str]:
    """Extract canonical team names mentioned in a market question."""
    q_lower = question.lower()
    found = []
    for team_lower, team_canonical in _TEAM_LOWER.items():
        if team_lower in _SKIP_TOKENS:
            continue
        # Whole-word match to avoid e.g. "iran" inside "Ukraine"
        if re.search(r'\b' + re.escape(team_lower) + r'\b', q_lower):
            found.append(team_canonical)
    return found


def _market_stage(question: str) -> str:
    """Classify a WC market into a tournament stage bucket."""
    q = question.lower()
    if any(k in q for k in ("winner", "champion", "win the world cup", "win the 2026")):
        return "champion"
    if any(k in q for k in ("final", "reach the final", "make the final")):
        return "final"
    if any(k in q for k in ("semi", "semifinal", "semi-final", "top 4")):
        return "semifinal"
    if any(k in q for k in ("quarter", "top 8")):
        return "quarterfinal"
    if any(k in q for k in ("round of 16", "last 16", "top 16")):
        return "round16"
    if any(k in q for k in ("advance", "qualify", "knockout", "pass", "progress")):
        return "advance"
    if any(k in q for k in ("win group", "group winner", "top of group", "finish first")):
        return "group_win"
    if any(k in q for k in ("group", "finish second", "top 2 in group", "from group")):
        return "group"
    return "other"


# Stage ordering: higher index = deeper in tournament (should have lower or
# correlated probability). Used to compute meaningful deltas.
_STAGE_ORDER = {
    "group": 0,
    "group_win": 1,
    "advance": 2,
    "round16": 3,
    "quarterfinal": 4,
    "semifinal": 5,
    "final": 6,
    "champion": 7,
    "other": -1,
}


def fetch_wc_markets(venue: str = None, limit: int = 200) -> List[dict]:
    """Fetch all active WC markets using tags="world-cup".

    Uses a targeted tag filter — NOT a bulk limit=700 scan of all markets.
    The tag filter is applied server-side, so only WC markets are transferred.
    """
    client = get_client(venue)
    try:
        markets = client.get_markets(
            tags=WC_TAG,
            status="active",
            limit=limit,
            sort="volume",
        )
    except Exception as e:
        print(f"❌ Error fetching WC markets: {e}")
        sys.exit(1)

    result = []
    for m in markets:
        # Accept both Market objects (SDK) and plain dicts
        if hasattr(m, "__dict__"):
            md = {
                "id":       getattr(m, "id", None),
                "question": getattr(m, "question", "") or "",
                "yes_ask":  getattr(m, "yes_ask", None),
                "yes_bid":  getattr(m, "yes_bid", None),
                "yes_price": getattr(m, "yes_price", None),
                "volume":   getattr(m, "volume", 0) or 0,
                "tags":     getattr(m, "tags", []) or [],
            }
        else:
            md = {
                "id":       m.get("id"),
                "question": m.get("question", "") or "",
                "yes_ask":  m.get("yes_ask"),
                "yes_bid":  m.get("yes_bid"),
                "yes_price": m.get("yes_price"),
                "volume":   m.get("volume", 0) or 0,
                "tags":     m.get("tags", []) or [],
            }
        # Use mid-price: average of bid and ask if available, else yes_price
        ask = md.get("yes_ask")
        bid = md.get("yes_bid")
        if ask is not None and bid is not None:
            try:
                mid = (float(ask) + float(bid)) / 2.0
            except (TypeError, ValueError):
                mid = None
        else:
            mid = md.get("yes_price")
        try:
            mid = float(mid) if mid is not None else None
        except (TypeError, ValueError):
            mid = None
        if mid is None or not (MIN_PRICE <= mid <= MAX_PRICE):
            continue
        md["mid_price"] = mid
        md["stage"] = _market_stage(md["question"])
        md["teams"] = _extract_teams(md["question"])
        result.append(md)

    return result


# ---------------------------------------------------------------------------
# Delta-pair computation
# ---------------------------------------------------------------------------

def compute_delta_pairs(markets: List[dict]) -> List[dict]:
    """
    Group markets by team and find pairs where the prices imply an inconsistency.

    For each (team, stage_A, stage_B) pair where stage_A < stage_B (A is an
    earlier round), the implied conditional is:

        implied_B_given_A = price_B / price_A

    If implied_B_given_A > 1.0, price_B > price_A — a later stage is priced
    HIGHER than an earlier one, which is structurally impossible (can't win the
    final without reaching it). That's the strongest delta signal.

    Even when B < A (as expected), if the ratio is unusually high or low vs.
    historical WC base rates, it surfaces as a notable delta.

    We return pairs sorted by delta magnitude (abs difference price_A - price_B
    adjusted for stage ordering).
    """
    # Index by team
    by_team: Dict[str, List[dict]] = {}
    for m in markets:
        for team in m["teams"]:
            by_team.setdefault(team, []).append(m)

    pairs = []
    seen = set()

    for team, team_markets in by_team.items():
        # Only teams with 2+ markets across different stages
        staged = [(m, _STAGE_ORDER.get(m["stage"], -1)) for m in team_markets
                  if m["stage"] != "other" and _STAGE_ORDER.get(m["stage"], -1) >= 0]
        if len(staged) < 2:
            continue

        for i, (m_a, order_a) in enumerate(staged):
            for m_b, order_b in staged[i + 1:]:
                if m_a["id"] == m_b["id"]:
                    continue
                pair_key = tuple(sorted([m_a["id"], m_b["id"]]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                # Ensure A is the earlier stage
                if order_a > order_b:
                    m_a, m_b = m_b, m_a
                    order_a, order_b = order_b, order_a

                p_a = m_a["mid_price"]
                p_b = m_b["mid_price"]

                # Core delta: how much does B deviate from what A implies?
                # Structurally p_b should be <= p_a (can't win final without advancing)
                # If p_b > p_a: structural impossibility — very strong signal
                # If p_b < p_a: normal, but how big is the implied conditional?
                stage_gap = order_b - order_a
                if stage_gap == 0:
                    continue

                if p_a > 0:
                    implied_conditional = p_b / p_a
                else:
                    continue

                # Delta score: raw price difference weighted by stage gap
                raw_delta = abs(p_a - p_b)
                # Structural inversion is flagged separately
                is_inverted = p_b > p_a

                pairs.append({
                    "team":               team,
                    "market_a":           {
                        "id":       m_a["id"],
                        "question": m_a["question"],
                        "stage":    m_a["stage"],
                        "price":    p_a,
                        "volume":   m_a["volume"],
                    },
                    "market_b":           {
                        "id":       m_b["id"],
                        "question": m_b["question"],
                        "stage":    m_b["stage"],
                        "price":    p_b,
                        "volume":   m_b["volume"],
                    },
                    "stage_gap":          stage_gap,
                    "implied_conditional": round(implied_conditional, 4),
                    "raw_delta":          round(raw_delta, 4),
                    "is_inverted":        is_inverted,
                    # Sort key: inverted pairs first, then by raw delta desc
                    "_sort_key":          (int(is_inverted), raw_delta),
                })

    pairs.sort(key=lambda x: x["_sort_key"], reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(pairs: List[dict], top: int, min_delta: float, venue: str) -> None:
    filtered = [p for p in pairs if p["raw_delta"] >= min_delta]
    shown = filtered[:top]

    print(f"\n⚽ World Cup Delta-Pairs — Implied Probability Discrepancies")
    print(f"   Venue: {venue}  |  Markets scanned: via tags=\"{WC_TAG}\"")
    print(f"   Showing top {len(shown)} of {len(filtered)} pairs (min delta {min_delta:.0%})")
    print("=" * 70)

    if not shown:
        print(f"\n  No pairs found with delta ≥ {min_delta:.0%}.")
        print("  Try --min-delta 0.05 or check market availability.")
        return

    for i, p in enumerate(shown, 1):
        flag = " ⚠️  INVERTED" if p["is_inverted"] else ""
        print(f"\n  {i:2}. {p['team']}{flag}")
        ma, mb = p["market_a"], p["market_b"]
        print(f"      Stage {ma['stage']:12s} ({ma['price']:.3f})  {ma['question'][:55]}")
        print(f"      Stage {mb['stage']:12s} ({mb['price']:.3f})  {mb['question'][:55]}")
        cond = p["implied_conditional"]
        delta = p["raw_delta"]
        inv_note = " (B > A — structurally impossible)" if p["is_inverted"] else ""
        print(f"      Delta: {delta:.3f}  |  Implied B|A: {cond:.3f}{inv_note}")
        print(f"      Volume A: ${ma['volume']:,.0f}  Volume B: ${mb['volume']:,.0f}")

    print(f"\n{'─' * 70}")
    inverted_count = sum(1 for p in shown if p["is_inverted"])
    if inverted_count:
        print(f"⚠️   {inverted_count} structurally-inverted pair(s) — later stage priced HIGHER than earlier stage.")
    print(f"💡  Large deltas may indicate genuine mispricing or thin books. Verify volume before trading.")
    print()


def print_json(pairs: List[dict], top: int, min_delta: float) -> None:
    filtered = [p for p in pairs if p["raw_delta"] >= min_delta][:top]
    for p in filtered:
        del p["_sort_key"]
    print(json.dumps({"pairs": filtered, "total": len(filtered)}, indent=2))


def print_config(venue: str) -> None:
    config_path = get_config_path(__file__)
    print("\n⚽ WC Delta-Pairs Configuration")
    print("=" * 40)
    print(f"API key:     {'✅ Set' if os.environ.get('SIMMER_API_KEY') else '❌ Not set'}")
    print(f"Venue:       {venue}")
    print(f"Top N:       {_config['top']}")
    print(f"Min delta:   {_config['min_delta']:.0%}")
    print(f"Limit:       {_config['limit']} (markets fetched per run)")
    print(f"Config file: {config_path}")
    print()


# ---------------------------------------------------------------------------
# Automaton support
# ---------------------------------------------------------------------------

def _emit_automaton(pairs: List[dict], min_delta: float) -> None:
    if not os.environ.get("AUTOMATON_MANAGED"):
        return
    filtered = [p for p in pairs if p["raw_delta"] >= min_delta]
    inverted = [p for p in filtered if p["is_inverted"]]
    print(json.dumps({"automaton": {
        "signals": len(filtered),
        "inverted_pairs": len(inverted),
        "top_delta": round(filtered[0]["raw_delta"], 4) if filtered else 0,
        "trades_attempted": 0,
        "trades_executed": 0,
    }}))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="World Cup Delta-Pairs — find implied probability discrepancies across WC markets"
    )
    parser.add_argument("--top",       type=int,   default=None, help="Number of pairs to show (default: 20)")
    parser.add_argument("--min-delta", type=float, default=None, help="Minimum delta to include (default: 0.10)")
    parser.add_argument("--venue",     type=str,   choices=["sim", "polymarket"],
                        help="Venue: sim (default) or polymarket")
    parser.add_argument("--limit",     type=int,   default=None,
                        help="Max WC markets to fetch (default: 200, max recommended: 500)")
    parser.add_argument("--json",      action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--config",    action="store_true", help="Show configuration")
    args = parser.parse_args()

    top = args.top if args.top is not None else int(_config.get("top", 20))
    min_delta = args.min_delta if args.min_delta is not None else float(_config.get("min_delta", 0.10))
    venue = _resolve_venue(args.venue)
    limit = args.limit if args.limit is not None else int(_config.get("limit", 200))

    if args.config:
        print_config(venue)
        return

    if not args.json:
        print(f"\n⚽ Fetching World Cup markets (tags=\"{WC_TAG}\", limit={limit})…")

    markets = fetch_wc_markets(venue=venue, limit=limit)

    if not args.json:
        print(f"   Found {len(markets)} active WC markets with usable prices.")

    pairs = compute_delta_pairs(markets)

    if args.json:
        print_json(pairs, top, min_delta)
    else:
        print_report(pairs, top, min_delta, venue)

    _emit_automaton(pairs, min_delta)


if __name__ == "__main__":
    main()

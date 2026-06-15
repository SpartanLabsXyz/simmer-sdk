#!/usr/bin/env python3
"""
Combo Builder — place an atomic Polymarket combo (parlay) as one signed RFQ order.

Dry-run by default: resolves legs, shows the product-of-legs estimate, and
prints the placement plan WITHOUT opening a socket, signing, or moving money.
`--live` places for real (money path).

Usage:
    python combo_builder.py            # dry-run
    python combo_builder.py --live     # real placement
    python combo_builder.py --legs     # browse combo-eligible legs

Config (combo_config.json, from combo_config.example.json):
    {
      "stake_usd": 1.0,
      "side": "YES",
      "direction": "BUY",
      "legs": [
        {"position_id": "<chosen-side CTF token id>", "label": "Brazil to win"},
        {"position_id": "<...>", "label": "Over 2.5 goals"}
      ]
    }
"""
import argparse
import json
import os
import sys

CONFIG_PATH = os.getenv("COMBO_CONFIG", "combo_config.json")


def _load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"Missing {CONFIG_PATH}. Copy combo_config.example.json and fill in legs.")
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    legs = cfg.get("legs") or []
    if len(legs) < 2:
        sys.exit("A combo needs at least 2 legs in combo_config.json.")
    for leg in legs:
        if not leg.get("position_id"):
            sys.exit(f"Leg missing position_id: {leg!r}")
    return cfg


def browse_legs():
    from simmer_sdk import combo
    legs = combo.fetch_combo_legs(limit=100, max_legs=60)
    print(f"{len(legs)} combo-eligible legs (showing up to 40):\n")
    for m in legs[:40]:
        pos = m.get("position_ids") or []
        prices = m.get("outcome_prices") or []
        slug = m.get("slug", "")
        yes_p = prices[0] if prices else "?"
        yes_tok = pos[0] if pos else "?"
        print(f"  {slug:<48}  YES={yes_p:<6}  token={str(yes_tok)[:18]}…")
    print("\nPick the chosen-side token id (position_ids[0]=YES, [1]=NO) per leg "
          "into combo_config.json.")


def run(live: bool):
    cfg = _load_config()
    legs = cfg["legs"]
    stake = float(cfg.get("stake_usd", 1.0))
    side = cfg.get("side", "YES")
    direction = cfg.get("direction", "BUY")

    from simmer_sdk import combo
    from simmer_sdk.client import SimmerClient

    # Pre-quote preview (offline product-of-legs; the real quote comes from RFQ).
    leg_prices = [leg.get("price") for leg in legs if leg.get("price") is not None]
    est = combo.estimate_combo_price(leg_prices, stake=stake) if len(leg_prices) >= 2 else None

    print("=== Combo slip ===")
    for i, leg in enumerate(legs, 1):
        print(f"  Leg {i}: {leg.get('label', leg['position_id'])}"
              + (f"  (~{leg['price']})" if leg.get("price") is not None else ""))
    print(f"  Side: {side}  Direction: {direction}  Stake: ${stake:.2f}")
    if est:
        print(f"  Pre-quote estimate: combined ~{est['combined_price']:.3f} "
              f"-> ~{est['multiplier']:.1f}x  (potential ~${est['potential_payout']:.2f})")
    print("  RISK: every leg must hit to win. Any single leg losing = TOTAL LOSS "
          f"of the ${stake:.2f} stake. Not risk-free; higher variance than the legs.\n")

    api_key = os.getenv("SIMMER_API_KEY")
    if not api_key:
        sys.exit("Set SIMMER_API_KEY (simmer.markets/dashboard -> SDK tab).")

    client = SimmerClient(api_key=api_key, live=live)
    leg_ids = [str(leg["position_id"]) for leg in legs]

    if not live:
        plan = client.place_combo(
            leg_position_ids=leg_ids, size_usdc=stake, side=side,
            direction=direction, dry_run=True,
        )
        print("=== DRY RUN (no socket, no signing, no money) ===")
        print(json.dumps(plan, indent=2))
        print("\nReview the resolved identity + size above, then re-run with --live.")
        return

    print("=== LIVE — placing combo (money path) ===")
    result = client.place_combo(
        leg_position_ids=leg_ids, size_usdc=stake, side=side,
        direction=direction, dry_run=False,
        on_status=lambda s: print(f"  [{s}]"),
    )
    print("Result:")
    print(json.dumps(result, indent=2))
    if result.get("tx_hash"):
        print(f"\nFilled. tx: https://polygonscan.com/tx/{result['tx_hash']}")


def main():
    ap = argparse.ArgumentParser(description="Place an atomic Polymarket combo (parlay).")
    ap.add_argument("--live", action="store_true", help="Place for real (money path). Default: dry-run.")
    ap.add_argument("--legs", action="store_true", help="Browse combo-eligible legs and exit.")
    args = ap.parse_args()
    if args.legs:
        browse_legs()
        return
    run(live=args.live)


if __name__ == "__main__":
    main()

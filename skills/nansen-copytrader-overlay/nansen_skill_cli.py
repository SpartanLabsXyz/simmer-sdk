#!/usr/bin/env python3
"""
CLI wrapper for the Simmer x Nansen skills — the runnable entry point
referenced by SKILL.md.

Nansen is a data layer here, nothing more: PM PnL quality overlay
(pnl-by-market primary signal) + address-summary/wallet quality. This is
NOT smart-money-labeled copytrading — Nansen's smart-money labels are not
Polymarket-specific and no code path in this repo calls them.

Subcommands:
    copytrader-overlay   Rank leaders for a target market. pnl-by-market for
                         that market is the PRIMARY signal; address-summary
                         + pnl-by-address/trades-by-address are a secondary
                         refinement, gated to control credit spend.
    insider-scan         EXPERIMENTAL — dry-run only. Refuses --live.

Examples:
    python3 nansen_skill_cli.py copytrader-overlay \\
        --market-id 12345 --leaders leaders.json --top-n 5

    python3 nansen_skill_cli.py copytrader-overlay \\
        --market-id 12345 --leaders leaders.json --worldcup

    python3 nansen_skill_cli.py insider-scan --market-id 12345 67890

leaders.json shape:
    [
      {"proxy_address": "0x...", "owner_address": "0x...", "wallet_score": 0.8}
    ]
    proxy_address is used for PM endpoints (pnl-by-market, pnl-by-address,
    trades-by-address, address-summary); owner_address for profiler endpoints
    (historical-balances). owner_address is OPTIONAL — it is read off
    the pnl-by-market leaderboard row when omitted.
    `address` is accepted as a legacy alias for proxy_address only.

This is a bring-your-own-key skill: every call spends the user's own Nansen
credits, so the --max-wallets/--max-credits defaults are deliberately
conservative. Raise them explicitly if you want a wider sweep.
"""

import argparse
import json
import sys

from nansen_adapter import DEFAULT_MAX_CREDITS_PER_RUN
from nansen_copytrader_overlay_general import DEFAULT_MAX_WALLETS


def _cmd_copytrader_overlay(args):
    with open(args.leaders) as f:
        leaders = json.load(f)

    if args.worldcup:
        from nansen_copytrader_overlay_worldcup import enrich_leaders
    else:
        from nansen_copytrader_overlay_general import enrich_leaders

    from nansen_adapter import CreditGuard
    guard = CreditGuard(max_credits=args.max_credits)

    result = enrich_leaders(
        leaders,
        market_id=args.market_id,
        dry_run=not args.live,
        top_n=args.top_n,
        max_wallets=args.max_wallets,
        guard=guard,
    )
    print(json.dumps(result, indent=2))
    print(
        f"[credits] {guard.credits_spent}/{guard.max_credits} Nansen credits used this run",
        file=sys.stderr,
    )


def _cmd_insider_scan(args):
    from nansen_live_insider_scan import scan_live_markets, LiveTradingNotSupportedError

    if args.live:
        print(
            "[REFUSED] insider-scan is experimental and dry-run only until "
            "owner/proxy wallet handling and NO-side price semantics are "
            "confirmed against the live Nansen API. See the module docstring "
            "in nansen_live_insider_scan.py.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        signals = scan_live_markets(
            market_ids=args.market_id or None,
            dry_run=True,
            top_markets=args.top_markets,
        )
    except LiveTradingNotSupportedError as e:
        print(f"[REFUSED] {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps([s.as_dict() for s in signals], indent=2))


def _positive_int(raw: str) -> int:
    """argparse type for the credit caps: reject 0 and negatives at parse time.

    Both caps spend the user's own Nansen credits, and both fail badly on a
    nonpositive value (--max-calls 0 trips the guard on the first request;
    --max-wallets 0 skips the cap and enriches everything). The library layer
    raises ValueError for programmatic callers; this turns the CLI case into a
    normal usage error instead of a traceback.
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {raw!r}")
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"must be >= 1, got {value} (this is a credit cap; 0 and negative "
            "values would spend more credits, not fewer)"
        )
    return value


def main():
    parser = argparse.ArgumentParser(
        prog="nansen_skill_cli",
        description="Simmer x Nansen skills CLI — data-layer-only overlays for copytrading",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    overlay = sub.add_parser(
        "copytrader-overlay",
        help="Rank leaders for a target market (pnl-by-market is the primary signal)",
    )
    overlay.add_argument("--market-id", required=True,
                          help="Target Polymarket market_id to rank leaders for")
    overlay.add_argument("--leaders", required=True,
                          help="Path to a JSON file: list of "
                               "{proxy_address, owner_address, wallet_score}")
    overlay.add_argument("--worldcup", action="store_true",
                          help="Use the World Cup-tuned overlay variant")
    overlay.add_argument("--top-n", type=int, default=None)
    # Defaults come from the constants themselves so the help text can never
    # drift from the actual value again.
    overlay.add_argument("--max-wallets", type=_positive_int, default=DEFAULT_MAX_WALLETS,
                          help="Hard cap on leaders enriched this run "
                               f"(credit guard, default {DEFAULT_MAX_WALLETS})")
    overlay.add_argument("--max-credits", type=_positive_int, default=DEFAULT_MAX_CREDITS_PER_RUN,
                          help="Hard cap on Nansen credits spent this run "
                               f"(credit guard, default {DEFAULT_MAX_CREDITS_PER_RUN})")
    overlay.add_argument("--live", action="store_true",
                          help="Mark output dry_run=False. This CLI never places trades "
                               "itself either way — the caller still owns that gate.")
    overlay.set_defaults(func=_cmd_copytrader_overlay)

    insider = sub.add_parser(
        "insider-scan",
        help="EXPERIMENTAL — dry-run only. --live is refused, not just discouraged.",
    )
    insider.add_argument("--market-id", nargs="*", default=None,
                          help="Market IDs to scan (default: auto-discover top markets)")
    insider.add_argument("--top-markets", type=_positive_int, default=10)
    insider.add_argument("--live", action="store_true",
                          help="NOT SUPPORTED YET — prints why and exits nonzero")
    insider.set_defaults(func=_cmd_insider_scan)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Core-flow smoke test against the live Nansen API over HTTPS.

Proves the ported adapter works end-to-end without the `nansen` CLI:
account -> pnl-by-market (primary signal) -> address-summary (secondary
refinement) -> overlay ranking.

This SPENDS CREDITS. It is deliberately tiny — one market, two wallets,
a hard eight-call ceiling — so it costs single-digit credits per run.
Reads NANSEN_API_KEY from the environment; never prints it.

    export NANSEN_API_KEY=...
    python3 scripts/smoke_test.py --market-id 2063134

Output is a markdown transcript on stdout. Addresses are public on-chain
identifiers and are left intact; nothing else about the account is echoed
beyond plan name and credit balance.
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nansen_adapter as na
import nansen_copytrader_overlay_general as og

MAX_CREDITS = 20   # ~1 pnl-by-market (5) + a handful of 1-credit calls
MAX_WALLETS = 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market-id", default="2063134",
                    help="Polymarket market_id to smoke-test against")
    args = ap.parse_args()

    if not os.environ.get("NANSEN_API_KEY"):
        print("NANSEN_API_KEY is not set — refusing to run.", file=sys.stderr)
        return 2

    out = print
    out(f"# Nansen core-flow smoke test\n")
    out(f"- Run at: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    out(f"- Market: `{args.market_id}`")
    out(f"- Transport: direct HTTPS to `{na.NANSEN_API_BASE}` (stdlib urllib, no CLI)")
    out(f"- Caps: max_credits={MAX_CREDITS}, max_wallets={MAX_WALLETS}\n")

    # 1. account — free and uncounted, so this never costs anything.
    acct = na.account()
    out("## 1. `GET /account` (free, uncounted)\n")
    out(f"```\nplan={acct.get('plan')!r} credits_remaining={acct.get('credits_remaining')}\n```\n")
    start_credits = acct.get("credits_remaining")

    guard = na.CreditGuard(max_credits=MAX_CREDITS)

    # 2. pnl-by-market — the primary signal.
    out("## 2. `POST prediction-market/pnl-by-market` (primary signal)\n")
    rows = na.pnl_by_market(args.market_id, limit=10, guard=guard)
    out(f"Returned **{len(rows)} rows**. Top 3 by PnL:\n")
    out("| # | proxy | owner | total_pnl_usd | net_buy_cost_usd | roi |")
    out("|---|---|---|---|---|---|")
    for i, r in enumerate(rows[:3], 1):
        owner = na.owner_address_from_row(r) or "_(placeholder `0x`)_"
        roi = na.compute_roi(r.get("net_buy_cost_usd", 0.0),
                             r.get("total_pnl_usd", 0.0))
        out(f"| {i} | `{r.get('address', '')}` | `{owner}` | "
            f"{r.get('total_pnl_usd')} | {r.get('net_buy_cost_usd')} | {roi} |")
    out("")

    placeholders = sum(1 for r in rows if not na.owner_address_from_row(r))
    out(f"Owner-address coverage this page: **{len(rows) - placeholders}/{len(rows)}** "
        f"usable ({placeholders} carried the placeholder `0x`).\n")

    if not rows:
        out("No rows returned — cannot continue the flow.")
        return 1

    # 3. address-summary on the top wallet — keyed by PROXY, not owner.
    top_proxy = rows[0].get("address", "")
    out("## 3. `POST prediction-market/address-summary` (proxy-keyed)\n")
    summary = na.address_summary(top_proxy, guard=guard)
    out(f"Called with the **proxy** address `{top_proxy}`:\n")
    out("```")
    for k in ("address", "markets_traded", "markets_won", "resolved_count",
              "win_rate", "realized_pnl_usd", "total_pnl_usd"):
        out(f"{k} = {summary.get(k)!r}")
    out("```\n")

    # 4. the overlay itself, over the same guard so the cap is shared.
    out("## 4. Overlay ranking (`enrich_leaders`)\n")
    leaders = [
        {"proxy_address": r.get("address", ""),
         "owner_address": na.owner_address_from_row(r),
         "wallet_score": 1.0}
        for r in rows[:MAX_WALLETS]
    ]
    enriched = og.enrich_leaders(
        leaders, market_id=args.market_id,
        max_wallets=MAX_WALLETS, guard=guard,
    )
    out("| proxy | original | adjusted | reason_tags |")
    out("|---|---|---|---|")
    for lead in enriched:
        tags = ", ".join(lead.get("reason_tags", [])) or "—"
        out(f"| `{lead.get('proxy_address', '')}` | {lead.get('wallet_score')} | "
            f"{lead.get('adjusted_score')} | {tags} |")
    out("")

    # 5. credit accounting.
    end = na.account()
    out("## 5. Credit accounting\n")
    out("```")
    out(f"credits before : {start_credits}")
    out(f"credits after  : {end.get('credits_remaining')}")
    if isinstance(start_credits, int) and isinstance(end.get("credits_remaining"), int):
        out(f"spent          : {start_credits - end['credits_remaining']}")
    out(f"guard credits  : {guard.credits_spent}/{MAX_CREDITS}")
    out("```")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

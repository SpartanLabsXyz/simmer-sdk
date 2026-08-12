---
name: nansen-copytrader-overlay
description: Rank Polymarket copytrading leaders using Nansen as a data layer — pnl-by-market for the target market is the primary signal, address-summary/pnl-by-address is a secondary, credit-guarded refinement. Includes an experimental (dry-run only) live insider scan. Use when Simmer needs to re-rank or filter a leader list before executing copytrading rebalances on a specific Polymarket market.
tags:
  - copytrading
  - polymarket
  - nansen
metadata:
  author: Simmer (@simmer_markets)
  version: "0.2.1"
  displayName: Nansen Copytrader Overlay
  difficulty: intermediate
  simmer:
    credit:
      name: "Alyna Takahashi"
      url: "https://github.com/alyna123t"
      label: by
---

# Nansen Copytrader Overlay

Nansen is a **data layer only** in this skill: PM PnL quality overlay +
address-summary/wallet quality. It is **not** smart-money-labeled
copytrading — Nansen's smart-money labels are not Polymarket-specific, and
no code path here calls the labels endpoint at all.

## When to use this skill

Simmer is about to execute a copytrading rebalance into a specific
Polymarket market and has a candidate leader list (wallets + an existing
`wallet_score`). Use this skill to re-rank that list using Nansen's
Polymarket-specific PnL data before Simmer acts on it.

Do **not** use this skill to:
- Generate smart-money labels or entity classifications (out of scope — see framing above).
- Place trades. Every function and CLI command here is signal-only; the
  caller (Simmer's execution wrapper) owns the actual `--live` gate.

## Core signal: pnl-by-market first

The primary ranking signal is `pnl_by_market(market_id)` — who is actually
profitable **in the target market**, not a wallet's broad trading history.
`pnl_by_address` / `trades_by_address` (broad history) and
`address_summary` (cheap wallet-quality pre-filter) are secondary —
they refine the market-specific score, they never replace it. A wallet
absent from the target market's leaderboard gets tagged
`NOT_IN_MARKET_LEADERBOARD` and keeps its original score with a small
discount, rather than a fabricated 50/50 blend against a signal that
doesn't exist.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pytest   # for tests only; the skill itself is stdlib-only
export NANSEN_API_KEY=...   # your own Nansen key — sent as the `apiKey` header
```

No `nansen` CLI required: calls go straight to `https://api.nansen.ai/api/v1`
over HTTPS. Bring-your-own-key — every call spends **your** Nansen credits,
so `--max-wallets` (default 30) and `--max-credits` (default 45) are
deliberately conservative.

### Getting a Nansen key

Sign up at **[nsn.ai/simmer](https://nsn.ai/simmer)**, then copy your key from
the Nansen dashboard. Signing up through that link gets you the Simmer partner
discount on your subscription and API credits.

**What a run costs.** Credit prices measured directly against the live API on
2026-08-11:

| Endpoint | Credits |
|---|---|
| `pnl-by-market` (primary signal, once per run) | 5 |
| `top-holders` | 5 |
| `address-summary`, `pnl-by-address`, `trades-by-address`, `market-screener` | 1 |
| `account` (balance check) | 0 |

At the default caps a full run costs **about 45 credits**. Note that Nansen's
published price list currently shows `pnl-by-market` at 1 credit; we are billed
5, so budget from the table above rather than from their docs.

**Which plan you need.** Nansen's free tier gives 100 starter credits plus 10
per day, which covers roughly two full runs and is enough to try the skill. For
regular use, Pro (2,000 credits per month) covers about 45 runs. Check your
balance any time with `GET /api/v1/account`, which is free.

Rank leaders for a market:

```bash
python3 nansen_skill_cli.py copytrader-overlay \
    --market-id 12345 \
    --leaders leaders.json \
    --top-n 5
```

`leaders.json`:

```json
[
  {"proxy_address": "0xabc...", "owner_address": "0xowner...", "wallet_score": 0.8}
]
```

World Cup market variant (adds WC-specialist bonus to the secondary signal):

```bash
python3 nansen_skill_cli.py copytrader-overlay \
    --market-id 12345 --leaders leaders.json --worldcup
```

Experimental live insider scan (always dry-run — see below):

```bash
python3 nansen_skill_cli.py insider-scan --market-id 12345 67890
```

Programmatic use (what `copytrading_strategy.py` would actually call):

```python
from nansen_copytrader_overlay_general import enrich_leaders

enriched = enrich_leaders(
    leaders=raw_leaders,           # [{proxy_address, owner_address, wallet_score}, ...]
    market_id=target_market_id,    # the market Simmer is about to copytrade into
    dry_run=not is_live,
)
target_wallets = [l["proxy_address"] for l in enriched[:top_n]]
```

## Proxy vs. owner wallets — must be explicit

Nansen indexes Polymarket activity (`prediction-market/...`) by the
**proxy** wallet (the contract Polymarket trades through), and profiler
data (`profiler/...`) by the **owner** (signing/EOA) wallet.
`address-summary` is a prediction-market endpoint, so it takes the
**proxy** — passing the owner returns an all-zero row.

`owner_address` is optional on input: it rides along on every
`pnl-by-market` row and is read from there when omitted. That harvest
covers only about **40% of the leaderboard** — 30 of 50 rows on the market
used to verify this carried the placeholder `"0x"` rather than a real
owner, which is treated as absent.

| Field | Used for | Endpoints |
|---|---|---|
| `proxy_address` (or legacy `address`) | Polymarket-specific data | `pnl-by-market`, `pnl-by-address`, `trades-by-address`, `top-holders`, `address-summary` |
| `owner_address` (optional) | General on-chain wallet quality | `historical-balances` |

A leader missing `proxy_address` is tagged `MISSING_PROXY_ADDRESS` and
skipped (kept at its original score, discounted). Missing `owner_address`
is tagged `MISSING_OWNER_ADDRESS` for the record but does **not** gate the
secondary signal — every call in that path is proxy-keyed.

## Measured leaders always rank above unmeasured ones

Each returned leader carries `nansen_measured`. Leaders with a usable Nansen
signal are ranked as one block **above** every leader without one; within each
block the order is by `adjusted_score`.

This matters whenever your leader list is longer than `max_wallets`, or when
some lookups fail. The two scores are not on the same scale — an unmeasured
leader keeps `base * 0.9`, a measured one gets `0.5*base + 0.5*quality`, which
only exceeds `0.9` when quality is above `0.8`. Ranking them together would
promote wallets the overlay could not evaluate above nearly every wallet it
did, which is the opposite of the point.

If every leader is measured (the intended use), ranking is purely by
`adjusted_score` and this rule changes nothing.

## Credit guards (hard, not advisory)

Nansen bills per call against **your** key, so every enrichment run is
protected by:

- **`max_wallets`** (default 30): leaders beyond the cap are never
  enriched — they keep their original score, tagged
  `CREDIT_GUARD_MAX_WALLETS`, and are **ranked below every leader that was
  measured** (see below).
- **`CreditGuard(max_credits=...)`** (default 45): a hard ceiling on the
  **credits** spent across the whole run, with a 5-minute TTL cache so
  re-fetching the same market/wallet doesn't double-spend. It charges the real
  per-endpoint cost (5 for `pnl-by-market` and `top-holders`, 1 for the rest),
  not one unit per call, so the number means what you think it means. If the
  budget runs out mid-run, remaining leaders are tagged
  `CREDIT_GUARD_EXHAUSTED` and kept at their original score — the run does not
  crash, and it does not keep spending.
- **Preflight balance check**: before spending anything, the CLI calls the free
  `account()` endpoint. If your balance can't cover even the primary
  `pnl-by-market` call it refuses outright; if your balance is below the budget
  it warns and proceeds, since the budget is a cap rather than a prediction and
  most runs spend well under it. A failed balance check never blocks the run.
- **No `profiler labels` wrapper exists**, so nothing here can call it
  (100 credits for common labels, 500 for premium). The live insider scan's
  known-entity discount uses a free local allowlist instead.
- **`address_summary` as a pre-filter**: a wallet only gets the more
  expensive `pnl_by_address` + `trades_by_address` pull if its cheap
  `address_summary` win-rate/resolved-count check clears
  `MIN_RESOLVED_MARKETS` first.

Tune both via the CLI (`--max-wallets`, `--max-credits`) or by passing
`max_wallets=` / `guard=CreditGuard(max_credits=...)` directly.

## Live insider scan — experimental, dry-run only

`nansen_live_insider_scan.py` / `insider-scan` is **not launchable for live
trading**. `scan_live_markets(dry_run=False)` and `--live` both hard-refuse
(`LiveTradingNotSupportedError`), because two things are unconfirmed
against the live API:

1. **Owner/proxy handling**: scanned wallets come from PM trade data (proxy
   addresses), but `historical_balances` is a profiler/owner-indexed
   endpoint — `wallet_age_days` may be silently wrong until there's an
   owner mapping for scanned wallets.
2. **`HIGH_NO_ENTRY` price semantics**: whether Nansen quotes NO-side
   trades in YES terms or the NO token's own price changes whether this
   flag means conviction or the opposite. Unconfirmed — see the module
   docstring.

Removing the guard requires a deliberate code change after both are
verified — not a flag flip.

## Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

## Status

This skill ships as **research tooling, not an allocation signal.** The
scoring weights (the 50/50 blend, `MIN_RESOLVED_MARKETS`, the tag penalties)
are reasoned defaults. They have not been fitted or backtested, and no
forward test has shown that the re-ranking improves realised copytrading
returns. Read `DISCLAIMER.md` before acting on the output.

## Credits

Built by **Alyna Takahashi** ([@alyna123t](https://github.com/alyna123t)),
who wrote the adapter, both overlays and the insider scan. Code review by
**Nick** ([@BridgeAISocial](https://github.com/BridgeAISocial)). Published and
maintained by Simmer. Nansen provides the underlying Polymarket data.

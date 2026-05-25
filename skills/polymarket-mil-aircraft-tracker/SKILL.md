---
name: polymarket-mil-aircraft-tracker
description: Trade Polymarket strike/action markets using military aircraft ADS-B positioning via pref.trade. Fires when tracked mil aircraft cluster in a target region.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "1.0.0"
  displayName: "Polymarket Military Aircraft Tracker"
  difficulty: "intermediate"
---
# Polymarket Military Aircraft Tracker

Trade Polymarket strike/action markets using military aircraft ADS-B positioning via pref.trade.

> **Framework, not a production trading system.** Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting to a wallet with real funds.

> **Template skill.** Defaults to dry-run mode. The `--live` flag is a deliberate opt-in for real-money execution. Configure tunables via env vars listed below.

## Safety Rails

This skill executes real-money trades on Polymarket only when the `--live` flag is passed and the human's wallet is linked to their Simmer account. Trading is bounded by default:

- **Dry-run is the default.** `python milaircraft_tracker.py` shows cluster state and opportunities but executes no trades.
- **`$SIM` paper sandbox option.** Set `TRADING_VENUE=sim` to trade Simmer's virtual currency at real prices.
- **Real-money trading requires explicit human verification.** A wallet must be linked at `simmer.markets/dashboard` before any real trade lands.
- **Per-trade cap.** `SIMMER_MILACFT_TRADE_SIZE` defaults to `$5.00` per trade.
- **Region exposure cap.** `SIMMER_MILACFT_CLUSTER_CAP` defaults to `$25.00` open exposure per region.
- **Daily kill switches.** `SIMMER_MILACFT_DAILY_LOSS_KILL` and `SIMMER_MILACFT_DAILY_TRADE_KILL` stop new entries after the configured limits.
- **Strategy-side safeguards.** The skill checks Simmer market context, slippage, resolved-market warnings, duplicate positions, and cluster state before orders. Disable only with `--no-safeguards`.
- **Reversibility.** Open positions exit when price rises above `SIMMER_MILACFT_EXIT_THRESHOLD`, or when the regional aircraft cluster drops below its threshold.

If anything above is unclear, stop before passing `--live`.

## Strategy Logic

The strategy watches military-tagged ADS-B aircraft via pref.trade's `aviation.get_adsb_military` MCP tool. Each scan fetches visible military aircraft once, filters them client-side into five target regions, then searches Polymarket for strike/action markets matching the fired region's keywords.

When a region has at least its configured aircraft threshold and a matching active market is priced below the entry threshold, the skill may buy YES. Position sizing is handled by `simmer_sdk.sizing.size_position()` using a conservative active-cluster prior and the SDK's minimum expected-value gate.

Test with `TRADING_VENUE=sim` before running real-money mode.

## When to Use This Skill

Use this skill when the user wants to:

- Track military aircraft cluster signals
- Trade strike or military-action Polymarket markets
- Run a pref.trade-powered Simmer skill
- Check current cluster state and skill activity

## Setup

Required environment:

- `SIMMER_API_KEY` - get from `simmer.markets/dashboard`
- `PREF_API_KEY`, `PREFERENCE_API_KEY`, or `~/.config/preference/credentials.json` - a pref.trade `pref_agent_*` key for the aircraft data source

### Autonomous pref.trade key setup

Agents do not need to use the human browser sign-up flow. If no pref key exists, register an agent key directly and save it to the standard credentials file:

```bash
mkdir -p ~/.config/preference
python - <<'PY'
import json
import os
import stat
import urllib.request
from pathlib import Path

cred = Path.home() / ".config" / "preference" / "credentials.json"
if cred.exists():
    raise SystemExit(f"credentials already exist: {cred}")

payload = json.dumps({
    "name": "Military Aircraft Tracker Agent",
    "description": "Agent using pref.trade aviation data through the Simmer mil-aircraft tracker skill",
}).encode()
req = urllib.request.Request(
    "https://pref.trade/v1/agents/register",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode())

# The api_key is shown once. Save it without printing it.
cred.write_text(json.dumps({"api_key": data["api_key"]}, indent=2))
os.chmod(cred, stat.S_IRUSR | stat.S_IWUSR)
print(f"saved pref credentials to {cred}")
print("claim_url available:", bool(data.get("claim_url")))
PY
```

Then verify the key and quota before any strategy run:

```bash
python milaircraft_tracker.py --check
```

The skill reads pref credentials in this order:

1. `PREF_API_KEY`
2. `PREFERENCE_API_KEY`
3. `~/.config/preference/credentials.json` with shape `{"api_key":"pref_agent_*"}`

Keep the key out of terminal logs, issues, PRs, and prompts. If `--check` reports an anonymous identity or missing key, stop and fix the credential path before scanning.

Then install the SDK:

```bash
pip install --upgrade simmer-sdk
```

## Configuration

| Setting | Environment Variable | Default | Description |
|---------|----------------------|---------|-------------|
| Trading venue | `TRADING_VENUE` | polymarket | Set `sim` for paper trading |
| Entry threshold | `SIMMER_MILACFT_ENTRY_THRESHOLD` | 0.15 | Buy YES when price is below this and cluster fires |
| Exit threshold | `SIMMER_MILACFT_EXIT_THRESHOLD` | 0.45 | Sell open YES position when price is above this |
| Trade size | `SIMMER_MILACFT_TRADE_SIZE` | 5.00 | Max USD per trade |
| Region cap | `SIMMER_MILACFT_CLUSTER_CAP` | 25.00 | Max open USD per region |
| Max trades/run | `SIMMER_MILACFT_MAX_TRADES_PER_RUN` | 3 | Maximum entries per scan |
| Cadence | `SIMMER_MILACFT_CADENCE_MIN` | 15 | Intended polling cadence in minutes |
| Daily loss kill | `SIMMER_MILACFT_DAILY_LOSS_KILL` | 25.00 | Stop new entries after daily loss reaches this |
| Daily trade kill | `SIMMER_MILACFT_DAILY_TRADE_KILL` | 10 | Stop new entries after this many daily trades |
| Slippage max | `SIMMER_MILACFT_SLIPPAGE_MAX` | 0.15 | Skip trades with estimated slippage above this |
| Order type | `SIMMER_MILACFT_ORDER_TYPE` | GTC | Simmer order type |

Position sizing also supports the standard Simmer SDK sizing env vars from `simmer_sdk.sizing`, including `SIMMER_POSITION_SIZING`, `SIMMER_KELLY_MULTIPLIER`, and `SIMMER_MIN_EV`.

## Quick Commands

```bash
# Dry run, no trades
python milaircraft_tracker.py

# Execute live trades
python milaircraft_tracker.py --live

# Use $SIM paper venue
TRADING_VENUE=sim python milaircraft_tracker.py --live

# Validate pref key and quota
python milaircraft_tracker.py --check

# Show current cluster dashboard
python milaircraft_tracker.py --status

# Show current Simmer positions
python milaircraft_tracker.py --positions

# View config
python milaircraft_tracker.py --config
```

## How It Works

Each cycle the script:

1. Loads region bounds from `regions.yaml`
2. Calls pref.trade `aviation.get_adsb_military`
3. Filters aircraft into target regions by bounding box
4. Searches Polymarket for strike/action markets using region keywords
5. Checks price, source context, slippage, daily kill switches, and exposure caps
6. Sizes the position through Simmer SDK
7. Buys YES in eligible markets
8. Sells open positions when the price exits or the cluster goes stale
9. Tags all trades with `sdk:milaircraft`

## Source Tagging

All trades are tagged with `source: "sdk:milaircraft"` and `skill_slug: "polymarket-mil-aircraft-tracker"`. This keeps P&L attribution separate from other skills and lets Simmer enforce cross-skill conflict checks.

## Troubleshooting

**`PREF_API_KEY not set`** - create a pref.trade agent key with the autonomous setup command above, export it as `PREF_API_KEY`/`PREFERENCE_API_KEY`, or save it to `~/.config/preference/credentials.json`. Run `python milaircraft_tracker.py --check` before scanning.

**`SIMMER_API_KEY environment variable not set`** - get a key from `simmer.markets/dashboard`.

**No clusters fired** - military aircraft are visible globally, but may not be inside one of the five configured regions.

**No markets found** - Polymarket may not have active strike/action markets matching the region keywords.

**Safeguard blocked** - the Simmer context endpoint flagged a resolved market, high slippage, or flip-flop risk.

**Daily kill switch active** - reset happens automatically on the next UTC day, or inspect `~/.simmer/milaircraft-tracker/state.json`.

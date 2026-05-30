# OpenClaw Skills

Official trading skills for OpenClaw, powered by the Simmer SDK.

## Available Skills

| Skill | Description | Default |
|-------|-------------|---------|
| [polymarket-weather-trader](./polymarket-weather-trader/) | Trade Polymarket weather markets using NOAA forecasts | Dry run, cron off |
| [polymarket-copytrading](./polymarket-copytrading/) | Mirror positions from top Polymarket traders | Dry run, cron off |
| [polymarket-signal-sniper](./polymarket-signal-sniper/) | Trade on breaking news from RSS feeds | Dry run, cron off |
| [polymarket-dca-eval-trader](./polymarket-dca-eval-trader/) | Build a three-tranche DCA plan with eval-envelope sizing checks | Dry run, cron off |

All skills run in **dry-run mode by default** (no trades). Pass `--live` to enable real trading. Cron scheduling is disabled by default — enable it after verifying the skill works as expected.

## Installation

```bash
clawhub install simmer-weather
clawhub install simmer-copytrading
clawhub install simmer-signalsniper
clawhub install polymarket-dca-eval-trader
```

## Requirements

- `SIMMER_API_KEY` from simmer.markets/dashboard
- Funded Polymarket account via Simmer

## SDK Reference

For programmatic access, see the [Python SDK](../simmer_sdk) documentation in the main [README](../README.md).

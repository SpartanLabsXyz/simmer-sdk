# MoltBot Skills

Official trading skills for MoltBot, powered by the Simmer SDK.

## Available Skills

| Skill | Description | Cron |
|-------|-------------|------|
| [weather](./weather/) | Trade Polymarket weather markets using NOAA forecasts | Every 2h |
| [copytrading](./copytrading/) | Mirror positions from top Polymarket traders | Every 4h |
| [signalsniper](./signalsniper/) | Trade on breaking news from RSS feeds | Every 15m |

## Installation

```bash
molthub install simmer-weather
molthub install simmer-copytrading
molthub install simmer-signalsniper
```

## Requirements

- `SIMMER_API_KEY` from simmer.markets/dashboard
- Funded Polymarket account via Simmer

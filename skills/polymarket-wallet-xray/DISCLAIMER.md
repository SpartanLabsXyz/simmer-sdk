# Disclaimer

This skill provides read-only public Polymarket wallet analysis and optional read-only status for your own Simmer account. It does not connect to a signing wallet, execute trades, change account settings, or run scheduled monitors.

## No financial advice

Reports describe historical activity using heuristic metrics. Rating tiers and the `recommendation` field summarize historical activity for research only and are not advice to copy wallets. Past performance does not guarantee future results.

## Data and metric limitations

Public activity can be incomplete or limited by pagination and the requested trade limit. Historical entry, profitability, bot, and arbitrage assessments are estimates. They do not establish a validated strategy or future returns. Check data quality, fees, and sample size before drawing conclusions.

## Optional account status

Public analysis needs no credentials or SDK. Only `scripts/status.py` uses `SIMMER_API_KEY`, and only for read-only requests to `https://api.simmer.markets`. Configure it outside chat only when requesting your own account status. Never paste a key into chat. If it is unset, the helper exits without a network request.

## Responsibility

You are responsible for how you interpret these reports. The authors are not liable for losses arising from their use. See `SKILL.md` for supported commands and data sources.

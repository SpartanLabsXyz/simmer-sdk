# Disclaimer - Read Before Going Live

This is a trading framework, not a guaranteed edge. A parlay multiplies risk by
design: one lost leg ends the entire streak, including accumulated winnings from
earlier legs. Long streaks are unlikely by construction; five 60%-favorites
chained together win less than 8% of the time.

- Maximum loss = your initial stake, plus fees and slippage. The skill never
  adds funds beyond the configured stake and uses no leverage.
- Draws lose team-win legs. Soccer has draws. "Mexico WIN" resolves NO on a
  draw. The config restates each leg's exact resolution condition; read them.
- Selling the winner early costs roughly 2-3%. The default roll sells at a 0.97+
  bid instead of waiting for redemption, trading a small haircut for faster
  compounding.
- Roll timing can stop the streak. If proceeds have not landed 15 minutes before
  the next kickoff, the skill banks cash and stops rather than chasing.
- Postponed or abandoned matches pause the skill. No automatic action; review
  and decide manually.
- Dry-run is the default. Pass `--live` only after a dry-run streak looked sane.
- Edges depend on spreads, fees, liquidity, and your thesis being right. Past
  performance of any strategy is not indicative of future results. Trade only
  what you can afford to lose.

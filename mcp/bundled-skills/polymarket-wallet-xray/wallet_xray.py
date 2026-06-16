#!/usr/bin/env python3
"""
Polymarket Wallet Xray

Forensic analysis of any Polymarket wallet's trading patterns, skill level,
and edge detection. Inspired by @thejayden's "Autopsy of a Polymarket Whale".

Queries Polymarket's public data API (data-api.polymarket.com) — no authentication needed.
Analyzes ANY Polymarket wallet, not just Simmer users.

Usage:
    python wallet_xray.py 0x1234...abcd
    python wallet_xray.py 0x1234...abcd "Bitcoin"
    python wallet_xray.py 0x1111... 0x2222... --compare
    python wallet_xray.py 0x1234...abcd --json
    python wallet_xray.py 0x1234...abcd --limit 100
"""

import os
import sys
import json
import argparse
import statistics
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from typing import Dict, List, Optional, Any
from urllib.parse import quote

# Force line-buffered stdout so output is visible in non-TTY environments
sys.stdout.reconfigure(line_buffering=True)

# Polymarket public APIs
DATA_API_BASE = "https://data-api.polymarket.com"

def api_request(url: str, headers: Optional[Dict] = None, timeout: int = 30) -> dict:
    """Make HTTP request to public API."""
    req = Request(url, headers=headers or {
        "Content-Type": "application/json",
        "User-Agent": "SimmerSDK/1.0",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"❌ API Error {e.code}: {error_body}", file=sys.stderr)
        return {}
    except URLError as e:
        print(f"❌ Connection error: {e.reason}", file=sys.stderr)
        return {}

def get_wallet_trades(wallet: str, limit: Optional[int] = None) -> List[Dict]:
    """Fetch trades for a wallet from Polymarket data API (public, no auth)."""
    max_trades = limit or 1000
    url = f"{DATA_API_BASE}/activity?user={quote(wallet.lower(), safe='')}&limit={max_trades}"

    result = api_request(url)

    raw = result if isinstance(result, list) else []

    # Normalize activity fields to the format the rest of the script expects
    trades = []
    for r in raw:
        activity_type = r.get("type", "TRADE")  # TRADE, REDEEM, MERGE
        ts = r.get("timestamp", 0)
        created_at = datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else ""
        outcome = r.get("outcome", "").lower()  # "yes", "no", "up", "down", etc.
        side_raw = r.get("side", "").upper()  # "BUY" or "SELL"

        # REDEEMs are position closures (winning outcome resolved, payout = $1/share)
        if activity_type == "REDEEM":
            side_raw = "SELL"

        action = f"{side_raw.lower()}_{outcome}" if outcome else side_raw.lower()
        default_price = 1.0 if activity_type == "REDEEM" else 0.5

        trades.append({
            "created_at": created_at,
            "market_id": r.get("conditionId", ""),
            "side": outcome if outcome else "unknown",
            "action": action,
            "shares": r.get("size", 0),
            "price": r.get("price", default_price),
            "cost_usdc": r.get("usdcSize", r.get("size", 0) * r.get("price", 0.5)),
            "title": r.get("title", ""),
            "transaction_hash": r.get("transactionHash", ""),
            "activity_type": activity_type,
        })

    if not trades:
        print(f"⚠️  No trade history found for wallet {wallet}", file=sys.stderr)
        print(f"   Note: Some wallets may have limited public trade history.", file=sys.stderr)

    return trades

def compute_profitability(trades: List[Dict]) -> Dict[str, Any]:
    """Compute profitability metrics from trade history."""
    if not trades:
        return {
            "time_profitable_pct": 0,
            "win_rate_pct": 0,
            "avg_profit_per_win": 0,
            "avg_loss_per_loss": 0,
            "realized_pnl_usd": 0,
            "total_trades": 0
        }

    # Track positions per (market, outcome) — works for any outcome label
    # Key: (conditionId, outcome) -> {shares, cost}
    positions = {}
    trade_outcomes = []  # List of realized P&Ls from closed positions
    skipped_sells = 0

    for trade in sorted(trades, key=lambda t: t.get("created_at", "")):
        market_id = trade.get("market_id", "")
        outcome = trade.get("side", "").lower()  # outcome label: yes/no/up/down
        action = trade.get("action", "").lower()
        shares = float(trade.get("shares", 0))
        price = float(trade.get("price", 0.5))
        cost = float(trade.get("cost_usdc", shares * price))
        is_buy = action.startswith("buy")
        is_sell = action.startswith("sell")

        pos_key = (market_id, outcome)
        if pos_key not in positions:
            positions[pos_key] = {"shares": 0, "cost": 0}

        pos = positions[pos_key]

        if is_buy:
            pos["shares"] += shares
            pos["cost"] += cost
        elif is_sell and pos["shares"] > 0:
            avg_entry = pos["cost"] / pos["shares"]
            sold = min(shares, pos["shares"])
            pnl = (price - avg_entry) * sold
            trade_outcomes.append(pnl)
            pos["shares"] = max(0, pos["shares"] - shares)
            pos["cost"] = max(0, pos["cost"] - (avg_entry * sold))
        elif is_sell:
            skipped_sells += 1

    if skipped_sells > 0:
        print(f"   ⚠️  {skipped_sells} sells/redeems had no matching buy (outside fetch window)", file=sys.stderr)

    # Calculate metrics
    profitable_trades = [p for p in trade_outcomes if p > 0.001]
    losing_trades = [p for p in trade_outcomes if p < -0.001]
    win_rate = len(profitable_trades) / len(trade_outcomes) * 100 if trade_outcomes else 0

    avg_profit = statistics.mean(profitable_trades) if profitable_trades else 0
    avg_loss = statistics.mean(losing_trades) if losing_trades else 0
    total_realized_pnl = sum(trade_outcomes)

    # Compute time profitable: track running P&L across all trades,
    # measure what fraction of the trade timeline the cumulative P&L was > 0
    running_pnl = 0.0
    profitable_count = 0
    for outcome in trade_outcomes:
        running_pnl += outcome
        if running_pnl > 0:
            profitable_count += 1
    time_profitable_pct = (profitable_count / len(trade_outcomes) * 100) if trade_outcomes else 0

    return {
        "time_profitable_pct": round(time_profitable_pct, 1),
        "win_rate_pct": round(win_rate, 1),
        "avg_profit_per_win": round(avg_profit, 4),
        "avg_loss_per_loss": round(avg_loss, 4),
        "realized_pnl_usd": round(total_realized_pnl, 2),
        "total_trades": len(trades)
    }

def compute_entry_quality(trades: List[Dict]) -> Dict[str, Any]:
    """Analyze entry quality by measuring price consistency per market.

    Without spot-price data, we measure how consistent the trader's buy
    prices are within each market.  Low variance = patient limit orders,
    high variance = chasing / market orders at shifting prices.
    """
    if not trades:
        return {
            "avg_price_spread_bps": 0,
            "quality_rating": "N/A",
            "assessment": "No trades to analyze"
        }

    # Group buy prices by (market, outcome)
    buy_prices: Dict[tuple, List[float]] = {}
    for trade in trades:
        action = trade.get("action", "").lower()
        if action.startswith("buy"):
            price = float(trade.get("price", 0.5))
            key = (trade.get("market_id", ""), trade.get("side", ""))
            buy_prices.setdefault(key, []).append(price)

    # Compute spread (max-min) in bps for each market with 2+ buys
    spreads = []
    for prices in buy_prices.values():
        if len(prices) >= 2:
            avg_p = statistics.mean(prices)
            if avg_p > 0.01:
                spread_bps = (max(prices) - min(prices)) / avg_p * 10000
                spreads.append(spread_bps)

    total_buys = sum(len(v) for v in buy_prices.values())
    if not spreads:
        # Only single buys per market — can't measure consistency
        return {
            "avg_price_spread_bps": 0,
            "quality_rating": "B" if total_buys > 0 else "N/A",
            "assessment": f"Single entries across {len(buy_prices)} markets. No re-entries to measure consistency." if total_buys else "No buy trades to analyze"
        }

    avg_spread = statistics.mean(spreads)

    if avg_spread < 50:
        rating = "A"
        assessment = "Tight entries. Patient limit orders."
    elif avg_spread < 150:
        rating = "B+"
        assessment = "Good consistency. Balanced speed/price."
    elif avg_spread < 400:
        rating = "B"
        assessment = "Moderate spread. Some price chasing."
    else:
        rating = "C"
        assessment = "Wide spread across entries. Chasing prices."

    return {
        "avg_price_spread_bps": round(avg_spread, 1),
        "quality_rating": rating,
        "assessment": assessment
    }

def detect_bot_behavior(trades: List[Dict]) -> Dict[str, Any]:
    """Detect if wallet is likely a bot based on trading speed."""
    if len(trades) < 2:
        return {
            "is_bot_detected": False,
            "trading_intensity": "very_low",
            "avg_seconds_between_trades": 0,
            "price_chasing": "unknown",
            "accumulation_signal": "unknown"
        }

    # Calculate time between trades
    sorted_trades = sorted(trades, key=lambda t: t.get("created_at", ""))
    time_diffs = []
    for i in range(1, len(sorted_trades)):
        try:
            t1 = datetime.fromisoformat(sorted_trades[i-1].get("created_at", "").replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(sorted_trades[i].get("created_at", "").replace("Z", "+00:00"))
            diff_seconds = (t2 - t1).total_seconds()
            if diff_seconds > 0:
                time_diffs.append(diff_seconds)
        except (ValueError, TypeError, OSError):
            continue

    if not time_diffs:
        avg_seconds = 0
        is_bot = False
        intensity = "unknown"
    else:
        avg_seconds = statistics.mean(time_diffs)
        is_bot = avg_seconds < 5  # Bots trade < 5 seconds apart

    # Trading intensity
    if not time_diffs:
        pass  # already set above
    elif avg_seconds < 10:
        intensity = "very_high"
    elif avg_seconds < 60:
        intensity = "high"
    elif avg_seconds < 300:
        intensity = "medium"
    else:
        intensity = "low"

    # Detect price chasing (buying at increasingly higher prices)
    yes_prices = []
    for trade in sorted_trades:
        if trade.get("action", "").lower().startswith("buy"):
            yes_prices.append(float(trade.get("price", 0.5)))

    price_chasing = "unknown"
    if len(yes_prices) > 1:
        avg_early = statistics.mean(yes_prices[:len(yes_prices)//2])
        avg_late = statistics.mean(yes_prices[len(yes_prices)//2:])
        if avg_late > avg_early * 1.05:
            price_chasing = "high"
        elif avg_late > avg_early:
            price_chasing = "moderate"
        else:
            price_chasing = "low"

    # Accumulation signal (position sizing increasing over time)
    sizes = [float(t.get("shares", 0)) for t in sorted_trades]
    accumulation = "unknown"
    if len(sizes) > 1:
        avg_early = statistics.mean(sizes[:len(sizes)//2])
        avg_late = statistics.mean(sizes[len(sizes)//2:])
        if avg_late > avg_early * 1.3:
            accumulation = "growing"
        elif avg_late > avg_early:
            accumulation = "stable"
        else:
            accumulation = "decreasing"

    return {
        "is_bot_detected": is_bot,
        "trading_intensity": intensity,
        "avg_seconds_between_trades": round(avg_seconds, 1),
        "price_chasing": price_chasing,
        "accumulation_signal": accumulation
    }

def detect_arbitrage_edge(trades: List[Dict]) -> Dict[str, Any]:
    """Detect if wallet has locked-in arbitrage edge (combined avg < 1.0)."""
    if not trades:
        return {
            "hedge_check_combined_avg": 1.0,
            "has_arbitrage_edge": False,
            "assessment": "No trades to analyze"
        }

    # Group buys by (market, outcome)
    # For arb detection, check if user bought both sides of the same market
    outcome_costs: Dict[str, Dict[str, dict]] = {}  # market -> {outcome -> {cost, shares}}
    for trade in trades:
        action = trade.get("action", "").lower()
        if not action.startswith("buy"):
            continue
        market_id = trade.get("market_id", "")
        outcome = trade.get("side", "").lower()
        price = float(trade.get("price", 0.5))
        shares = float(trade.get("shares", 0))

        if market_id not in outcome_costs:
            outcome_costs[market_id] = {}
        if outcome not in outcome_costs[market_id]:
            outcome_costs[market_id][outcome] = {"cost": 0, "shares": 0}
        outcome_costs[market_id][outcome]["cost"] += price * shares
        outcome_costs[market_id][outcome]["shares"] += shares

    # For markets with exactly 2 outcomes traded, compute combined avg price
    combined_avgs = []
    for market_id, outcomes in outcome_costs.items():
        if len(outcomes) == 2:
            avgs = []
            for data in outcomes.values():
                if data["shares"] > 0:
                    avgs.append(data["cost"] / data["shares"])
            if len(avgs) == 2:
                combined_avgs.append(sum(avgs))

    if not combined_avgs:
        return {
            "hedge_check_combined_avg": 1.0,
            "has_arbitrage_edge": False,
            "assessment": "No hedged positions (one-sided bets)"
        }

    avg_combined = statistics.mean(combined_avgs)
    has_edge = avg_combined < 0.99

    if has_edge:
        assessment = f"Found arbitrage edge! Combined avg ${avg_combined:.2f} < $1.00"
    else:
        assessment = f"No arbitrage edge. Combined avg ${avg_combined:.2f}"

    return {
        "hedge_check_combined_avg": round(avg_combined, 4),
        "has_arbitrage_edge": has_edge,
        "assessment": assessment
    }

def compute_risk_profile(trades: List[Dict]) -> Dict[str, Any]:
    """Analyze risk profile based on drawdowns and concentration."""
    if not trades:
        return {
            "max_drawdown_pct": 0,
            "volatility": "unknown",
            "max_position_concentration": 0
        }

    # Estimate drawdown from mark-to-market portfolio value over time.
    # Track positions and value them at the latest trade price per outcome.
    positions: Dict[tuple, dict] = {}  # (market, outcome) -> {shares, cost}
    last_price: Dict[tuple, float] = {}
    portfolio_values = []

    for trade in sorted(trades, key=lambda t: t.get("created_at", "")):
        cost = float(trade.get("cost_usdc", 0))
        price = float(trade.get("price", 0.5))
        shares = float(trade.get("shares", 0))
        action = trade.get("action", "").lower()
        key = (trade.get("market_id", ""), trade.get("side", ""))
        last_price[key] = price

        if key not in positions:
            positions[key] = {"shares": 0, "cost": 0}
        pos = positions[key]

        if action.startswith("buy"):
            pos["shares"] += shares
            pos["cost"] += cost
        elif action.startswith("sell"):
            sold = min(shares, pos["shares"])
            if pos["shares"] > 0:
                avg = pos["cost"] / pos["shares"]
                pos["shares"] -= sold
                pos["cost"] = max(0, pos["cost"] - avg * sold)

        # Mark-to-market: sum(position_shares * last_known_price) - total_cost
        mtm = sum(p["shares"] * last_price.get(k, 0.5) for k, p in positions.items())
        total_cost = sum(p["cost"] for p in positions.values())
        portfolio_values.append(mtm - total_cost)

    if len(portfolio_values) > 1:
        peak = portfolio_values[0]
        max_dd = 0.0
        for val in portfolio_values:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        # Express as % of total capital deployed
        total_invested = sum(float(t.get("cost_usdc", 0)) for t in trades if t.get("action", "").startswith("buy"))
        max_drawdown = min(max_dd / max(total_invested, 1) * 100, 100)
    else:
        max_drawdown = 0

    # Volatility from trade size variance
    costs = [float(t.get("cost_usdc", 0)) for t in trades if float(t.get("cost_usdc", 0)) > 0]
    if len(costs) > 1:
        volatility_std = statistics.stdev(costs)
        avg_cost = statistics.mean(costs)
        cv = volatility_std / avg_cost if avg_cost > 0 else 0  # coefficient of variation
        if cv < 0.5:
            volatility = "low"
        elif cv < 1.5:
            volatility = "medium"
        else:
            volatility = "high"
    else:
        volatility = "unknown"

    # Max position concentration
    sizes = [float(t.get("shares", 0)) for t in trades]
    max_size = max(sizes) if sizes else 0
    total_size = sum(sizes) if sizes else 1
    concentration = max_size / total_size if total_size > 0 else 0

    return {
        "max_drawdown_pct": round(max_drawdown, 1),
        "volatility": volatility,
        "max_position_concentration": round(concentration, 2)
    }

def generate_recommendation(data: Dict[str, Any]) -> str:
    """Generate a recommendation based on all metrics."""
    prof = data.get("profitability", {})
    entry = data.get("entry_quality", {})
    behavior = data.get("behavior", {})
    edge = data.get("edge_detection", {})
    risk = data.get("risk_profile", {})

    score = 0
    factors = []

    # Time profitable (max 30 points)
    time_prof = prof.get("time_profitable_pct", 0)
    if time_prof > 80:
        score += 30
        factors.append("✅ Strong Time Profitable (>80%)")
    elif time_prof > 60:
        score += 20
        factors.append("✅ Good Time Profitable (60-80%)")
    elif time_prof > 40:
        score += 10
        factors.append("⚠️ Moderate Time Profitable (40-60%)")
    else:
        factors.append("❌ Low Time Profitable (<40%)")

    # Entry quality (max 20 points)
    rating = entry.get("quality_rating", "")
    if rating in ["A", "A+"]:
        score += 20
        factors.append("✅ Excellent entry quality")
    elif rating in ["B+", "B"]:
        score += 12
        factors.append("✅ Good entry quality")
    elif rating == "C":
        score += 5
        factors.append("⚠️ Weak entry quality")
    else:
        factors.append("⚠️ Insufficient data for entry quality")

    # Bot detection (max 15 points)
    if not behavior.get("is_bot_detected", False):
        score += 15
        factors.append("✅ Human trader (not bot)")
    else:
        factors.append("❌ Bot detected")

    # Arbitrage edge (max 15 points)
    if edge.get("has_arbitrage_edge", False):
        score += 15
        factors.append("✅ Arbitrage edge found")
    else:
        factors.append("⚠️ No arbitrage edge (direction bet)")

    # Risk profile (max 10 points)
    drawdown = risk.get("max_drawdown_pct", 50)
    if drawdown < 15:
        score += 10
        factors.append("✅ Low risk (small drawdowns)")
    elif drawdown < 30:
        score += 7
        factors.append("✅ Moderate risk")
    else:
        factors.append("⚠️ High risk (large drawdowns)")

    # Trade history length (max 10 points)
    trades = prof.get("total_trades", 0)
    if trades > 100:
        score += 10
        factors.append("✅ Large sample size (100+ trades)")
    elif trades > 20:
        score += 5
        factors.append("⚠️ Moderate sample size")
    else:
        factors.append("⚠️ Small sample size (<20 trades)")

    # Generate text
    if score >= 90:
        rec = f"Excellent trader. {'; '.join(factors[:3])} Safe to copytrade with 25-50% of capital."
    elif score >= 75:
        rec = f"Good trader. {'; '.join(factors[:3])} Safe to copytrade with 10-25% of capital."
    elif score >= 60:
        rec = f"Decent trader. {'; '.join(factors[:3])} Cautious copytrading with 5-10% of capital."
    else:
        rec = f"Risky trader. {'; '.join(factors[:2])} Avoid copytrading or limit to <5% of capital."

    return rec

def analyze_wallet(wallet: str, limit: Optional[int] = None) -> Dict[str, Any]:
    """Main analysis function."""
    # Validate wallet address
    if not wallet.lower().startswith("0x") or len(wallet) != 42:
        print(f"❌ Invalid wallet address: {wallet}", file=sys.stderr)
        print(f"   Expected 42-char hex address (0x...)", file=sys.stderr)
        return {}

    # Fetch trades from Polymarket CLOB API
    trades = get_wallet_trades(wallet, limit)

    if not trades:
        print(f"❌ No trade history found for wallet {wallet}", file=sys.stderr)
        print(f"   (Some wallets may have limited public trade data)", file=sys.stderr)
        return {}

    print(f"📊 Analyzing {len(trades)} trades from Polymarket...", file=sys.stderr)

    # Compute trading period from first/last trade timestamps
    sorted_by_time = sorted(trades, key=lambda t: t.get("created_at", ""))
    try:
        t_first = datetime.fromisoformat(sorted_by_time[0]["created_at"].replace("Z", "+00:00"))
        t_last = datetime.fromisoformat(sorted_by_time[-1]["created_at"].replace("Z", "+00:00"))
        period_hours = round((t_last - t_first).total_seconds() / 3600, 1)
    except (ValueError, KeyError, IndexError):
        period_hours = 0

    # Compute all metrics
    data = {
        "wallet": wallet,
        "total_trades": len(trades),
        "total_period_hours": period_hours,
        "analysis_timestamp": datetime.utcnow().isoformat(),
        "profitability": compute_profitability(trades),
        "entry_quality": compute_entry_quality(trades),
        "behavior": detect_bot_behavior(trades),
        "edge_detection": detect_arbitrage_edge(trades),
        "risk_profile": compute_risk_profile(trades),
    }

    # Generate recommendation
    data["recommendation"] = generate_recommendation(data)

    return data

def format_output(data: Dict[str, Any]) -> str:
    """Format analysis results for console output."""
    if not data:
        return "No data to display"

    output = []
    wallet = data.get("wallet", "unknown")
    output.append(f"\n{'='*60}")
    output.append(f"🔍 WALLET XRAY: {wallet[:6]}...{wallet[-4:]}")
    output.append(f"{'='*60}\n")

    # Profitability
    prof = data.get("profitability", {})
    output.append("💰 PROFITABILITY")
    output.append(f"  Time Profitable:    {prof.get('time_profitable_pct', 0):.1f}%")
    output.append(f"  Win Rate:           {prof.get('win_rate_pct', 0):.1f}%")
    output.append(f"  Avg Profit/Win:     ${prof.get('avg_profit_per_win', 0):.4f}")
    output.append(f"  Avg Loss/Loss:      ${prof.get('avg_loss_per_loss', 0):.4f}")
    output.append(f"  Realized P&L:       ${prof.get('realized_pnl_usd', 0):.2f}")
    output.append(f"  Total Trades:       {prof.get('total_trades', 0)}\n")

    # Entry Quality
    entry = data.get("entry_quality", {})
    output.append("🎯 ENTRY QUALITY")
    output.append(f"  Price Spread:       {entry.get('avg_price_spread_bps', 0):.1f} bps")
    output.append(f"  Rating:             {entry.get('quality_rating', 'N/A')}")
    output.append(f"  Assessment:         {entry.get('assessment', 'N/A')}\n")

    # Behavior
    behavior = data.get("behavior", {})
    is_bot = "🤖 BOT" if behavior.get("is_bot_detected") else "👤 HUMAN"
    output.append(f"🤖 BEHAVIOR ({is_bot})")
    output.append(f"  Trading Intensity:  {behavior.get('trading_intensity', 'unknown')}")
    output.append(f"  Avg Time Between:   {behavior.get('avg_seconds_between_trades', 0):.1f}s")
    output.append(f"  Price Chasing:      {behavior.get('price_chasing', 'unknown')}")
    output.append(f"  Accumulation:       {behavior.get('accumulation_signal', 'unknown')}\n")

    # Edge Detection
    edge = data.get("edge_detection", {})
    has_edge = "✅ YES" if edge.get("has_arbitrage_edge") else "❌ NO"
    output.append(f"💎 EDGE DETECTION ({has_edge})")
    output.append(f"  Combined Avg:       ${edge.get('hedge_check_combined_avg', 1.0):.4f}")
    output.append(f"  Assessment:         {edge.get('assessment', 'N/A')}\n")

    # Risk Profile
    risk = data.get("risk_profile", {})
    output.append("⚠️  RISK PROFILE")
    output.append(f"  Max Drawdown:       {risk.get('max_drawdown_pct', 0):.1f}%")
    output.append(f"  Volatility:         {risk.get('volatility', 'unknown')}")
    output.append(f"  Max Concentration:  {risk.get('max_position_concentration', 0):.1%}\n")

    # Recommendation
    output.append("📋 RECOMMENDATION")
    output.append(f"  {data.get('recommendation', 'N/A')}\n")

    output.append(f"{'='*60}\n")

    return "\n".join(output)

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Wallet Xray - Forensic trading analysis",
        epilog="Example: python wallet_xray.py 0x1234...abcd"
    )
    parser.add_argument("wallet", nargs="?", help="Wallet address (0x...)")
    parser.add_argument("market", nargs="?", help="Second wallet address for --compare")
    parser.add_argument("--compare", action="store_true", help="Compare two wallets")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--limit", type=int, help="Limit trades analyzed")
    args = parser.parse_args()

    if not args.wallet:
        parser.print_help()
        sys.exit(0)

    print(f"🔍 Polymarket Wallet Xray", file=sys.stderr)
    print(f"Inspired by @thejayden's trading analysis framework\n", file=sys.stderr)

    # Analyze wallet(s)
    if args.compare and args.market:
        # Compare two wallets
        wallet1 = args.wallet
        wallet2 = args.market
        data1 = analyze_wallet(wallet1, limit=args.limit)
        data2 = analyze_wallet(wallet2, limit=args.limit)

        if args.json:
            print(json.dumps({"wallet1": data1, "wallet2": data2}, indent=2))
        else:
            if data1:
                print(format_output(data1))
            if data2:
                print(format_output(data2))
            if data1 and data2:
                print("\n🔄 COMPARISON")
                print(f"  Wallet 1 Time Profitable:  {data1.get('profitability', {}).get('time_profitable_pct', 0):.1f}%")
                print(f"  Wallet 2 Time Profitable:  {data2.get('profitability', {}).get('time_profitable_pct', 0):.1f}%")
    else:
        # Analyze single wallet
        data = analyze_wallet(args.wallet, args.limit)

        if args.json:
            print(json.dumps(data, indent=2))
        else:
            if data:
                print(format_output(data))

if __name__ == "__main__":
    main()

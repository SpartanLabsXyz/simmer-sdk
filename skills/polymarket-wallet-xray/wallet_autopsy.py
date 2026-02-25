#!/usr/bin/env python3
"""
Polymarket Wallet Autopsy

Forensic analysis of any Polymarket wallet's trading patterns, skill level,
and edge detection. Inspired by @thejayden's "Autopsy of a Polymarket Whale".

Queries Polymarket's public CLOB API directly — no authentication needed.
Analyzes ANY Polymarket wallet, not just Simmer users.

Usage:
    python wallet_autopsy.py 0x1234...abcd
    python wallet_autopsy.py 0x1234...abcd "Bitcoin"
    python wallet_autopsy.py 0x1111... 0x2222... --compare
    python wallet_autopsy.py 0x1234...abcd --json
    python wallet_autopsy.py 0x1234...abcd --limit 100
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

# Force line-buffered stdout so output is visible in non-TTY environments
sys.stdout.reconfigure(line_buffering=True)

# Polymarket public APIs
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

def api_request(url: str, headers: Optional[Dict] = None, timeout: int = 30) -> dict:
    """Make HTTP request to public API."""
    req = Request(url, headers=headers or {"Content-Type": "application/json"})
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

def search_markets(query: str, limit: int = 10) -> List[Dict]:
    """Search for markets by query using Gamma API."""
    url = f"{GAMMA_API_BASE}/markets"
    params = f"?query={query}&limit={limit}&orderBy=volume24h"

    result = api_request(url + params)
    return result.get("markets", []) if isinstance(result, dict) else []

def get_orderbook(token_id: str) -> Dict:
    """Get current orderbook for a token."""
    url = f"{CLOB_API_BASE}/book?token_id={token_id}"
    return api_request(url)

def get_wallet_trades(wallet: str, limit: Optional[int] = None) -> List[Dict]:
    """Fetch trades for a wallet from Polymarket CLOB API."""
    # Use the orderbook history endpoint to get wallet trades
    # This queries Polymarket's public trade data
    url = f"{CLOB_API_BASE}/trades?wallet={wallet.lower()}&limit={limit or 1000}"

    result = api_request(url)

    # Try different possible response formats
    if isinstance(result, dict):
        trades = result.get("trades", result.get("data", []))
    else:
        trades = result if isinstance(result, list) else []

    if not trades:
        print(f"⚠️  No trade history found for wallet {wallet}", file=sys.stderr)
        print(f"   Note: Some wallets may have limited public trade history.", file=sys.stderr)

    return trades if isinstance(trades, list) else []

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

    # Track positions over time
    positions = {}  # market_id -> {shares_yes, shares_no, cost_basis_yes, cost_basis_no}
    trade_outcomes = []  # List of realized P&Ls from closed positions

    for trade in sorted(trades, key=lambda t: t.get("created_at", "")):
        market_id = trade.get("market_id", "")
        side = trade.get("side", "").lower()
        action = trade.get("action", "").lower()
        shares = float(trade.get("shares", 0))
        price = float(trade.get("price", 0.5))
        cost = float(trade.get("cost_usdc", shares * price))

        if market_id not in positions:
            positions[market_id] = {
                "shares_yes": 0,
                "shares_no": 0,
                "cost_yes": 0,
                "cost_no": 0
            }

        pos = positions[market_id]

        # Update position
        if action == "buy" or action == "buy_yes":
            if side == "yes":
                pos["shares_yes"] += shares
                pos["cost_yes"] += cost
            else:  # side == "no"
                pos["shares_no"] += shares
                pos["cost_no"] += cost
        elif action == "sell" or action == "sell_yes":
            if side == "yes":
                if pos["shares_yes"] > 0:
                    # Realize P&L
                    avg_entry = pos["cost_yes"] / pos["shares_yes"] if pos["shares_yes"] > 0 else 0
                    pnl = (price - avg_entry) * min(shares, pos["shares_yes"])
                    trade_outcomes.append(pnl)
                    pos["shares_yes"] = max(0, pos["shares_yes"] - shares)
                    pos["cost_yes"] = max(0, pos["cost_yes"] - (avg_entry * shares))
            else:  # side == "no"
                if pos["shares_no"] > 0:
                    avg_entry = pos["cost_no"] / pos["shares_no"] if pos["shares_no"] > 0 else 0
                    pnl = ((1 - price) - (1 - avg_entry)) * min(shares, pos["shares_no"])
                    trade_outcomes.append(pnl)
                    pos["shares_no"] = max(0, pos["shares_no"] - shares)
                    pos["cost_no"] = max(0, pos["cost_no"] - (avg_entry * shares))

    # Calculate metrics
    profitable_trades = [p for p in trade_outcomes if p > 0.001]
    losing_trades = [p for p in trade_outcomes if p < -0.001]
    win_rate = len(profitable_trades) / len(trade_outcomes) * 100 if trade_outcomes else 0

    avg_profit = statistics.mean(profitable_trades) if profitable_trades else 0
    avg_loss = statistics.mean(losing_trades) if losing_trades else 0
    total_realized_pnl = sum(trade_outcomes)

    # Estimate time profitable (simplified: based on trade count profitability)
    time_profitable_pct = win_rate  # Rough estimate

    return {
        "time_profitable_pct": round(time_profitable_pct, 1),
        "win_rate_pct": round(win_rate, 1),
        "avg_profit_per_win": round(avg_profit, 4),
        "avg_loss_per_loss": round(avg_loss, 4),
        "realized_pnl_usd": round(total_realized_pnl, 2),
        "total_trades": len(trades)
    }

def compute_entry_quality(trades: List[Dict]) -> Dict[str, Any]:
    """Analyze entry quality by measuring slippage."""
    if not trades:
        return {
            "avg_slippage_bps": 0,
            "quality_rating": "N/A",
            "assessment": "No trades to analyze"
        }

    slippages = []
    for trade in trades:
        action = trade.get("action", "").lower()
        if action in ["buy", "buy_yes", "buy_no"]:
            spot_price = float(trade.get("spot_price_at_trade", 0.5))
            execution_price = float(trade.get("price", 0.5))
            slippage_bps = abs(execution_price - spot_price) / max(spot_price, 0.01) * 10000
            slippages.append(slippage_bps)

    if not slippages:
        return {
            "avg_slippage_bps": 0,
            "quality_rating": "N/A",
            "assessment": "No buy trades to analyze"
        }

    avg_slippage = statistics.mean(slippages)

    # Rating based on slippage
    if avg_slippage < 20:
        rating = "A"
        assessment = "Expert. Limit orders, excellent patience."
    elif avg_slippage < 40:
        rating = "B+"
        assessment = "Good entries, balanced speed/price."
    elif avg_slippage < 60:
        rating = "B"
        assessment = "Decent, but some FOMO buying."
    else:
        rating = "C"
        assessment = "Weak entries. Chasing prices."

    return {
        "avg_slippage_bps": round(avg_slippage, 1),
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
        except:
            continue

    if not time_diffs:
        avg_seconds = 0
        is_bot = False
    else:
        avg_seconds = statistics.mean(time_diffs)
        is_bot = avg_seconds < 5  # Bots trade < 5 seconds apart

    # Trading intensity
    if avg_seconds < 10:
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
        if trade.get("action", "").lower() in ["buy", "buy_yes"]:
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

    # Group by market and side
    positions = {}
    for trade in trades:
        market_id = trade.get("market_id", "")
        side = trade.get("side", "").lower()
        price = float(trade.get("price", 0.5))
        shares = float(trade.get("shares", 0))

        if market_id not in positions:
            positions[market_id] = {"yes_cost": 0, "yes_shares": 0, "no_cost": 0, "no_shares": 0}

        if side == "yes":
            positions[market_id]["yes_cost"] += price * shares
            positions[market_id]["yes_shares"] += shares
        else:
            positions[market_id]["no_cost"] += price * shares
            positions[market_id]["no_shares"] += shares

    # Calculate combined averages
    combined_avgs = []
    for market_id, pos in positions.items():
        if pos["yes_shares"] > 0 and pos["no_shares"] > 0:
            yes_avg = pos["yes_cost"] / pos["yes_shares"]
            no_avg = pos["no_cost"] / pos["no_shares"]
            combined = yes_avg + no_avg
            combined_avgs.append(combined)

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

    # Calculate max drawdown (simplified)
    balances = []
    running_balance = 10000  # Assume starting balance
    for trade in sorted(trades, key=lambda t: t.get("created_at", "")):
        pnl = float(trade.get("pnl", 0)) if "pnl" in trade else 0
        running_balance += pnl
        balances.append(running_balance)

    if balances:
        max_balance = max(balances)
        min_balance = min(balances)
        max_drawdown = (max_balance - min_balance) / max_balance * 100 if max_balance > 0 else 0
    else:
        max_drawdown = 0

    # Volatility (std dev of balance changes)
    if len(balances) > 1:
        changes = [balances[i+1] - balances[i] for i in range(len(balances)-1)]
        if changes:
            volatility_std = statistics.stdev(changes) if len(changes) > 1 else 0
            if volatility_std < 50:
                volatility = "low"
            elif volatility_std < 200:
                volatility = "medium"
            else:
                volatility = "high"
        else:
            volatility = "unknown"
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

def analyze_wallet(wallet: str, market_query: Optional[str] = None,
                   limit: Optional[int] = None) -> Dict[str, Any]:
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

    # Compute all metrics
    data = {
        "wallet": wallet,
        "total_trades": len(trades),
        "total_period_hours": 0,  # Would require timestamp analysis
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
    output.append(f"🔍 WALLET AUTOPSY: {wallet[:16]}...")
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
    output.append(f"  Avg Slippage:       {entry.get('avg_slippage_bps', 0):.1f} bps")
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
        description="Polymarket Wallet Autopsy - Forensic trading analysis",
        epilog="Example: python wallet_autopsy.py 0x1234...abcd"
    )
    parser.add_argument("wallet", nargs="?", help="Wallet address (0x...)")
    parser.add_argument("market", nargs="?", help="Market query (optional)")
    parser.add_argument("--compare", action="store_true", help="Compare two wallets")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--limit", type=int, help="Limit trades analyzed")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no analysis)")
    args = parser.parse_args()

    if not args.wallet:
        parser.print_help()
        sys.exit(0)

    print(f"🔍 Polymarket Wallet Autopsy", file=sys.stderr)
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
        data = analyze_wallet(args.wallet, args.market, args.limit)

        if args.json:
            print(json.dumps(data, indent=2))
        else:
            if data:
                print(format_output(data))

if __name__ == "__main__":
    main()

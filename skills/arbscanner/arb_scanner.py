#!/usr/bin/env python3
"""
Simmer Arbitrage Scanner Skill

Detects arbitrage opportunities on Polymarket and executes via Simmer SDK.
Based on runesatsdev's research + IMDEA Networks paper ($39.59M extracted Apr 2024-Apr 2025).

Strategies:
1. Single-Condition: YES + NO ≠ $1.00 ($10.58M extracted historically)
2. NegRisk Rebalancing: Multi-outcome sum ≠ 100% (29× capital efficiency)

Usage:
    python arb_scanner.py              # Scan and report opportunities
    python arb_scanner.py --execute    # Scan and execute profitable trades
    python arb_scanner.py --dry-run    # Show what would be traded

Requires:
    SIMMER_API_KEY environment variable
"""

import os
import sys
import json
import argparse
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# =============================================================================
# Configuration
# =============================================================================

POLYMARKET_CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"
SIMMER_API_BASE = "https://api.simmer.markets"

# Detection thresholds (from IMDEA research)
MIN_PROFIT_THRESHOLD = 0.02  # 2 cents minimum (covers gas)
MIN_ROI_THRESHOLD = 0.01     # 1% minimum ROI to consider
WHALE_THRESHOLD = 5000       # $5K+ for whale detection

# Execution settings
DEFAULT_TRADE_SIZE = 5.0     # $5 per leg
MAX_TRADE_SIZE = 25.0        # $25 max per opportunity
TRADE_SOURCE = "sdk:arbscanner"

# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ArbOpportunity:
    """Represents a detected arbitrage opportunity"""
    market_id: str
    market_question: str
    opportunity_type: str  # 'single_condition' or 'negrisk'
    expected_profit: float
    roi: float
    capital_required: float
    action: str  # 'buy_both', 'sell_both', 'buy_all', 'sell_all'
    details: Dict
    timestamp: str
    
    def to_dict(self):
        return asdict(self)

# =============================================================================
# Polymarket Client (Free API - no auth needed)
# =============================================================================

def fetch_json(url: str, timeout: int = 15) -> Optional[Dict]:
    """Fetch JSON from URL"""
    try:
        req = Request(url, headers={"User-Agent": "SimmerArbScanner/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (HTTPError, URLError) as e:
        print(f"  ⚠️ Error fetching {url}: {e}")
        return None

def get_polymarket_markets(limit: int = 100) -> List[Dict]:
    """Fetch active markets from Polymarket Gamma API"""
    url = f"{POLYMARKET_GAMMA_URL}/markets?limit={limit}&active=true&closed=false"
    data = fetch_json(url)
    if isinstance(data, list):
        # Filter to only markets accepting orders
        return [m for m in data if m.get('acceptingOrders')]
    return []

def get_orderbook(token_id: str) -> Optional[Dict]:
    """Get orderbook for a token"""
    url = f"{POLYMARKET_CLOB_URL}/book?token_id={token_id}"
    return fetch_json(url)

def get_best_prices(orderbook: Dict) -> Tuple[float, float, float]:
    """Extract best bid, ask, and liquidity from orderbook"""
    if not orderbook:
        return 0, 0, 0
    
    asks = orderbook.get('asks', [])
    bids = orderbook.get('bids', [])
    
    best_ask = float(asks[0].get('price', 0)) if asks else 0
    best_bid = float(bids[0].get('price', 0)) if bids else 0
    
    # Sum top 5 levels of liquidity
    ask_liquidity = sum(float(a.get('size', 0)) for a in asks[:5])
    
    return best_bid, best_ask, ask_liquidity

# =============================================================================
# Arbitrage Detection
# =============================================================================

def detect_single_condition_arb(market: Dict) -> Optional[ArbOpportunity]:
    """
    Detect YES + NO ≠ $1.00 opportunities
    Historical: $10.58M extracted across 7,051 conditions
    """
    # Use gamma API data format - parse JSON strings if needed
    outcome_prices_raw = market.get('outcomePrices', '[]')
    outcomes_raw = market.get('outcomes', '[]')
    clob_token_ids_raw = market.get('clobTokenIds', '[]')
    
    try:
        outcome_prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        clob_token_ids = json.loads(clob_token_ids_raw) if isinstance(clob_token_ids_raw, str) else clob_token_ids_raw
    except json.JSONDecodeError:
        return None
    
    # Must be binary market (2 outcomes)
    if len(outcome_prices) != 2 or len(outcomes) != 2:
        return None
    
    try:
        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])
    except (ValueError, IndexError):
        return None
    
    if yes_price == 0 or no_price == 0:
        return None
    
    sum_price = yes_price + no_price
    deviation = abs(1.0 - sum_price)
    
    if deviation < MIN_PROFIT_THRESHOLD:
        return None
    
    # Use liquidity from market data
    liquidity = float(market.get('liquidityClob', 0) or market.get('liquidity', 0) or 1000)
    capital_required = min(MAX_TRADE_SIZE, liquidity * 0.1)
    expected_profit = deviation * capital_required
    roi = deviation / sum_price if sum_price > 0 else 0
    
    if roi < MIN_ROI_THRESHOLD:
        return None
    
    return ArbOpportunity(
        market_id=market.get('conditionId', '') or market.get('id', ''),
        market_question=market.get('question', 'Unknown')[:80],
        opportunity_type='single_condition',
        expected_profit=expected_profit,
        roi=roi,
        capital_required=capital_required,
        action='buy_both' if sum_price < 1.0 else 'sell_both',
        details={
            'yes_price': yes_price,
            'no_price': no_price,
            'sum_price': sum_price,
            'deviation': deviation,
            'outcomes': outcomes,
            'clob_token_ids': clob_token_ids,
        },
        timestamp=datetime.now().isoformat()
    )

def detect_negrisk_group_arb(markets: List[Dict], group_id: str) -> Optional[ArbOpportunity]:
    """
    Detect NegRisk GROUP opportunities (sum of YES prices across related markets ≠ 100%)
    Historical: $28.99M extracted, 29× capital efficiency
    """
    if len(markets) < 3:
        return None
    
    yes_prices = []
    outcomes_info = []
    
    for m in markets:
        prices_raw = m.get('outcomePrices', '[]')
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            if prices:
                yes_price = float(prices[0])
                yes_prices.append(yes_price)
                outcomes_info.append({
                    'question': m.get('question', '')[:40],
                    'yes_price': yes_price,
                    'market_id': m.get('conditionId', '')
                })
        except (json.JSONDecodeError, ValueError, IndexError):
            continue
    
    if len(yes_prices) < 3:
        return None
    
    prob_sum = sum(yes_prices)
    deviation = abs(1.0 - prob_sum)
    
    if deviation < MIN_PROFIT_THRESHOLD:
        return None
    
    # Estimate capital based on group liquidity
    total_liquidity = sum(float(m.get('liquidityClob', 0) or m.get('liquidity', 0) or 0) for m in markets)
    capital_required = min(MAX_TRADE_SIZE, total_liquidity * 0.05)
    expected_profit = deviation * capital_required
    roi = deviation / prob_sum if prob_sum > 0 else 0
    
    if roi < MIN_ROI_THRESHOLD:
        return None
    
    return ArbOpportunity(
        market_id=group_id,
        market_question=f"NegRisk Group: {markets[0].get('question', '')[:50]}...",
        opportunity_type='negrisk_group',
        expected_profit=expected_profit,
        roi=roi,
        capital_required=capital_required,
        action='buy_all_yes' if prob_sum < 1.0 else 'sell_all_yes',
        details={
            'num_buckets': len(markets),
            'yes_prices': yes_prices,
            'prob_sum': prob_sum,
            'deviation': deviation,
            'buckets': outcomes_info[:5],  # First 5 for display
            'capital_efficiency': '29×',
        },
        timestamp=datetime.now().isoformat()
    )


def detect_negrisk_arb(market: Dict) -> Optional[ArbOpportunity]:
    """
    Detect NegRisk opportunities (multi-outcome sum ≠ 100%)
    Historical: $28.99M extracted, 29× capital efficiency
    """
    # Check if it's a NegRisk market
    if not market.get('negRisk'):
        return None

    # Gamma API returns these as JSON strings - need to parse
    outcome_prices_raw = market.get('outcomePrices', '[]')
    outcomes_raw = market.get('outcomes', '[]')
    clob_token_ids_raw = market.get('clobTokenIds', '[]')

    try:
        outcome_prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        clob_token_ids = json.loads(clob_token_ids_raw) if isinstance(clob_token_ids_raw, str) else clob_token_ids_raw
    except json.JSONDecodeError:
        return None

    # NegRisk = 3+ mutually exclusive outcomes
    if len(outcome_prices) < 3:
        return None

    try:
        prices = [float(p) for p in outcome_prices]
    except (ValueError, TypeError):
        return None
    
    if any(p == 0 for p in prices):
        return None
    
    prob_sum = sum(prices)
    deviation = abs(1.0 - prob_sum)
    
    if deviation < MIN_PROFIT_THRESHOLD:
        return None
    
    liquidity = float(market.get('liquidityClob', 0) or market.get('liquidity', 0) or 1000)
    capital_required = min(MAX_TRADE_SIZE, liquidity * 0.1)
    expected_profit = deviation * capital_required
    roi = deviation / prob_sum if prob_sum > 0 else 0
    
    if roi < MIN_ROI_THRESHOLD:
        return None
    
    return ArbOpportunity(
        market_id=market.get('conditionId', '') or market.get('id', ''),
        market_question=market.get('question', 'Unknown')[:80],
        opportunity_type='negrisk',
        expected_profit=expected_profit,
        roi=roi,
        capital_required=capital_required,
        action='buy_all' if prob_sum < 1.0 else 'sell_all',
        details={
            'num_outcomes': len(outcomes),
            'prices': prices,
            'outcomes': outcomes,
            'prob_sum': prob_sum,
            'deviation': deviation,
            'clob_token_ids': clob_token_ids,
            'capital_efficiency': '29×',
        },
        timestamp=datetime.now().isoformat()
    )

# =============================================================================
# Simmer Integration
# =============================================================================

def get_simmer_api_key() -> str:
    """Get Simmer API key from environment"""
    key = os.environ.get('SIMMER_API_KEY')
    if not key:
        print("⚠️ SIMMER_API_KEY not set - scan only, no execution")
    return key or ''

def simmer_request(api_key: str, method: str, endpoint: str, data: dict = None) -> dict:
    """Make authenticated request to Simmer SDK"""
    url = f"{SIMMER_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    try:
        if method == "GET":
            req = Request(url, headers=headers)
        else:
            body = json.dumps(data).encode() if data else None
            req = Request(url, data=body, headers=headers, method=method)
        
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {"error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}

def import_market_to_simmer(api_key: str, condition_id: str) -> Optional[str]:
    """Import a Polymarket market to Simmer, return Simmer market ID"""
    # Try to find existing market
    result = simmer_request(api_key, "GET", f"/api/sdk/markets?q={condition_id}&limit=1")
    markets = result.get('markets', [])
    
    if markets:
        return markets[0].get('id')
    
    # Import if not found
    polymarket_url = f"https://polymarket.com/event/{condition_id}"
    result = simmer_request(api_key, "POST", "/api/sdk/markets/import", {
        "polymarket_url": polymarket_url
    })
    
    return result.get('market_id')

def execute_arb_trade(api_key: str, opp: ArbOpportunity, venue: str = "polymarket") -> Dict:
    """Execute arbitrage trade via Simmer"""
    
    # For single-condition: buy both YES and NO
    if opp.opportunity_type == 'single_condition':
        # Import market first
        simmer_market_id = import_market_to_simmer(api_key, opp.market_id)
        if not simmer_market_id:
            return {"error": "Could not import market"}
        
        trade_amount = min(DEFAULT_TRADE_SIZE, opp.capital_required / 2)
        
        results = []
        for side in ['yes', 'no']:
            result = simmer_request(api_key, "POST", "/api/sdk/trade", {
                "market_id": simmer_market_id,
                "side": side,
                "amount": trade_amount,
                "venue": venue,
                "source": TRADE_SOURCE,
                "reasoning": f"Arb: {opp.action}, sum={opp.details['sum_price']:.3f}, ROI={opp.roi:.1%}"
            })
            results.append(result)
        
        return {"trades": results, "type": "single_condition"}
    
    # For NegRisk: would need to buy all outcomes - more complex
    # For now, just report the opportunity
    return {"error": "NegRisk execution not yet implemented - multi-leg complexity"}

# =============================================================================
# Main Scanner
# =============================================================================

def run_scanner(execute: bool = False, dry_run: bool = False, venue: str = "simmer"):
    """Run the arbitrage scanner"""
    
    print("🔍 Simmer Arbitrage Scanner")
    print("=" * 60)
    print(f"  Execution: {'DRY RUN' if dry_run else 'ENABLED' if execute else 'SCAN ONLY'}")
    print(f"  Venue: {venue}")
    print(f"  Min profit: ${MIN_PROFIT_THRESHOLD:.2f}")
    print(f"  Min ROI: {MIN_ROI_THRESHOLD:.0%}")
    print()
    
    api_key = get_simmer_api_key()
    
    print("📡 Fetching Polymarket markets...")
    markets = get_polymarket_markets(limit=100)
    print(f"  Found {len(markets)} active markets")
    
    opportunities = []
    
    print("\n🔎 Scanning for arbitrage...")
    
    # Group markets by negRiskMarketID for group analysis
    negrisk_groups = defaultdict(list)
    
    for market in markets:
        neg_risk_id = market.get('negRiskMarketID')
        if neg_risk_id:
            negrisk_groups[neg_risk_id].append(market)
        
        # Check single-condition arb (binary markets)
        opp = detect_single_condition_arb(market)
        if opp:
            opportunities.append(opp)
            print(f"  ✅ Single-condition: {opp.market_question[:40]}...")
            print(f"     ROI: {opp.roi:.1%} | Profit: ${opp.expected_profit:.2f} | Action: {opp.action}")
    
    # Check NegRisk GROUPS
    print(f"\n  Checking {len(negrisk_groups)} NegRisk groups...")
    for group_id, group_markets in negrisk_groups.items():
        opp = detect_negrisk_group_arb(group_markets, group_id)
        if opp:
            opportunities.append(opp)
            print(f"  ✅ NegRisk Group ({opp.details['num_buckets']} buckets): {opp.market_question[:35]}...")
            print(f"     ROI: {opp.roi:.1%} | Profit: ${opp.expected_profit:.2f} | Sum: {opp.details['prob_sum']:.3f}")
    
    print(f"\n📊 Found {len(opportunities)} opportunities")
    
    if not opportunities:
        print("  No arbitrage opportunities detected at current thresholds")
        return
    
    # Sort by ROI
    opportunities.sort(key=lambda x: x.roi, reverse=True)
    
    print("\n🏆 Top Opportunities:")
    print("-" * 60)
    for i, opp in enumerate(opportunities[:5], 1):
        print(f"{i}. [{opp.opportunity_type}] {opp.market_question[:45]}...")
        print(f"   ROI: {opp.roi:.1%} | Profit: ${opp.expected_profit:.2f} | Capital: ${opp.capital_required:.2f}")
        print(f"   Action: {opp.action}")
        print()
    
    # Execute if requested
    if execute and api_key:
        print("\n⚡ Executing trades...")
        for opp in opportunities[:3]:  # Max 3 trades per run
            if dry_run:
                print(f"  [DRY RUN] Would execute {opp.action} on {opp.market_question[:40]}...")
            else:
                print(f"  Executing: {opp.market_question[:40]}...")
                result = execute_arb_trade(api_key, opp, venue=venue)
                if result.get('error'):
                    print(f"    ❌ {result['error']}")
                else:
                    print(f"    ✅ Trades submitted")
    
    # Summary
    total_profit = sum(o.expected_profit for o in opportunities)
    avg_roi = sum(o.roi for o in opportunities) / len(opportunities) if opportunities else 0
    
    print("\n" + "=" * 60)
    print("📈 Summary:")
    print(f"  Total opportunities: {len(opportunities)}")
    print(f"  Total potential profit: ${total_profit:.2f}")
    print(f"  Average ROI: {avg_roi:.1%}")
    print(f"  Single-condition: {sum(1 for o in opportunities if o.opportunity_type == 'single_condition')}")
    print(f"  NegRisk: {sum(1 for o in opportunities if o.opportunity_type in ('negrisk', 'negrisk_group'))}")

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simmer Arbitrage Scanner")
    parser.add_argument("--execute", action="store_true", help="Execute trades on opportunities")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be traded")
    parser.add_argument("--venue", default="simmer", choices=["simmer", "sandbox", "polymarket"],
                        help="Trading venue (default: simmer, sandbox is deprecated alias)")
    args = parser.parse_args()
    
    run_scanner(execute=args.execute, dry_run=args.dry_run, venue=args.venue)

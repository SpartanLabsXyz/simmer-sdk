#!/usr/bin/env python3
"""
Polymarket Copytrading — Reactor Mode (Pro tier)

Event-driven mirror of whale trades using Simmer's Reactor capability
(Server-Sent Events from a Simmer-brokered PolyNode pre-settlement stream).

Requires Simmer Pro: the SSE endpoint is gated by `users.is_pro`. For free
polling mode, use `copytrading_trader.py` instead — it's the portfolio-aware,
batch-oriented sibling that consumes the Polymarket Data API directly.

Runtime model:
- Long-lived Python task (runs inside your harness: OpenClaw, Hermes, Claude
  Code, or plain CLI)
- Connects to `https://api.simmer.markets/api/sdk/reactor/stream` via SSE
- Server-side filters events against the user's watchlist before delivery —
  the skill's local filter is a safety net, not the primary gate
- Programmatic decision in Python — no LLM in the hot path
- Trades execute via SimmerClient.trade() in the user's process, so both
  managed and external wallets work (SDK handles signing transparently)

Config flow:
- Reactor config (watchlist, caps) lives at /api/sdk/reactor/config
- Fetch on startup + on each config-changed signal
- Dashboard edits and PATCH calls invalidate the server-side cache, so
  changes take effect within seconds

Dedup:
- File-based seen set at ~/.simmer/polymarket-copytrading/reactor-seen.jsonl
- Append-only, survives restarts, capped size

Usage:
    # Dry run (logs decisions, does NOT execute trades)
    python reactor_trader.py --dry-run

    # Live with explicit venue
    python reactor_trader.py --venue sim
    python reactor_trader.py --venue polymarket

    # Stop after N events (for smoke test)
    python reactor_trader.py --dry-run --max-events 10
"""

import os
import sys
import json
import asyncio
import argparse
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

# Force line-buffered stdout so output is visible in non-TTY environments
sys.stdout.reconfigure(line_buffering=True)


# =============================================================================
# Constants
# =============================================================================

SKILL_SLUG = "polymarket-copytrading"
TRADE_SOURCE = "sdk:copytrading:reactor"
SEEN_SET_PATH = Path.home() / ".simmer" / "polymarket-copytrading" / "reactor-seen.jsonl"
SEEN_SET_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — rotate when exceeded

# SSE endpoint + config endpoints live under the reactor capability namespace
REACTOR_STREAM_PATH = "/api/sdk/reactor/stream"
REACTOR_CONFIG_PATH = "/api/sdk/reactor/config"
REACTOR_REACTIONS_PATH = "/api/sdk/reactor/reactions"

# Reconnect backoff
RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


# =============================================================================
# SimmerClient singleton (shared pattern with copytrading_trader.py)
# =============================================================================

_client = None


def get_client(venue: str):
    """Lazy-init SimmerClient. Exits if SIMMER_API_KEY is missing."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue=venue)
    return _client


def get_api_url() -> str:
    """Return the base API URL the SimmerClient is pointed at."""
    c = get_client(venue="polymarket")  # venue is irrelevant for URL lookup
    return getattr(c, "api_url", "https://api.simmer.markets")


def get_api_key() -> str:
    key = os.environ.get("SIMMER_API_KEY")
    if not key:
        print("Error: SIMMER_API_KEY environment variable not set")
        sys.exit(1)
    return key


# =============================================================================
# Seen-set (dedup across restarts)
# =============================================================================

def load_seen_set() -> Set[str]:
    """Load the persisted tx_hash seen set, rotating if oversized."""
    SEEN_SET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SEEN_SET_PATH.exists():
        return set()

    # Rotate if file has grown beyond the cap. Simple rename → start fresh.
    if SEEN_SET_PATH.stat().st_size > SEEN_SET_MAX_BYTES:
        rotated = SEEN_SET_PATH.with_suffix(
            f".jsonl.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.rotated"
        )
        SEEN_SET_PATH.rename(rotated)
        print(f"[reactor] rotated oversized seen set → {rotated.name}")
        return set()

    seen: Set[str] = set()
    try:
        with SEEN_SET_PATH.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    tx = rec.get("tx_hash")
                    if tx:
                        seen.add(tx)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"[reactor] failed to load seen set: {e}")
    return seen


def append_seen(tx_hash: str) -> None:
    """Append one tx_hash to the persistent seen set."""
    try:
        with SEEN_SET_PATH.open("a") as f:
            f.write(json.dumps({"tx_hash": tx_hash, "ts": datetime.utcnow().isoformat()}) + "\n")
    except Exception as e:
        print(f"[reactor] failed to append seen: {e}")


# =============================================================================
# HTTP helpers (httpx for async SSE + JSON)
# =============================================================================

def get_httpx():
    try:
        import httpx
    except ImportError:
        print("Error: httpx not installed. Run: pip install httpx")
        sys.exit(1)
    return httpx


async def fetch_config(httpx_mod) -> dict:
    """Fetch reactor config from the Simmer API. Returns default-shape dict on error."""
    url = f"{get_api_url()}{REACTOR_CONFIG_PATH}"
    headers = {"Authorization": f"Bearer {get_api_key()}"}
    try:
        async with httpx_mod.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 402:
                print("[reactor] 402: Reactor requires Simmer Pro. Upgrade at https://simmer.markets/dashboard")
                sys.exit(2)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[reactor] failed to fetch config: {e}")
        sys.exit(1)


async def post_reaction(httpx_mod, payload: dict) -> None:
    """Fire-and-forget reaction POST (logged on failure, doesn't raise)."""
    url = f"{get_api_url()}{REACTOR_REACTIONS_PATH}"
    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx_mod.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                print(f"[reactor] reaction POST failed {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[reactor] reaction POST errored: {e}")


# =============================================================================
# Sizing + decision logic (programmatic, no LLM)
# =============================================================================

def compute_mirror_size(taker_size: float, config: dict) -> Optional[float]:
    """
    Return the size to mirror (in shares, same units as taker_size), or None
    if the event should be skipped. Applies mirror_fraction, max_size, and the
    5-share Polymarket minimum.
    """
    mirror_fraction = float(config.get("mirror_fraction") or 0.01)
    max_size = float(config.get("max_size") or 50.0)

    raw = taker_size * mirror_fraction
    capped = min(raw, max_size)
    if capped < 5.0:  # Polymarket enforces min 5 shares per order
        return None
    return capped


def build_reasoning(event: dict, config: dict) -> str:
    whale = (event.get("taker_wallet") or "")[:10]
    market = event.get("market_title") or event.get("market_slug") or "unknown"
    side = event.get("taker_side") or "?"
    size = event.get("taker_size") or 0
    frac = config.get("mirror_fraction") or 0.01
    return (
        f"reactor mirror: whale {whale}... placed {side} {size:.0f} shares on "
        f"'{market}'; mirroring at {frac*100:.1f}% via Simmer Reactor SSE"
    )


# =============================================================================
# SSE event loop
# =============================================================================

async def run_once(
    httpx_mod,
    config: dict,
    seen: Set[str],
    dry_run: bool,
    venue: str,
    max_events: Optional[int],
) -> int:
    """
    One SSE session. Returns 0 on clean exit, non-zero on error (caller decides
    whether to reconnect).
    """
    url = f"{get_api_url()}{REACTOR_STREAM_PATH}"
    headers = {
        "Authorization": f"Bearer {get_api_key()}",
        "Accept": "text/event-stream",
    }
    processed = 0

    print(f"[reactor] connecting to {url}")
    async with httpx_mod.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 402:
                print("[reactor] 402: Reactor requires Simmer Pro. Upgrade at https://simmer.markets/dashboard")
                return 2
            if resp.status_code != 200:
                body = await resp.aread()
                print(f"[reactor] stream returned {resp.status_code}: {body[:200]!r}")
                return 1

            print(f"[reactor] stream connected — waiting for events (venue={venue}, dry_run={dry_run})")
            event_name: Optional[str] = None
            async for line in resp.aiter_lines():
                if not line:
                    event_name = None
                    continue
                if line.startswith(":"):
                    # Comment / heartbeat frame
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # "ready" frames from the server carry no settlement
                if event_name == "ready":
                    print(f"[reactor] ready frame: {event}")
                    continue

                if not isinstance(event, dict):
                    continue
                if event.get("event_type") != "settlement":
                    continue

                tx_hash = event.get("tx_hash")
                if not tx_hash:
                    continue
                if tx_hash in seen:
                    continue

                processed += 1
                handled = await handle_event(
                    httpx_mod=httpx_mod,
                    event=event,
                    config=config,
                    dry_run=dry_run,
                    venue=venue,
                )
                seen.add(tx_hash)
                append_seen(tx_hash)

                if max_events is not None and processed >= max_events:
                    print(f"[reactor] reached --max-events={max_events}, exiting")
                    return 0

    return 0


async def handle_event(
    httpx_mod,
    event: dict,
    config: dict,
    dry_run: bool,
    venue: str,
) -> str:
    """Return the decision string ('mirrored' | 'skipped_capped' | 'skipped_filter' | 'failed')."""
    taker_wallet = event.get("taker_wallet") or ""
    taker_size = float(event.get("taker_size") or 0)
    taker_side = event.get("taker_side") or "BUY"
    taker_token = event.get("taker_token") or ""
    taker_price = float(event.get("taker_price") or 0)
    market_title = event.get("market_title") or event.get("market_slug") or "unknown"
    tx_hash = event.get("tx_hash") or ""

    whale_label = taker_wallet[:10] + "..." if taker_wallet else "unknown"
    print(
        f"[reactor] event: whale={whale_label} side={taker_side} "
        f"size={taker_size:.0f} price={taker_price:.4f} market='{market_title[:60]}' tx={tx_hash[:14]}"
    )

    mirror_size = compute_mirror_size(taker_size, config)
    if mirror_size is None:
        print(f"[reactor]   → skip: mirror size below 5-share minimum (raw={taker_size * float(config.get('mirror_fraction') or 0.01):.2f})")
        await post_reaction(httpx_mod, {
            "event_tx_hash": tx_hash,
            "taker_wallet": taker_wallet,
            "taker_side": taker_side,
            "taker_token": taker_token,
            "taker_price": taker_price,
            "taker_size": taker_size,
            "market_title": market_title,
            "decision": "skipped_filter",
            "reason": "mirror size below 5-share minimum",
            "raw_event": event,
        })
        return "skipped_filter"

    if dry_run:
        print(f"[reactor]   → DRY RUN: would mirror {taker_side} {mirror_size:.2f} shares @ {taker_price}")
        await post_reaction(httpx_mod, {
            "event_tx_hash": tx_hash,
            "taker_wallet": taker_wallet,
            "taker_side": taker_side,
            "taker_token": taker_token,
            "taker_price": taker_price,
            "taker_size": taker_size,
            "market_title": market_title,
            "decision": "mirrored",
            "reason": f"DRY RUN mirror {mirror_size:.2f} shares",
            "raw_event": event,
        })
        return "mirrored"

    # Live execution via SimmerClient
    client = get_client(venue=venue)
    try:
        # The skill uses token_id as the market key. SimmerClient.trade() accepts
        # polymarket_token_id on the polymarket venue.
        result = client.trade(
            token_id=taker_token,
            side=taker_side,
            size=mirror_size,
            price=taker_price,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=build_reasoning(event, config),
        )
        trade_id = None
        if isinstance(result, dict):
            trade_id = result.get("trade_id") or result.get("id")
        print(f"[reactor]   → mirrored: {result}")
        await post_reaction(httpx_mod, {
            "event_tx_hash": tx_hash,
            "taker_wallet": taker_wallet,
            "taker_side": taker_side,
            "taker_token": taker_token,
            "taker_price": taker_price,
            "taker_size": taker_size,
            "market_title": market_title,
            "decision": "mirrored",
            "trade_id": trade_id,
            "reason": f"mirrored {mirror_size:.2f} shares",
            "raw_event": event,
        })
        return "mirrored"
    except Exception as e:
        print(f"[reactor]   → failed: {e}")
        await post_reaction(httpx_mod, {
            "event_tx_hash": tx_hash,
            "taker_wallet": taker_wallet,
            "taker_side": taker_side,
            "taker_token": taker_token,
            "taker_price": taker_price,
            "taker_size": taker_size,
            "market_title": market_title,
            "decision": "failed",
            "reason": f"trade error: {e}",
            "raw_event": event,
        })
        return "failed"


# =============================================================================
# Main loop with reconnect
# =============================================================================

async def main_async(args) -> int:
    httpx_mod = get_httpx()

    seen = load_seen_set()
    print(f"[reactor] loaded {len(seen)} prior tx_hashes from seen set")

    config = await fetch_config(httpx_mod)
    if not config.get("enabled"):
        print("[reactor] config has enabled=false — set it via dashboard or PATCH /api/sdk/reactor/config")
        return 1
    wallets = config.get("wallets") or []
    print(f"[reactor] config: {len(wallets)} wallets, min_size={config.get('min_size')}, "
          f"mirror_fraction={config.get('mirror_fraction')}, max_size={config.get('max_size')}, "
          f"daily_cap={config.get('daily_cap')}, venue={config.get('venue')}")

    venue = args.venue or config.get("venue") or "sim"

    # Reconnect loop with exponential backoff
    backoff = RECONNECT_BASE_DELAY
    while True:
        try:
            exit_code = await run_once(
                httpx_mod=httpx_mod,
                config=config,
                seen=seen,
                dry_run=args.dry_run,
                venue=venue,
                max_events=args.max_events,
            )
            if exit_code != 0:
                return exit_code
            if args.max_events is not None:
                return 0
            backoff = RECONNECT_BASE_DELAY  # successful session resets backoff
            print("[reactor] stream closed cleanly — reconnecting")
        except asyncio.CancelledError:
            print("[reactor] cancelled, shutting down")
            return 0
        except Exception as e:
            print(f"[reactor] stream error: {e} — reconnecting in {backoff:.1f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_DELAY)
            continue

        # If we got here via clean close, wait a beat before reconnecting
        await asyncio.sleep(backoff)


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Copytrading Reactor Mode (Pro) — event-driven whale mirror via Simmer SSE",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log decisions without executing trades")
    parser.add_argument("--venue", choices=["sim", "polymarket", "kalshi"], default=None,
                        help="Override venue (default: from reactor config)")
    parser.add_argument("--max-events", type=int, default=None,
                        help="Stop after processing N events (for smoke tests)")
    args = parser.parse_args()

    # Graceful SIGINT
    def _sigint(*_):
        print("\n[reactor] SIGINT received, shutting down")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint)

    try:
        exit_code = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

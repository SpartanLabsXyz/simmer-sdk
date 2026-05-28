"""News-recency veto for short-dated news-resolution markets.

This guard is defensive: it blocks entries only when a market looks tied to a
scheduled macro/news event and the current time is inside the post-release
lookback window. Continuous-feed crypto Up/Down markets are intentionally not
classified as news-resolution markets.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_LOOKBACK_S = 30
DEFAULT_SCHEDULE_PATHS = (
    Path.cwd() / "shared-knowledge" / "data" / "macro-news-schedule.json",
    Path.cwd().parent / "simmer-labs" / "shared-knowledge" / "data" / "macro-news-schedule.json",
    Path.home() / "Documents" / "code" / "active" / "kozy" / "simmer-labs" / "shared-knowledge" / "data" / "macro-news-schedule.json",
)

NEWS_KEYWORDS = (
    "cpi",
    "consumer price index",
    "inflation",
    "fomc",
    "fed decision",
    "federal reserve",
    "interest rate decision",
    "unemployment",
    "jobs report",
    "nonfarm payroll",
    "non-farm payroll",
    "payrolls",
    "bls",
    "earnings",
    "eps",
    "revenue",
)

CONTINUOUS_FEED_PATTERNS = (
    re.compile(r"\b(btc|bitcoin|eth|ethereum|sol|solana|xrp)\s+up\s+or\s+down\b", re.I),
    re.compile(r"\bup\s+or\s+down\s*-\s*\w{3}\s+\d{1,2},?\s+\d{1,2}:\d{2}", re.I),
)


def load_macro_news_schedule(path: Optional[str] = None) -> Dict[str, Any]:
    """Load a macro-news schedule JSON.

    Returns an empty schedule when no configured file exists so installed skills
    can fail closed only on explicit schedule data, not on missing local files.
    """

    candidates: List[Path] = []
    explicit_path = path or os.environ.get("SIMMER_NEWS_SCHEDULE_PATH")
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    candidates.extend(DEFAULT_SCHEDULE_PATHS)

    for candidate in candidates:
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"events": []}
    return {"events": []}


def is_news_resolution_market(market_id: Any) -> bool:
    """Return True when a market descriptor looks tied to a scheduled news drop."""

    text_parts: List[str] = []
    if isinstance(market_id, dict):
        for key in ("id", "market_id", "question", "title", "slug", "category", "description"):
            value = market_id.get(key)
            if value:
                text_parts.append(str(value))
    else:
        text_parts.append(str(market_id or ""))

    text = " ".join(text_parts).lower()
    if any(pattern.search(text) for pattern in CONTINUOUS_FEED_PATTERNS):
        return False
    return any(keyword in text for keyword in NEWS_KEYWORDS)


def is_within_news_window(
    market_id: Any,
    schedule: Any,
    lookback_s: int = DEFAULT_LOOKBACK_S,
    now: Optional[datetime] = None,
) -> bool:
    """Return True when a news-eligible market is inside a recent event window."""

    in_window, _event = news_window_match(market_id, schedule, lookback_s=lookback_s, now=now)
    return in_window


def news_window_match(
    market_id: Any,
    schedule: Any,
    lookback_s: int = DEFAULT_LOOKBACK_S,
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Return whether the veto fires and the matching schedule event."""

    if lookback_s <= 0 or not is_news_resolution_market(market_id):
        return False, None

    current = _coerce_aware_datetime(now) or datetime.now(timezone.utc)
    for event in _iter_events(schedule):
        event_dt = _parse_event_time(event)
        if not event_dt:
            continue
        age_s = (current - event_dt).total_seconds()
        if 0 <= age_s <= lookback_s:
            return True, event
    return False, None


def _iter_events(schedule: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(schedule, list):
        for item in schedule:
            yield _normalize_event(item)
        return

    if isinstance(schedule, dict):
        events = schedule.get("events", [])
        if isinstance(events, list):
            for item in events:
                yield _normalize_event(item)


def _normalize_event(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {"timestamp": item}


def _parse_event_time(event: Dict[str, Any]) -> Optional[datetime]:
    for key in ("timestamp", "datetime", "time", "released_at", "release_time"):
        value = event.get(key)
        if value:
            return _coerce_aware_datetime(value)
    return None


def _coerce_aware_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


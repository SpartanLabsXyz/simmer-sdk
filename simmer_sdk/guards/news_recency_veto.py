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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


DEFAULT_LOOKBACK_S = 30
DEFAULT_SCHEDULE_PATHS = (
    Path.cwd() / "shared-knowledge" / "data" / "macro-news-schedule.json",
    Path.cwd().parent / "simmer-labs" / "shared-knowledge" / "data" / "macro-news-schedule.json",
    Path.home() / "Documents" / "code" / "active" / "kozy" / "simmer-labs" / "shared-knowledge" / "data" / "macro-news-schedule.json",
)

EVENT_CATEGORY_PATTERNS = {
    "CPI": (
        re.compile(r"\bcpi\b", re.I),
        re.compile(r"\bconsumer\s+price\s+index\b", re.I),
        re.compile(r"\binflation\b", re.I),
    ),
    "FOMC": (
        re.compile(r"\bfomc\b", re.I),
        re.compile(r"\bfed(?:eral\s+reserve)?\s+(?:decision|rates?|meeting)\b", re.I),
        re.compile(r"\binterest\s+rate\s+decision\b", re.I),
    ),
    "BLS_JOBS": (
        re.compile(r"\bbls\b", re.I),
        re.compile(r"\bunemployment\b", re.I),
        re.compile(r"\bjobs\s+report\b", re.I),
        re.compile(r"\bnon-?farm\s+payrolls?\b", re.I),
        re.compile(r"\bpayrolls\b", re.I),
    ),
    "EARNINGS": (
        re.compile(r"\bearnings\b", re.I),
        re.compile(r"\beps\b", re.I),
        re.compile(r"\bquarterly\s+results\b", re.I),
    ),
}

SCHEDULE_CATEGORY_ALIASES = {
    "CPI": {"CPI", "CONSUMER_PRICE_INDEX", "INFLATION"},
    "FOMC": {"FOMC", "FED", "FEDERAL_RESERVE", "INTEREST_RATE_DECISION"},
    "BLS_JOBS": {
        "BLS",
        "BLS_UNEMPLOYMENT",
        "BLS_UNEMPLOYMENT_NONFARM_PAYROLLS",
        "JOBS_REPORT",
        "NONFARM_PAYROLLS",
        "NON_FARM_PAYROLLS",
        "UNEMPLOYMENT",
    },
    "EARNINGS": {"EARNINGS", "QUARTERLY_EARNINGS", "EPS", "QUARTERLY_RESULTS"},
}

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

    text = _market_text(market_id)
    if any(pattern.search(text) for pattern in CONTINUOUS_FEED_PATTERNS):
        return False
    return bool(_market_event_categories(market_id))


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

    market_categories = _market_event_categories(market_id)
    if not market_categories:
        return False, None

    current = _coerce_aware_datetime(now, allow_naive=True) or datetime.now(timezone.utc)
    for event in _iter_events(schedule):
        if not (market_categories & _event_categories(event)):
            continue
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
            return _coerce_aware_datetime(value, allow_naive=False)
    return None


def _coerce_aware_datetime(value: Any, *, allow_naive: bool = True) -> Optional[datetime]:
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
        if not allow_naive:
            return None
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _market_text(market_id: Any) -> str:
    text_parts: List[str] = []
    if isinstance(market_id, dict):
        for key in ("id", "market_id", "question", "title", "slug", "category", "description"):
            value = market_id.get(key)
            if value:
                text_parts.append(str(value))
    else:
        text_parts.append(str(market_id or ""))
    return " ".join(text_parts)


def _market_event_categories(market_id: Any) -> Set[str]:
    text = _market_text(market_id)
    if any(pattern.search(text) for pattern in CONTINUOUS_FEED_PATTERNS):
        return set()
    return {
        category
        for category, patterns in EVENT_CATEGORY_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }


def _event_categories(event: Dict[str, Any]) -> Set[str]:
    categories: Set[str] = set()
    for key in ("category", "type", "name", "id"):
        value = event.get(key)
        if not value:
            continue
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").upper()
        for category, aliases in SCHEDULE_CATEGORY_ALIASES.items():
            if normalized in aliases:
                categories.add(category)
        for category, patterns in EVENT_CATEGORY_PATTERNS.items():
            if any(pattern.search(str(value)) for pattern in patterns):
                categories.add(category)
    return categories

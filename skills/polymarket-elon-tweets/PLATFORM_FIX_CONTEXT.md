# Elon Tweets Market Discovery — Bug Context & Platform Fix Required

**PR:** #14 (fix/elon-tweets-market-discovery)  
**Investigated:** 2026-03-05  
**Status:** Skill-side fixes in this PR. Platform fix still needed in `simmer` backend repo.

---

## The User-Reported Issue

User running the `polymarket-elon-tweets` skill reported:

> "cannot find the market of max current_probability ex: 160-178, 180-199 ... only found following active markets which below 0.1"

```python
{'market_id': '02e17ae8-e1bc-4b63-a0aa-bcdfc33fae62', 'question': 'Will Elon Musk post 220-239 tweets from February 27 to March 6, 2026?', 'current_probability': 0.043}
{'market_id': 'db0bc8c3-a849-4ee0-b6ed-27ab5523d589', 'question': 'Will Elon Musk post 240-259 tweets from February 27 to March 6, 2026?', 'current_probability': 0.0255}
{'market_id': 'd5801bf9-5a87-493e-a351-b8f954871992', 'question': 'Will Elon Musk post 260-279 tweets from February 27 to March 6, 2026?', 'current_probability': 0.01}
```

The actual high-probability markets (160-179 at ~40%, 180-199 at ~37%) were not being found.

---

## Root Cause Investigation

### Finding 1: Search query returns 0 results (skill bug — fixed in this PR)

```
GET /api/sdk/markets?q=elon+musk+tweets&status=active&limit=100  →  0 markets
GET /api/sdk/markets?q=elon+musk&limit=100                       →  62 markets ✓
GET /api/sdk/markets?q=tweets&limit=100                          →  55 markets ✓
GET /api/sdk/markets?q=musk+tweets&limit=100                     →  0 markets
```

The three-word phrase `"elon musk tweets"` is not indexed. The skill always fell through to the import path.

### Finding 2: Import endpoint returns incomplete market set (platform bug — NOT fixed here)

```python
POST /api/sdk/markets/import
{"url": "https://polymarket.com/event/elon-musk-of-tweets-february-27-march-6"}

# Response:
{
  "success": True,
  "already_imported": True,
  "event_id": "06f17f03-f65a-4654-821c-615c5d8c75f5",
  "markets_imported": 0,
  "markets_skipped": 0,
  "markets": [12 markets, ALL with probability < 0.02]
}
```

The 12 markets returned are all the low-probability tail buckets (220-239 through 440-459). The 4 high-probability central buckets are **absent from the import response entirely**.

Full import response markets (sorted by probability):
```
prob=0.0200  220-239 tweets  market_id=02e17ae8-e1bc-4b63-a0aa-bcdfc33fae62
prob=0.0065  240-259 tweets  market_id=db0bc8c3-a849-4ee0-b6ed-27ab5523d589
prob=0.0045  260-279 tweets  market_id=d5801bf9-5a87-493e-a351-b8f954871992
prob=0.0025  280-299 tweets  market_id=99b36e49-4d46-4841-b25e-8afd00cf6386
prob=0.0015  300-319 tweets  market_id=d3b0c9ae-ce4e-4c96-bf1f-9d6c7da6315d
prob=0.0005  320-339 tweets  market_id=d1d6b6bc-97da-4faa-8036-c25a88ff69a6
prob=0.0005  340-359 tweets  market_id=e37c5fdf-386f-4b71-a27a-5f77053ce380
prob=0.0005  360-379 tweets  market_id=a5b3b2b3-ba3b-41b1-9978-f3fdc92e7e5c
prob=0.0005  380-399 tweets  market_id=203c70be-5f44-441e-a69f-c9371c859d52
prob=0.0005  400-419 tweets  market_id=2815f484-417b-47d7-8af4-9a689205864a
prob=0.0005  420-439 tweets  market_id=97785c2c-8919-4076-a7b3-aeda640f8813
prob=0.0005  440-459 tweets  market_id=0e95b9c4-e24a-4026-9502-d4a023ff06f2
```

### Finding 3: High-prob markets exist in search but missing `market_id` (platform bug — NOT fixed here)

Using `GET /api/sdk/markets?q=tweets&limit=100`, the high-probability markets ARE returned but with critical fields missing:

```
prob=0.4035  160-179 tweets  event_id=None  market_id=MISSING
prob=0.3669  180-199 tweets  event_id=None  market_id=MISSING
prob=0.1125  200-219 tweets  event_id=None  market_id=MISSING
prob=0.0857  140-159 tweets  event_id=None  market_id=MISSING
```

These markets exist in the search index but are not properly linked to their event and have no `market_id`, making them untradeable.

---

## What This PR Fixes (Skill Side)

1. **Search query**: `"elon musk tweets"` → `"elon musk"` — fixes the primary discovery failure
2. **Grouping**: No longer drops markets with `event_id=None`. Derives a stable group key from the date range in the question text (e.g. `derived:february 27 to march 6`)
3. **Import fallback search**: Changed from `search_markets(title[:50])` (would likely return 0) to `search_markets("elon musk")`
4. **Missing market_id logging**: Was silently skipped. Now logs a clear warning pointing at the platform issue

### Known limitation of this PR

Fix #2 (grouping by derived key) causes the `if not events:` import gate to be skipped when search finds markets. Those search-found markets have no `market_id`, so nothing can be traded. The skill ends up with more markets visible but fewer tradeable ones than before this PR.

**The skill PR and platform fix need to ship together** to fully resolve the user issue.

---

## Platform Fix Required (in `simmer` backend repo)

### Fix A: Import endpoint must return all markets for the event

**Endpoint:** `POST /api/sdk/markets/import`  
**Event:** `https://polymarket.com/event/elon-musk-of-tweets-february-27-march-6`  
**Event ID in Simmer:** `06f17f03-f65a-4654-821c-615c5d8c75f5`

The import response only returns 12 of the ~16 markets. The 4 high-probability central buckets were imported at some earlier point (they exist in the DB — they show up in search) but are not being returned in the `markets` array of the `already_imported` response.

**Expected behavior:** `already_imported` response should include ALL markets associated with the event, not just the subset that was originally imported.

**Hypothesis:** The initial import may have had a limit or filtered markets by some criterion (e.g., only imported markets with probability below a threshold, or only imported markets that were actively tradeable on Polymarket at import time). The high-probability central buckets may have been in a near-resolved state at import time and were skipped.

### Fix B: Search results must include `market_id` for all markets

**Endpoint:** `GET /api/sdk/markets?q=tweets`

Markets returned from search are missing `market_id` and `event_id` for the Feb 27-Mar 6 event's high-probability buckets. These fields are present for other events (e.g., the March 3-10 event returns markets with proper `event_id=0eca934c-8119-486c-a509-cf208f20c822`).

**Expected behavior:** All active markets returned from search should include `market_id` and `event_id`.

**Likely cause:** The markets were added to the search index but not properly linked to the event record (possibly the import that added them didn't complete the event linkage step).

---

## Verification Steps After Platform Fix

1. `GET /api/sdk/markets?q=elon+musk&limit=100` should return ALL 16 Feb 27-Mar 6 markets, each with a non-null `market_id` and `event_id=06f17f03-f65a-4654-821c-615c5d8c75f5`
2. `POST /api/sdk/markets/import` for the event URL should return all 16 markets in the `markets` array
3. Running `python elon_tweets.py` (dry run) against the fixed API should show the 160-179 and 180-199 buckets as target candidates

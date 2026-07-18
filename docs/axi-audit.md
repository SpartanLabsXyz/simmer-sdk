# Simmer SDK → AXI Principles Audit

## Audit Date: 2026-07-18
Audited against: Kun Chen's 10 AXI principles (Agent eXperience Interface)
SDK under review: simmer-sdk (Simmer SDK Python client)
Reference: https://axi.md

---

## The 10 Principles (from axi.md)

1. **Token-efficient output** — Use TOON format for ~40% token savings over JSON
2. **Minimal default schemas** — 3-4 fields per list item, not 10+
3. **Content truncation** — Truncate large text with size hints and `--full` escape hatch
4. **Pre-computed aggregates** — Include aggregated counts and statuses that eliminate round trips
5. **Definitive empty states** — Explicit "0 results" rather than ambiguous empty output
6. **Structured errors & exit codes** — Idempotent mutations, structured errors, no interactive prompts, fail loud on unknown flags
7. **Ambient context** — Install opt-in session integrations first, then offer an on-demand skill
8. **Content first** — Running with no arguments shows live data, not help text
9. **Contextual disclosure** — Include next-step suggestions after each output
10. **Consistent way to get help** — Concise per-subcommand reference when agents need it

---

## Audit Findings

### Principle 1: Token-Efficient Output (TOON format)
**Score: 3/10 — FAIL 🔴**

**Current state:** All SDK returns use full JSON objects with complete schemas. Every field is returned in every response, multiplied by every row in list responses. Example: `get_markets()` returns complete `Market` objects with 13+ fields each. `TradeResult` is always the full dataclass with 16 fields.

**AXI wants:** TOON format (~40% token savings). A compact CSV-like output with named headers:
```
trades[5]{trade_id,side,venue,status,cost}:
  "t_abc123",BUY,polymarket,filled,45.20
  "t_def456",SELL,kalshi,filled,12.80
```

**Impact:** Agents calling `get_markets()` on 1697 results currently consume massive tokens. TOON would cut this ~40%.

**Priority:** HIGH — biggest single token savings opportunity.

---

### Principle 2: Minimal Default Schemas  
**Score: 4/10 — NEEDS WORK 🟡**

**Current state:** `Market` dataclass has 13 fields (many Optional). `get_markets()` returns ALL of them. `get_trades()` returns full `TradeResult` with 16 fields.

**AXI wants:** 3-4 fields per list item. Agents should get identifier, title, status by default. `--fields` flag for more.

**Partial win:** The `TradeResult` is leaner (mostly numeric, only needed fields). But list responses dump everything.

**Priority:** MEDIUM-HIGH — compounds with principle 1.

---

### Principle 3: Content Truncation
**Score: 7/10 — MOSTLY OK 🟢**

Most SDK fields are short (IDs, prices, status strings). No large text/bodies in typical responses. The main area where this matters is error messages — which go through the `error` field in `TradeResult` and are already concise.

**Priority:** LOW — not a major issue for this SDK.

---

### Principle 4: Pre-Computed Aggregates
**Score: 5/10 — PARTIAL 🟡**

**Current state:** `get_portfolio()` returns totals (total_pnl, active_positions, etc.) — this is good. But `get_positions()` returns just a list without a summary count.

**AXI wants:** Include total counts inline. `"showing 30 of 1697 total markets"`.

**Priority:** MEDIUM — would eliminate many follow-up calls.

---

### Principle 5: Definitive Empty States
**Score: 2/10 — FAIL 🔴**

**Current state:** Empty lists return `[]` or `{}`. The agent can't tell if this means "no results for this query" (success), "something went wrong" (failure), or "the API is down" (infrastructure issue).

**AXI wants:** `"markets: 0 markets found matching your filters"` with clear success confirmation.

**Priority:** HIGH — currently agents waste tokens re-querying empty results to verify.

---

### Principle 6: Structured Errors & Exit Codes
**Score: 6/10 — PARTIAL 🟡**

**Current state:** Errors come through `success=False` + `error` string in `TradeResult`. Python exceptions for API failures. But error messages are sometimes raw API responses, not translated to agent-actionable suggestions.

**AXI wants:** `error: --market_id is required` + `help: get_portfolio(venue="polymarket")`. Structured, with actionable next steps.

**Priority:** MEDIUM-HIGH — error handling is the #1 thing agents struggle with.

---

### Principle 7: Ambient Context (Session Hooks)
**Score: N/A — NOT APPLICABLE**

AXI's principle 7 is about CLIs integrating into coding agent sessions (Claude Code, Codex). Our SDK is a Python library, not a CLI. The `simmer` CLI could adopt this pattern in the future.

**Priority:** LOW — not relevant until we build a dedicated CLI.

---

### Principle 8: Content First (No Args = Show Live Data)
**Score: 5/10 — PARTIAL 🟡**

**Current state:** `get_portfolio()` with no args returns all venues. `get_positions()` requires venue. `get_markets()` requires filtering. Calling with no args often returns everything or errors.

**AXI wants:** `simmer portfolio` should show the most relevant live data immediately. Not a help message.

**Priority:** MEDIUM — matters when/if we build a dedicated CLI.

---

### Principle 9: Contextual Disclosure (Next Steps)
**Score: 3/10 — FAIL 🔴**

**Current state:** SDK responses are purely data. No "what can I do next?" hints. Agents must know the full API surface upfront.

**AXI wants:** After a trade result: `help: cancel_order("order_123") to cancel, get_portfolio() to check balance`.

**Priority:** MEDIUM-HIGH — makes agent self-discovery possible.

---

### Principle 10: Consistent Help
**Score: N/A — PARTIAL**

**Current state:** Python docstrings exist on methods. `help()` on SDK objects shows signatures. But no machine-readable help that agents can request at runtime.

**Priority:** MEDIUM — would make a CLI much more powerful for agents.

---

## Summary Scorecard

| # | Principle | Score | Priority |
|---|---|---|---|
| 1 | Token-efficient output | 3/10 | 🔴 HIGH |
| 2 | Minimal default schemas | 4/10 | 🟡 MEDIUM-HIGH |
| 3 | Content truncation | 7/10 | 🟢 LOW |
| 4 | Pre-computed aggregates | 5/10 | 🟡 MEDIUM |
| 5 | Definitive empty states | 2/10 | 🔴 HIGH |
| 6 | Structured errors | 6/10 | 🟡 MEDIUM-HIGH |
| 7 | Ambient context | N/A | 🟢 LOW |
| 8 | Content first | 5/10 | 🟡 MEDIUM |
| 9 | Contextual disclosure | 3/10 | 🟡 MEDIUM-HIGH |
| 10 | Consistent help | N/A | 🟡 MEDIUM |

**Overall: 4.3/10** — the SDK works but was designed for human API consumers, not agent-first.

---

## Top 4 Quick Wins (This Week)

### 1. Add response summary mode
A lightweight response mode that returns only the 3-4 most important fields + a count. Biggest token saver for the easiest change.

### 2. Definitive empty states
`get_markets()` returns `{"markets": [], "total": 0, "message": "No markets matched your filters"}` instead of bare `[]`.

### 3. Structured error messages
Translate API errors into actionable strings: not `"HTTP 400: invalid market_id"` but `"market_id not found. Check get_markets() for valid IDs."`

### 4. Response include help hints
Add an optional `include_hints=True` parameter that appends a `next_steps` list to responses showing what calls make sense next.

---
Audit completed: 2026-07-18

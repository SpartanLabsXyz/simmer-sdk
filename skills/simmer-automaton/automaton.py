#!/usr/bin/env python3
"""
Simmer Automaton — Survival Meta-Skill

Conway-inspired strategy allocator with survival pressure.
Discovers installed trading skills, selects which to run each cycle
using an epsilon-greedy bandit, and degrades gracefully as budget depletes.

Usage:
    python automaton.py --budget 50 --days 30 --live   # Start/continue
    python automaton.py --live --quiet                  # Cron mode
    python automaton.py --status                        # Survival stats
    python automaton.py --skills                        # Discovered skills
    python automaton.py --config                        # Show config
    python automaton.py --set KEY=VALUE                 # Update config
    python automaton.py --reset                         # Fresh start

Requires:
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
"""

import os
import sys
import json
import random
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force line-buffered stdout for cron/Docker/OpenClaw
sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

def _load_config(schema, skill_file, config_filename="config.json"):
    """Load config with priority: config.json > env vars > defaults."""
    config_path = Path(skill_file).parent / config_filename
    file_cfg = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    result = {}
    for key, spec in schema.items():
        if key in file_cfg:
            result[key] = file_cfg[key]
        elif spec.get("env") and os.environ.get(spec["env"]):
            val = os.environ.get(spec["env"])
            type_fn = spec.get("type", str)
            try:
                result[key] = type_fn(val) if type_fn != str else val
            except (ValueError, TypeError):
                result[key] = spec.get("default")
        else:
            result[key] = spec.get("default")
    return result


def _update_config(updates, skill_file, config_filename="config.json"):
    """Update config values and save to file."""
    config_path = Path(skill_file).parent / config_filename
    existing = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    existing.update(updates)
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
    return existing


CONFIG_SCHEMA = {
    "budget_usd": {"env": "SIMMER_AUTOMATON_BUDGET", "default": 50.0, "type": float},
    "horizon_days": {"env": "SIMMER_AUTOMATON_DAYS", "default": 30, "type": int},
    "epsilon": {"env": "SIMMER_AUTOMATON_EPSILON", "default": 0.2, "type": float},
    "epsilon_decay": {"env": "SIMMER_AUTOMATON_EPSILON_DECAY", "default": 0.995, "type": float},
    "min_epsilon": {"env": "SIMMER_AUTOMATON_MIN_EPSILON", "default": 0.05, "type": float},
    "max_concurrent": {"env": "SIMMER_AUTOMATON_MAX_SKILLS", "default": 2, "type": int},
    "cycle_interval": {"env": "SIMMER_AUTOMATON_INTERVAL", "default": 300, "type": int},
}

_config = _load_config(CONFIG_SCHEMA, __file__)

# =============================================================================
# SimmerClient singleton
# =============================================================================

_client = None


def get_client(live=True):
    """Lazy-init SimmerClient singleton.

    When venue is 'simmer', always sets live=True since $SIM trades
    are the paper trading mechanism (no --live flag needed).
    """
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
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        # $SIM venue is inherently paper trading — always live
        effective_live = True if venue == "simmer" else live
        _client = SimmerClient(api_key=api_key, venue=venue, live=effective_live)
    return _client


def _is_paper_venue():
    """Check if we're on the simmer (paper trading) venue."""
    return os.environ.get("TRADING_VENUE", "polymarket") == "simmer"


# =============================================================================
# State persistence
# =============================================================================

STATE_FILE = Path(__file__).parent / "state.json"
JOURNAL_FILE = Path(__file__).parent / "cycle_journal.jsonl"
JOURNAL_PREV_FILE = Path(__file__).parent / "cycle_journal.prev.jsonl"
JOURNAL_MAX_ENTRIES = 1000  # ~3 days at 5-min interval


def load_state(path=None):
    """Load state from disk. Returns None if no state exists."""
    p = Path(path) if path else STATE_FILE
    if not p.exists():
        return None
    try:
        with open(p) as f:
            state = json.load(f)
        # Migrate: realized_pnl → total_pnl (v1.0 → v1.1)
        if "realized_pnl" in state and "total_pnl" not in state:
            state["total_pnl"] = state.pop("realized_pnl")
        return state
    except (json.JSONDecodeError, IOError):
        return None


def save_state(state, path=None):
    """Save state to disk."""
    p = Path(path) if path else STATE_FILE
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


def write_journal_entry(entry):
    """Append a cycle journal entry to JSONL file. Rotates when over max entries."""
    # Rotate if needed
    if JOURNAL_FILE.exists():
        try:
            with open(JOURNAL_FILE) as jf:
                line_count = sum(1 for _ in jf)
            if line_count >= JOURNAL_MAX_ENTRIES:
                if JOURNAL_PREV_FILE.exists():
                    JOURNAL_PREV_FILE.unlink()
                JOURNAL_FILE.rename(JOURNAL_PREV_FILE)
        except IOError:
            pass

    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def read_journal(n=20):
    """Read last N journal entries."""
    if not JOURNAL_FILE.exists():
        return []
    try:
        with open(JOURNAL_FILE) as f:
            lines = f.readlines()
        entries = []
        for line in lines[-n:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
    except IOError:
        return []


def init_state(budget, days):
    """Create fresh state."""
    return {
        "version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "budget_usd": budget,
        "horizon_days": days,
        "spent_usd": 0.0,
        "total_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "tier": "normal",
        "epsilon": _config["epsilon"],
        "cycle_count": 0,
        "tier_history": [],
        "skills": {},
    }


# =============================================================================
# Skill discovery
# =============================================================================

def _parse_frontmatter(text, filename=""):
    """Parse YAML frontmatter from SKILL.md. Returns dict or None."""
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    block = text[3:end].strip()
    # Minimal YAML parsing for the fields we need
    result = {}
    for line in block.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key == "metadata":
                try:
                    result["metadata"] = json.loads(val)
                except json.JSONDecodeError as e:
                    print(f"Warning: [{filename}] SKILL.md metadata JSON parse error: {e}")
            else:
                result[key] = val.strip('"').strip("'")
    return result


def _find_source_tag(skill_dir):
    """Grep Python files in skill_dir for TRADE_SOURCE = "sdk:..."."""
    for py_file in skill_dir.glob("*.py"):
        try:
            content = py_file.read_text()
            for line in content.split("\n"):
                if "TRADE_SOURCE" in line and "=" in line:
                    # Extract the string value
                    parts = line.split("=", 1)
                    val = parts[1].strip().strip('"').strip("'")
                    if val.startswith("sdk:"):
                        return val
        except (IOError, UnicodeDecodeError):
            pass
    return None


def discover_skills(skills_dir=None):
    """Scan sibling skill dirs for automaton-managed skills.

    Returns list of {slug, source_tag, entrypoint, dir}.
    """
    if skills_dir is None:
        skills_dir = Path(__file__).parent.parent

    discovered = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            text = skill_md.read_text()
        except (IOError, UnicodeDecodeError):
            continue

        fm = _parse_frontmatter(text, filename=child.name)
        if not fm:
            continue

        metadata = fm.get("metadata", {})
        clawdbot = metadata.get("clawdbot", metadata)
        automaton_cfg = clawdbot.get("automaton") if isinstance(clawdbot, dict) else None
        if not automaton_cfg:
            # Also check top-level metadata
            automaton_cfg = metadata.get("automaton")

        if not isinstance(automaton_cfg, dict) or not automaton_cfg.get("managed"):
            continue

        entrypoint = automaton_cfg.get("entrypoint")
        if not entrypoint:
            continue

        # Verify entrypoint exists
        if not (child / entrypoint).exists():
            continue

        slug = child.name  # Use directory name as stable state key
        source_tag = _find_source_tag(child)
        if not source_tag:
            print(f"  Warning: {slug} has no TRADE_SOURCE — P&L tracking and registry attribution won't work")
            source_tag = f"sdk:{child.name}"

        discovered.append({
            "slug": slug,
            "source_tag": source_tag,
            "entrypoint": entrypoint,
            "dir": str(child),
        })

    return discovered


# =============================================================================
# P&L fetching
# =============================================================================

def fetch_pnl_by_source(client):
    """Fetch total P&L per source tag from current positions.

    Uses /api/sdk/positions which includes both realized and unrealized P&L.
    Returns dict[source_tag] = total_pnl (float).
    """
    pnl_by_source = {}
    for venue in ("polymarket", "kalshi", "simmer"):
        try:
            resp = client._request("GET", "/api/sdk/positions", params={
                "venue": venue
            })
            positions = resp.get("positions", []) if resp else []
            for pos in positions:
                pnl = float(pos.get("pnl", 0) or 0)
                # sources is a list (e.g. ["sdk:divergence"]); split P&L
                # evenly across sources to avoid double-counting
                sources = pos.get("sources", [])
                if sources:
                    share = pnl / len(sources)
                    for src in sources:
                        pnl_by_source[src] = pnl_by_source.get(src, 0.0) + share
                else:
                    pnl_by_source["unknown"] = pnl_by_source.get("unknown", 0.0) + pnl
        except Exception:
            pass
    return pnl_by_source


def fetch_unrealized(client):
    """Fetch unrealized P&L from portfolio endpoint."""
    try:
        portfolio = client.get_portfolio()
        if not portfolio:
            return 0.0, {}
        by_source = portfolio.get("by_source", {})
        total_exposure = float(portfolio.get("total_exposure", 0))
        return total_exposure, by_source
    except Exception:
        return 0.0, {}


# =============================================================================
# Survival tier computation
# =============================================================================

TIER_EMOJIS = {
    "thriving": "\U0001f7e2",  # green circle
    "normal": "\U0001f535",    # blue circle
    "conserving": "\U0001f7e1",  # yellow circle
    "critical": "\U0001f534",  # red circle
    "dead": "\U0001f480",      # skull
}


def compute_tier(state):
    """Compute survival tier from budget state.

    Returns tier string: thriving, normal, conserving, critical, dead.
    """
    budget = state["budget_usd"]
    if budget <= 0:
        return "dead"

    spent = state["spent_usd"]
    pnl = state["total_pnl"]
    budget_remaining_pct = (budget - spent + pnl) / budget

    started = datetime.fromisoformat(state["started_at"])
    now = datetime.now(timezone.utc)
    # Handle naive timestamps
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    days_elapsed = (now - started).total_seconds() / 86400
    horizon = state["horizon_days"]

    if budget_remaining_pct <= 0 or days_elapsed >= horizon:
        return "dead"
    if budget_remaining_pct < 0.10:
        return "critical"
    if budget_remaining_pct < 0.30:
        return "conserving"
    if pnl > 0 and budget_remaining_pct > 0.70:
        return "thriving"
    return "normal"


def tier_max_skills(tier, max_concurrent):
    """Max skills allowed per cycle for this tier."""
    if tier in ("thriving", "normal"):
        return max_concurrent
    if tier in ("conserving", "critical"):
        return 1
    return 0  # dead


def tier_effective_epsilon(tier, epsilon):
    """Effective exploration rate for this tier."""
    if tier in ("thriving", "normal"):
        return epsilon
    if tier == "conserving":
        return epsilon * 0.5
    return 0.0  # critical, dead — pure exploit or no runs


# =============================================================================
# Epsilon-greedy bandit
# =============================================================================

def select_skills(state, n):
    """Select up to n skills using epsilon-greedy bandit.

    Unplayed skills (times_selected=0) are always explored first.
    Returns list of skill slugs.
    """
    skills = state.get("skills", {})
    enabled = {k: v for k, v in skills.items() if v.get("enabled", True)}

    if not enabled:
        return []

    tier = state.get("tier", "normal")
    epsilon = tier_effective_epsilon(tier, state.get("epsilon", 0.2))

    slugs = list(enabled.keys())
    n = min(n, len(slugs))

    # Unplayed skills get priority
    unplayed = [s for s in slugs if enabled[s].get("times_selected", 0) == 0]
    if unplayed:
        selected = random.sample(unplayed, min(n, len(unplayed)))
        if len(selected) < n:
            remaining = [s for s in slugs if s not in selected]
            selected.extend(random.sample(remaining, min(n - len(selected), len(remaining))))
        return selected[:n]

    # Critical tier: always pick best
    if tier == "critical":
        ranked = sorted(slugs, key=lambda s: _avg_reward(enabled[s]), reverse=True)
        return ranked[:n]

    # Epsilon-greedy
    selected = []
    available = slugs[:]
    for _ in range(n):
        if not available:
            break
        if random.random() < epsilon:
            # Explore: random
            pick = random.choice(available)
        else:
            # Exploit: best avg reward
            pick = max(available, key=lambda s: _avg_reward(enabled[s]))
        selected.append(pick)
        available.remove(pick)

    return selected


def _avg_reward(skill_entry):
    """Average reward for a skill. Unplayed = infinity."""
    times = skill_entry.get("times_selected", 0)
    if times == 0:
        return float("inf")
    return skill_entry.get("total_pnl", 0.0) / times


# =============================================================================
# Skill execution
# =============================================================================

def _parse_skill_report(stdout):
    """Extract structured report from skill stdout.

    Skills emit a JSON line like: {"automaton": {"signals": 3, ...}}
    Returns the inner dict or None if not found.
    """
    if not stdout:
        return None
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith('{"automaton"'):
            try:
                parsed = json.loads(line)
                return parsed.get("automaton")
            except (json.JSONDecodeError, AttributeError):
                pass
    return None


# Skip reason categorization — maps skill-emitted flat strings to categories.
# Skills keep their simple contract; automaton owns the intelligence.
SKIP_CATEGORY_PATTERNS = {
    # (prefix/substring, category)
    "safeguard": "safeguard",
    "flip-flop": "safeguard",
    "slippage": "safeguard",
    "market already resolved": "safeguard",
    "resolves in": "safeguard",
    "time decay": "safeguard",
    "price at extreme": "price_extreme",
    "split too narrow": "price_extreme",
    "budget exhausted": "budget",  # covers "daily budget exhausted" too
    "budget too small": "budget",
    "position too small": "budget",
    "max trades reached": "max_trades",
    "unclear signal": "signal_quality",
    "low volume": "signal_quality",
    "market already priced in": "signal_quality",
    "fees eat the edge": "signal_quality",
    "projection unreliable": "signal_quality",
    "cooldown": "cooldown",
    "already holding": "other",
    "fee market": "other",
    "conflicts skipped": "other",
    "cluster too expensive": "other",
    "copytrading failed": "other",
}


def categorize_skip_reasons(skip_reason_str):
    """Parse a comma-separated skip_reason string into categorized counts.

    Returns dict like {"safeguard": 3, "budget": 1, "other": 2}.
    Unrecognized reasons go to "other".
    """
    if not skip_reason_str:
        return {}
    counts = {}
    for reason in skip_reason_str.split(","):
        reason = reason.strip().lower()
        if not reason:
            continue
        category = "other"
        for pattern, cat in SKIP_CATEGORY_PATTERNS.items():
            if pattern in reason:
                category = cat
                break
        counts[category] = counts.get(category, 0) + 1
    return counts


def generate_tuning_hints(state):
    """Analyze skill state and generate actionable tuning hints.

    Returns list of hint dicts for the Clawbot LLM to reason about.
    Pure pattern detection — no LLM calls, no external API calls.
    """
    hints = []
    skills = state.get("skills", {})
    budget = state.get("budget_usd", 100)

    for slug, sk in skills.items():
        if not sk.get("enabled", True):
            continue

        runs = sk.get("times_selected", 0)
        if runs == 0:
            continue

        # 1. Zero signals streak — skill can't find opportunities
        zeros = sk.get("consecutive_zero_signals", 0)
        if zeros >= 5:
            hints.append({
                "skill": slug,
                "issue": "zero_signals_streak",
                "cycles": zeros,
                "suggestion": f"0 signals for {zeros} cycles — loosen thresholds or widen time windows",
            })

        # 2. Concentrated loss — single skill dominates losses
        pnl = sk.get("total_pnl", 0)
        if pnl < 0 and budget > 0:
            loss_pct = abs(pnl) / budget * 100
            if loss_pct > 20:
                hints.append({
                    "skill": slug,
                    "issue": "concentrated_loss",
                    "pnl": round(pnl, 2),
                    "pct_of_budget": round(loss_pct, 1),
                    "suggestion": f"Lost ${abs(pnl):.2f} ({loss_pct:.0f}% of budget) — consider disabling or reducing max bet",
                })

        # 3. Inert — finds signals but never executes
        sig_total = sk.get("signals_found_total", 0)
        exe_total = sk.get("trades_executed_total", 0)
        if sig_total > 50 and exe_total == 0:
            hints.append({
                "skill": slug,
                "issue": "inert",
                "signals": sig_total,
                "suggestion": f"{sig_total} signals found, 0 executed — execution thresholds likely too tight",
            })

        # 4. Win rate collapse — was working, now failing
        rewarded = sk.get("times_rewarded", 0)
        if runs >= 10:
            win_rate = rewarded / runs
            if win_rate < 0.20:
                hints.append({
                    "skill": slug,
                    "issue": "win_rate_collapse",
                    "win_rate": round(win_rate * 100, 1),
                    "runs": runs,
                    "suggestion": f"Win rate {win_rate:.0%} over {runs} cycles — strategy may not suit current markets",
                })

        # 5. Safeguard dominant — most skips are safeguards (uses P2j skip_counts)
        last = sk.get("last_cycle") or {}
        skip_counts = last.get("skip_counts", {})
        total_skips = sum(skip_counts.values())
        safeguard_skips = skip_counts.get("safeguard", 0)
        if total_skips >= 3 and safeguard_skips / total_skips > 0.8:
            hints.append({
                "skill": slug,
                "issue": "safeguard_dominant",
                "safeguard_pct": round(safeguard_skips / total_skips * 100),
                "suggestion": "Most skips are safeguard blocks — markets may be too volatile or near resolution",
            })

    return hints


def run_skill(slug, entrypoint, skill_dir, live=False, state=None):
    """Run a skill as a subprocess.

    Returns {success, returncode, output, stderr, report}.
    report is the parsed {"automaton": ...} dict or None.
    """
    cmd = [sys.executable, entrypoint]
    if live:
        cmd.append("--live")
    cmd.append("--quiet")

    env = os.environ.copy()
    env["AUTOMATON_MANAGED"] = "1"

    # Pass per-skill budget cap based on allocation and survival tier
    if state and slug:
        skill_state = state.get("skills", {}).get(slug, {})
        allocated = skill_state.get("allocated_budget", 0)
        spent = skill_state.get("spent_usd", 0)
        remaining = max(0, allocated - spent)
        tier = state.get("tier", "normal")
        tier_scale = {"thriving": 1.0, "normal": 0.8, "conserving": 0.5, "critical": 0.25, "dead": 0}
        max_bet = max(0.50, remaining * tier_scale.get(tier, 0.5))
        env["AUTOMATON_MAX_BET"] = str(round(max_bet, 2))

    try:
        result = subprocess.run(
            cmd,
            cwd=skill_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = result.stdout[-2000:] if result.stdout else ""
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "output": stdout,
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "report": _parse_skill_report(stdout),
        }
    except subprocess.TimeoutExpired:
        ts_err = datetime.now(timezone.utc).strftime("%H:%M:%S")
        return {
            "success": False,
            "returncode": -1,
            "output": "",
            "stderr": f"[{ts_err}] Timeout: {slug} exceeded 120s",
            "report": None,
        }
    except Exception as e:
        ts_err = datetime.now(timezone.utc).strftime("%H:%M:%S")
        return {
            "success": False,
            "returncode": -1,
            "output": "",
            "stderr": f"[{ts_err}] {e}",
            "report": None,
        }


# =============================================================================
# Bandit update
# =============================================================================

def update_bandit(state, slug, pnl_by_source):
    """Update bandit stats using cross-cycle P&L deltas.

    Stores total_pnl snapshot each cycle. Reward = delta since last run.
    This captures value accrued between selections (trades resolving,
    price moves) without the within-cycle timing problem.
    """
    if slug not in state["skills"]:
        return

    skill = state["skills"][slug]
    source_tag = skill.get("source_tag", "")
    current_pnl = pnl_by_source.get(source_tag, 0.0)
    prev_pnl = skill.get("total_pnl", 0.0)
    delta = current_pnl - prev_pnl

    skill["total_pnl"] = current_pnl
    skill["times_selected"] = skill.get("times_selected", 0) + 1
    # Reward based on delta (positive change since last run), not absolute P&L
    if delta > 0:
        skill["times_rewarded"] = skill.get("times_rewarded", 0) + 1
    skill["last_run"] = datetime.now(timezone.utc).isoformat()


# =============================================================================
# Main cycle
# =============================================================================

def run_cycle(config, live=False, quiet=False):
    """Run one automaton cycle.

    Steps:
    1. Load or init state
    2. Discover skills
    3. Init client (live only)
    4. Compute survival tier
    5. Check for death
    6. Select skills via bandit
    7. Run selected skills
    8. Store skill reports
    9. Fetch P&L from positions and update bandit weights
    10. Save state
    """
    cycle_start = datetime.now(timezone.utc)
    ts = cycle_start.strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. Load or init state
    state = load_state()
    if state is None:
        budget = config["budget_usd"]
        days = config["horizon_days"]
        state = init_state(budget, days)
        if not quiet:
            print(f"\U0001f9ec Automaton initialized: ${budget:.2f} budget, {days} day horizon")
    else:
        # Apply CLI overrides to existing state
        if config.get("_budget_override"):
            state["budget_usd"] = config["budget_usd"]
        if config.get("_days_override"):
            state["horizon_days"] = config["horizon_days"]

    # 2. Discover skills
    discovered = discover_skills()
    if not quiet:
        print(f"\U0001f50d Discovered {len(discovered)} managed skills")

    # Sync discovered skills into state
    for sk in discovered:
        slug = sk["slug"]
        if slug not in state["skills"]:
            state["skills"][slug] = {
                "slug": slug,
                "source_tag": sk["source_tag"],
                "entrypoint": sk["entrypoint"],
                "total_pnl": 0.0,
                "times_selected": 0,
                "times_rewarded": 0,
                "last_run": None,
                "enabled": True,
                "signals_found_total": 0,
                "trades_attempted_total": 0,
                "trades_executed_total": 0,
                "errors_total": 0,
                "last_cycle": None,
                "allocated_budget": 0.0,
                "spent_usd": 0.0,
            }
        else:
            # Update entrypoint/source_tag in case they changed
            state["skills"][slug]["source_tag"] = sk["source_tag"]
            state["skills"][slug]["entrypoint"] = sk["entrypoint"]

    # Mark skills no longer discovered as disabled
    discovered_slugs = {sk["slug"] for sk in discovered}
    for slug in state["skills"]:
        if slug not in discovered_slugs:
            state["skills"][slug]["enabled"] = False

    # Allocate per-skill budgets (equal split among enabled skills)
    enabled_skills = [s for s in state["skills"].values() if s.get("enabled")]
    if enabled_skills:
        per_skill = state["budget_usd"] / len(enabled_skills)
        for s in enabled_skills:
            s["allocated_budget"] = per_skill

    if not state["skills"]:
        print("\U0001f6ab No managed skills found. Add automaton metadata to your skills' SKILL.md files.")
        save_state(state)
        return

    # 3. Refresh P&L from API before computing tier
    # Always fetch when live or on paper venue (simmer) — $SIM trades produce real P&L
    track_pnl = live or _is_paper_venue()
    if track_pnl:
        client = get_client(live=live)

    # 4. Compute survival tier
    old_tier = state.get("tier", "normal")
    new_tier = compute_tier(state)
    state["tier"] = new_tier

    if new_tier != old_tier:
        state["tier_history"].append({
            "from": old_tier,
            "to": new_tier,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if not quiet:
            emoji = TIER_EMOJIS.get(new_tier, "")
            print(f"{emoji} Tier transition: {old_tier} -> {new_tier}")

    # 5. Check for death
    if new_tier == "dead":
        print(f"\n[{ts}]")
        print(f"\U0001f480 DEAD. Budget depleted or horizon expired.")
        print(f"   Cycles completed: {state['cycle_count']}")
        print(f"   Total P&L: ${state['total_pnl']:+.2f}")
        save_state(state)
        return

    if not quiet:
        emoji = TIER_EMOJIS.get(new_tier, "")
        print(f"\n[{ts}]")
        print(f"{emoji} Tier: {new_tier} | Cycle #{state['cycle_count'] + 1}")

    # 6. Select skills via bandit
    max_n = tier_max_skills(new_tier, config["max_concurrent"])
    selected = select_skills(state, max_n)

    if not quiet:
        print(f"\U0001f3b0 Selected: {', '.join(selected) if selected else 'none'}")

    if not live and not _is_paper_venue() and not quiet:
        print(f"\n[DRY RUN] Simulating without trades. Use --live for real trades or TRADING_VENUE=simmer for paper trading.")

    # 7. Run selected skills
    skill_dir_map = {sk["slug"]: sk["dir"] for sk in discovered}
    run_results = {}
    for slug in selected:
        skill_entry = state["skills"].get(slug)
        if not skill_entry:
            continue

        entrypoint = skill_entry["entrypoint"]
        skill_dir = skill_dir_map.get(slug)
        if not skill_dir:
            continue

        if not quiet:
            print(f"\U0001f3c3 Running {slug}...")

        # Paper venue ($SIM) runs skills in live mode — trades are the paper trading mechanism
        effective_live = live or _is_paper_venue()
        result = run_skill(slug, entrypoint, skill_dir, live=effective_live, state=state)
        run_results[slug] = result

        if not quiet:
            status = "\u2705" if result["success"] else "\u274c"
            print(f"   {status} {slug} (exit {result['returncode']})")
            if result["stderr"] and not result["success"]:
                first_line = result["stderr"].strip().split("\n")[0]
                print(f"   {first_line}")

    # 8. Store skill reports in state
    for slug in selected:
        res = run_results.get(slug)
        if not res:
            continue
        skill_entry = state["skills"].get(slug)
        if not skill_entry:
            continue
        report = res.get("report")
        if report:
            skip_reason = report.get("skip_reason")
            cycle_data = {
                "signals": report.get("signals", 0),
                "trades_attempted": report.get("trades_attempted", 0),
                "trades_executed": report.get("trades_executed", 0),
                "skip_reason": skip_reason,
                "skip_counts": categorize_skip_reasons(skip_reason),
                "execution_errors": report.get("execution_errors", []),
                "error": report.get("error"),
            }
            skill_entry["last_cycle"] = cycle_data
            skill_entry["signals_found_total"] = skill_entry.get("signals_found_total", 0) + cycle_data["signals"]
            skill_entry["trades_attempted_total"] = skill_entry.get("trades_attempted_total", 0) + cycle_data["trades_attempted"]
            skill_entry["trades_executed_total"] = skill_entry.get("trades_executed_total", 0) + cycle_data["trades_executed"]
            # Track per-skill spending from reports
            if report.get("amount_usd"):
                skill_entry["spent_usd"] = skill_entry.get("spent_usd", 0) + report["amount_usd"]
            if cycle_data.get("error"):
                skill_entry["errors_total"] = skill_entry.get("errors_total", 0) + 1
            # Track consecutive zero-signal cycles
            if cycle_data["signals"] == 0:
                skill_entry["consecutive_zero_signals"] = skill_entry.get("consecutive_zero_signals", 0) + 1
            else:
                skill_entry["consecutive_zero_signals"] = 0
        else:
            skill_entry["last_cycle"] = None

    # 8b. Circuit breakers — auto-disable skills on loss streak or rapid bleed
    for slug in selected:
        skill_entry = state["skills"].get(slug)
        if not skill_entry or not skill_entry.get("enabled"):
            continue
        report = run_results.get(slug, {}).get("report") or {}
        amount = report.get("amount_usd", 0)

        # Rapid bleed: single cycle spent > 10% of total budget
        if amount > 0 and amount > state["budget_usd"] * 0.10:
            skill_entry["enabled"] = False
            skill_entry["disabled_reason"] = "rapid_bleed"
            # +1 because cycle_count is incremented in step 10 after this
            skill_entry["disabled_at_cycle"] = state["cycle_count"] + 1
            if not quiet:
                print(f"  🛑 Circuit breaker: {slug} disabled (rapid bleed: ${amount:.2f} in one cycle)")

    # 8c. Check cooldowns — re-enable skills after cooldown expires (only in normal/thriving tiers)
    tier = state.get("tier", "normal")
    if tier in ("normal", "thriving"):
        for slug, sk in state["skills"].items():
            if not sk.get("enabled") and sk.get("disabled_reason") == "loss_streak":
                cooldown_cycles = 10
                disabled_at = sk.get("disabled_at_cycle", 0)
                if state["cycle_count"] - disabled_at >= cooldown_cycles:
                    sk["enabled"] = True
                    sk.pop("disabled_reason", None)
                    sk.pop("disabled_at_cycle", None)
                    sk["consecutive_negative_pnl"] = 0
                    if not quiet:
                        print(f"  🔄 Re-enabled {slug} after cooldown")

    # 9. Fetch P&L from positions and update bandit
    if track_pnl:
        pnl_by_source = fetch_pnl_by_source(client)
        state["total_pnl"] = sum(pnl_by_source.values())

        for slug in selected:
            if not run_results.get(slug, {}).get("success"):
                continue
            skill_entry = state["skills"].get(slug)
            if not skill_entry:
                continue
            # Track P&L delta for loss streak circuit breaker
            prev_pnl = skill_entry.get("prev_cycle_pnl", 0)
            source_tag = skill_entry.get("source_tag", "")
            current_pnl = pnl_by_source.get(source_tag, 0)
            delta = current_pnl - prev_pnl
            skill_entry["prev_cycle_pnl"] = current_pnl

            if delta < 0:
                skill_entry["consecutive_negative_pnl"] = skill_entry.get("consecutive_negative_pnl", 0) + 1
                # Loss streak: 3 consecutive negative P&L cycles → disable with cooldown
                if skill_entry["consecutive_negative_pnl"] >= 3 and skill_entry.get("enabled"):
                    skill_entry["enabled"] = False
                    skill_entry["disabled_reason"] = "loss_streak"
                    # +1 because cycle_count is incremented in step 10 after this
                    skill_entry["disabled_at_cycle"] = state["cycle_count"] + 1
                    if not quiet:
                        print(f"  🛑 Circuit breaker: {slug} disabled (3 consecutive losses)")
            elif delta >= 0:
                skill_entry["consecutive_negative_pnl"] = 0

            update_bandit(state, slug, pnl_by_source)

    # Decay epsilon
    state["epsilon"] = max(
        config["min_epsilon"],
        state.get("epsilon", config["epsilon"]) * config["epsilon_decay"],
    )

    # 10. Save state
    state["cycle_count"] += 1
    save_state(state)

    # 10b. Write cycle journal
    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    journal_entry = {
        "cycle": state["cycle_count"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tier": state.get("tier", "normal"),
        "epsilon": round(state.get("epsilon", 0), 4),
        "budget_remaining_pct": round(
            (state["budget_usd"] - sum(s.get("spent_usd", 0) for s in state["skills"].values()))
            / max(state["budget_usd"], 0.01), 3
        ),
        "selected_skills": list(selected),
        "results": {},
        "pnl_total": round(state.get("total_pnl", 0), 2),
        "spent_total": round(sum(s.get("spent_usd", 0) for s in state["skills"].values()), 2),
        "runtime_sec": round(elapsed, 1),
        "circuit_breakers": [],
        "tuning_hints": [],  # populated below
    }
    # Record any circuit breaker events
    for slug, sk in state["skills"].items():
        if sk.get("disabled_reason") and sk.get("disabled_at_cycle") == state["cycle_count"]:
            journal_entry["circuit_breakers"].append({
                "skill": slug,
                "reason": sk["disabled_reason"],
            })
    for slug in selected:
        res = run_results.get(slug, {})
        report = res.get("report", {}) or {}
        skip_reason = report.get("skip_reason")
        journal_entry["results"][slug] = {
            "signals": report.get("signals", 0),
            "trades_attempted": report.get("trades_attempted", 0),
            "trades_executed": report.get("trades_executed", 0),
            "amount_usd": report.get("amount_usd", 0),
            "skip_reason": skip_reason,
            "skip_counts": categorize_skip_reasons(skip_reason),
            "execution_errors": report.get("execution_errors", []),
            "exit_code": res.get("returncode", -1),
            "success": res.get("success", False),
        }
    # Generate tuning hints once (used in journal + summary)
    hints = generate_tuning_hints(state)
    journal_entry["tuning_hints"] = hints
    write_journal_entry(journal_entry)

    # 11. Print cycle summary
    if not quiet and selected:
        cycle_num = state["cycle_count"]
        print(f"\n\u2500\u2500 Cycle #{cycle_num} Summary \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        for slug in selected:
            res = run_results.get(slug, {})
            ok = "\u2713" if res.get("success") else "\u2717"
            report = res.get("report")
            if report:
                sig = report.get("signals", 0)
                att = report.get("trades_attempted", 0)
                exe = report.get("trades_executed", 0)
                skip = report.get("skip_reason")
                detail = f"signals={sig}  attempted={att}  executed={exe}"
                if skip:
                    cats = categorize_skip_reasons(skip)
                    if cats:
                        cat_str = " ".join(f"{k}={v}" for k, v in sorted(cats.items()))
                        detail += f"  skips:[{cat_str}]"
                    else:
                        detail += f"  ({skip})"
                exec_errs = report.get("execution_errors", [])
                if exec_errs:
                    detail += f"  errors:[{'; '.join(exec_errs[:3])}]"
                print(f"  {slug:<28} {detail}  {ok}")
            else:
                print(f"  {slug:<28} (no report){'':>24}  {ok}")
        total_pnl = sum(s.get("total_pnl", 0) for s in state["skills"].values())
        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        print(f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        print(f"\U0001f4be P&L: ${total_pnl:+.2f} | Epsilon: {state['epsilon']:.3f} | {elapsed:.1f}s")
        if hints:
            print(f"\n  Tuning hints ({len(hints)}):")
            for h in hints:
                print(f"    [{h['issue']}] {h['skill']}: {h['suggestion']}")
    elif not quiet:
        total_pnl = sum(s.get("total_pnl", 0) for s in state["skills"].values())
        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        print(f"\U0001f4be Cycle complete. Total P&L: ${total_pnl:+.2f} | Epsilon: {state['epsilon']:.3f} | {elapsed:.1f}s")


# =============================================================================
# Display functions
# =============================================================================

def show_status(state=None):
    """Display survival stats. Fetches live unrealized P&L from API."""
    state = state or load_state()
    if not state:
        print("No state found. Run the automaton first.")
        return

    # Fetch fresh unrealized P&L on demand (not during run_cycle to avoid rate limits)
    try:
        client = get_client()
        unrealized, _ = fetch_unrealized(client)
        state["unrealized_pnl"] = unrealized
        save_state(state)
    except Exception:
        pass  # Use stale value if API unavailable

    tier = state.get("tier", "unknown")
    emoji = TIER_EMOJIS.get(tier, "")
    budget = state["budget_usd"]
    spent = state["spent_usd"]
    pnl = state["total_pnl"]
    unrealized = state.get("unrealized_pnl", 0)
    remaining = budget - spent + pnl
    remaining_pct = (remaining / budget * 100) if budget > 0 else 0

    started = datetime.fromisoformat(state["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days_elapsed = (now - started).total_seconds() / 86400
    days_left = max(0, state["horizon_days"] - days_elapsed)

    print(f"\n{'='*50}")
    print(f"  {emoji} SIMMER AUTOMATON — {tier.upper()}")
    print(f"{'='*50}")
    print(f"  Budget:       ${budget:.2f}")
    print(f"  Spent:        ${spent:.2f}")
    print(f"  Total P&L: ${pnl:+.2f}")
    print(f"  Unrealized:   ${unrealized:+.2f}")
    print(f"  Remaining:    ${remaining:.2f} ({remaining_pct:.0f}%)")
    print(f"  Days left:    {days_left:.1f} / {state['horizon_days']}")
    print(f"  Cycles:       {state['cycle_count']}")
    print(f"  Epsilon:      {state.get('epsilon', 0):.3f}")
    print(f"  Skills:       {sum(1 for s in state['skills'].values() if s.get('enabled'))}"
          f" active / {len(state['skills'])} total")

    if state.get("tier_history"):
        print(f"\n  Recent tier transitions:")
        for t in state["tier_history"][-5:]:
            print(f"    {t['from']} -> {t['to']} at {t['at'][:16]}")

    print(f"{'='*50}\n")


def show_skills(state=None):
    """Display discovered skills with bandit stats."""
    state = state or load_state()
    if not state:
        print("No state found. Run the automaton first.")
        return

    skills = state.get("skills", {})
    if not skills:
        print("No skills tracked yet.")
        return

    print(f"\n{'='*85}")
    print(f"  SKILL PERFORMANCE")
    print(f"{'='*85}")
    print(f"  {'Skill':<28} {'P&L':>8} {'Runs':>5} {'Win%':>5} {'Sig':>5} {'Att':>5} {'Exe':>5} {'Err':>4} {'Status':<8}")
    print(f"  {'-'*28} {'-'*8} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*8}")

    for slug, sk in sorted(skills.items()):
        pnl = sk.get("total_pnl", 0)
        runs = sk.get("times_selected", 0)
        wins = sk.get("times_rewarded", 0)
        win_pct = (wins / runs * 100) if runs > 0 else 0
        sig = sk.get("signals_found_total", 0)
        att = sk.get("trades_attempted_total", 0)
        exe = sk.get("trades_executed_total", 0)
        err = sk.get("errors_total", 0)
        status = "active" if sk.get("enabled") else "disabled"

        print(f"  {slug:<28} ${pnl:>+7.2f} {runs:>5} {win_pct:>4.0f}% {sig:>5} {att:>5} {exe:>5} {err:>4} {status:<8}")

    print(f"{'='*85}\n")


def show_config():
    """Display current configuration."""
    print(f"\n{'='*40}")
    print(f"  AUTOMATON CONFIG")
    print(f"{'='*40}")
    for key, spec in CONFIG_SCHEMA.items():
        val = _config.get(key, spec.get("default"))
        env = spec.get("env", "")
        print(f"  {key:<20} = {val:<12} ({env})")
    print(f"{'='*40}\n")


def show_journal(n=20):
    """Display last N cycle journal entries."""
    entries = read_journal(n)
    if not entries:
        print("No journal entries. Run a cycle first.")
        return

    print(f"\n📓 Cycle Journal (last {len(entries)} entries)")
    print("=" * 70)
    for e in entries:
        cycle = e.get("cycle", "?")
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        tier = e.get("tier", "?")
        eps = e.get("epsilon", 0)
        pnl = e.get("pnl_total", 0)
        spent = e.get("spent_total", 0)
        runtime = e.get("runtime_sec", 0)
        selected = e.get("selected_skills", [])

        print(f"\n  Cycle #{cycle}  {ts} UTC  [{tier}]  ε={eps:.3f}  {runtime:.1f}s")
        for slug in selected:
            r = e.get("results", {}).get(slug, {})
            sig = r.get("signals", 0)
            exe = r.get("trades_executed", 0)
            amt = r.get("amount_usd", 0)
            skip = r.get("skip_reason")
            skip_cats = r.get("skip_counts") or categorize_skip_reasons(skip)
            ok = "✓" if r.get("success") else "✗"
            line = f"    {slug:<28} sig={sig}  exe={exe}  ${amt:.2f}  {ok}"
            if skip_cats:
                cat_str = " ".join(f"{k}={v}" for k, v in sorted(skip_cats.items()))
                line += f"  skips:[{cat_str}]"
            elif skip:
                line += f"  ({skip})"
            exec_errs = r.get("execution_errors", [])
            if exec_errs:
                line += f"  errors:[{'; '.join(exec_errs[:3])}]"
            print(line)
        print(f"    P&L: ${pnl:+.2f}  |  Spent: ${spent:.2f}")
        hints = e.get("tuning_hints", [])
        if hints:
            for h in hints:
                print(f"    hint: [{h['issue']}] {h['skill']}: {h['suggestion']}")
    print()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simmer Automaton - Survival Meta-Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--budget", type=float, help="Trading budget in USD")
    parser.add_argument("--days", type=int, help="Horizon in days")
    parser.add_argument("--live", action="store_true", help="Execute real trades")
    parser.add_argument("--quiet", action="store_true", help="Minimal output (cron mode)")
    parser.add_argument("--status", action="store_true", help="Show survival stats")
    parser.add_argument("--skills", action="store_true", help="Show skill performance")
    parser.add_argument("--journal", type=int, nargs="?", const=20, metavar="N", help="Show last N cycle journal entries (default: 20)")
    parser.add_argument("--config", action="store_true", help="Show configuration")
    parser.add_argument("--set", metavar="KEY=VALUE", help="Update a config value")
    parser.add_argument("--reset", action="store_true", help="Reset state (fresh start)")

    args = parser.parse_args()

    # Display commands (no client needed)
    if args.status:
        show_status()
        return

    if args.skills:
        show_skills()
        return

    if args.journal is not None:
        show_journal(args.journal)
        return

    if args.config:
        show_config()
        return

    if args.set:
        if "=" not in args.set:
            print("Error: --set requires KEY=VALUE format")
            sys.exit(1)
        key, _, val = args.set.partition("=")
        key = key.strip()
        if key not in CONFIG_SCHEMA:
            print(f"Error: unknown config key '{key}'. Valid: {', '.join(CONFIG_SCHEMA.keys())}")
            sys.exit(1)
        type_fn = CONFIG_SCHEMA[key].get("type", str)
        try:
            typed_val = type_fn(val)
        except (ValueError, TypeError):
            print(f"Error: invalid value '{val}' for {key} (expected {type_fn.__name__})")
            sys.exit(1)
        _update_config({key: typed_val}, __file__)
        print(f"Updated {key} = {typed_val}")
        return

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            for jf in [JOURNAL_FILE, JOURNAL_PREV_FILE]:
                if jf.exists():
                    jf.unlink()
            print("State and journal reset. Fresh start on next run.")
        else:
            print("No state file to reset.")
        return

    # Build config with CLI overrides
    config = dict(_config)
    if args.budget is not None:
        config["budget_usd"] = args.budget
        config["_budget_override"] = True
    if args.days is not None:
        config["horizon_days"] = args.days
        config["_days_override"] = True

    # Run cycle
    run_cycle(config, live=args.live, quiet=args.quiet)


if __name__ == "__main__":
    main()

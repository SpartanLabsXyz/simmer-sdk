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
    """Lazy-init SimmerClient singleton."""
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
        _client = SimmerClient(api_key=api_key, venue="polymarket", live=live)
    return _client


# =============================================================================
# State persistence
# =============================================================================

STATE_FILE = Path(__file__).parent / "state.json"


def load_state(path=None):
    """Load state from disk. Returns None if no state exists."""
    p = Path(path) if path else STATE_FILE
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_state(state, path=None):
    """Save state to disk."""
    p = Path(path) if path else STATE_FILE
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


def init_state(budget, days):
    """Create fresh state."""
    return {
        "version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "budget_usd": budget,
        "horizon_days": days,
        "spent_usd": 0.0,
        "realized_pnl": 0.0,
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
        source_tag = _find_source_tag(child) or f"sdk:{child.name}"

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

def fetch_skill_pnl(client, skills):
    """Fetch realized P&L per source tag from trade history.

    Queries all venues and merges by source tag.
    Returns dict[source_tag] = net_pnl (float).
    """
    all_trades = []
    for venue in ("polymarket", "kalshi"):
        try:
            resp = client._request("GET", "/api/sdk/trades", params={
                "venue": venue, "limit": 200
            })
            trades = resp.get("trades", []) if resp else []
            all_trades.extend(trades)
        except Exception:
            pass

    # Group by source, compute net P&L: sells - buys
    pnl_by_source = {}
    for trade in all_trades:
        source = trade.get("source", "unknown")
        side = trade.get("side", "").lower()
        cost = float(trade.get("cost", 0) or trade.get("amount", 0) or 0)
        if side == "sell":
            pnl_by_source[source] = pnl_by_source.get(source, 0.0) + cost
        elif side == "buy":
            pnl_by_source[source] = pnl_by_source.get(source, 0.0) - cost

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
    realized = state["realized_pnl"]
    budget_remaining_pct = (budget - spent + realized) / budget

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
    if realized > 0 and budget_remaining_pct > 0.70:
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

def run_skill(slug, entrypoint, skill_dir, live=False):
    """Run a skill as a subprocess.

    Returns {success, returncode, output, stderr}.
    """
    cmd = [sys.executable, entrypoint]
    if live:
        cmd.append("--live")
    cmd.append("--quiet")

    env = os.environ.copy()
    env["AUTOMATON_MANAGED"] = "1"

    try:
        result = subprocess.run(
            cmd,
            cwd=skill_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "output": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "returncode": -1,
            "output": "",
            "stderr": f"Timeout: {slug} exceeded 120s",
        }
    except Exception as e:
        return {
            "success": False,
            "returncode": -1,
            "output": "",
            "stderr": str(e),
        }


# =============================================================================
# Bandit update
# =============================================================================

def update_bandit(state, slug, pnl_before, pnl_after):
    """Update bandit stats for a skill after execution.

    Only updates if the skill ran successfully.
    """
    if slug not in state["skills"]:
        return

    skill = state["skills"][slug]
    source_tag = skill.get("source_tag", "")
    delta = pnl_after.get(source_tag, 0.0) - pnl_before.get(source_tag, 0.0)

    skill["total_pnl"] = skill.get("total_pnl", 0.0) + delta
    skill["times_selected"] = skill.get("times_selected", 0) + 1
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
    3. Refresh P&L from API (live only)
    4. Compute survival tier
    5. Check for death
    6. Select skills via bandit
    7. Fetch pre-run P&L snapshot
    8. Run selected skills
    9. Fetch post-run P&L
    10. Update bandit weights (successful runs only)
    11. Save state
    """
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

    if not state["skills"]:
        print("\U0001f6ab No managed skills found. Add automaton metadata to your skills' SKILL.md files.")
        save_state(state)
        return

    # 3. Refresh P&L from API (if live) before computing tier
    if live:
        client = get_client()
        pnl_snapshot = fetch_skill_pnl(client, state["skills"])
        unrealized, by_source = fetch_unrealized(client)
        state["unrealized_pnl"] = unrealized
        state["realized_pnl"] = sum(pnl_snapshot.values())

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
        print(f"\U0001f480 DEAD. Budget depleted or horizon expired.")
        print(f"   Cycles completed: {state['cycle_count']}")
        print(f"   Realized P&L: ${state['realized_pnl']:+.2f}")
        save_state(state)
        return

    if not quiet:
        emoji = TIER_EMOJIS.get(new_tier, "")
        print(f"{emoji} Tier: {new_tier} | Cycle #{state['cycle_count'] + 1}")

    # 6. Select skills via bandit
    max_n = tier_max_skills(new_tier, config["max_concurrent"])
    selected = select_skills(state, max_n)

    if not quiet:
        print(f"\U0001f3b0 Selected: {', '.join(selected) if selected else 'none'}")

    if not live:
        if not quiet:
            print(f"\n[DRY RUN] Would run: {', '.join(selected)}")
            print("Add --live to execute trades.")
        state["cycle_count"] += 1
        save_state(state)
        return

    # 7. Fetch pre-run P&L snapshot for bandit delta
    pnl_before = fetch_skill_pnl(client, state["skills"])

    # 8. Run selected skills
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

        result = run_skill(slug, entrypoint, skill_dir, live=True)
        run_results[slug] = result

        if not quiet:
            status = "\u2705" if result["success"] else "\u274c"
            print(f"   {status} {slug} (exit {result['returncode']})")
            if result["stderr"] and not result["success"]:
                first_line = result["stderr"].strip().split("\n")[0]
                print(f"   {first_line}")

    # 9. Fetch post-run P&L
    pnl_after = fetch_skill_pnl(client, state["skills"])

    # 10. Update bandit weights (only for successful runs)
    for slug in selected:
        if not run_results.get(slug, {}).get("success"):
            continue
        skill_entry = state["skills"].get(slug)
        if not skill_entry:
            continue
        update_bandit(state, slug, pnl_before, pnl_after)

    # Decay epsilon
    state["epsilon"] = max(
        config["min_epsilon"],
        state.get("epsilon", config["epsilon"]) * config["epsilon_decay"],
    )

    # 10. Save state
    state["cycle_count"] += 1
    save_state(state)

    if not quiet:
        total_pnl = sum(s.get("total_pnl", 0) for s in state["skills"].values())
        print(f"\U0001f4be Cycle complete. Total P&L: ${total_pnl:+.2f} | Epsilon: {state['epsilon']:.3f}")


# =============================================================================
# Display functions
# =============================================================================

def show_status(state=None):
    """Display survival stats."""
    state = state or load_state()
    if not state:
        print("No state found. Run the automaton first.")
        return

    tier = state.get("tier", "unknown")
    emoji = TIER_EMOJIS.get(tier, "")
    budget = state["budget_usd"]
    spent = state["spent_usd"]
    realized = state["realized_pnl"]
    unrealized = state.get("unrealized_pnl", 0)
    remaining = budget - spent + realized
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
    print(f"  Realized P&L: ${realized:+.2f}")
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

    print(f"\n{'='*70}")
    print(f"  SKILL PERFORMANCE")
    print(f"{'='*70}")
    print(f"  {'Skill':<30} {'P&L':>8} {'Runs':>6} {'Win%':>6} {'Avg':>8} {'Status':<8}")
    print(f"  {'-'*30} {'-'*8} {'-'*6} {'-'*6} {'-'*8} {'-'*8}")

    for slug, sk in sorted(skills.items()):
        pnl = sk.get("total_pnl", 0)
        runs = sk.get("times_selected", 0)
        wins = sk.get("times_rewarded", 0)
        win_pct = (wins / runs * 100) if runs > 0 else 0
        avg = pnl / runs if runs > 0 else 0
        status = "active" if sk.get("enabled") else "disabled"

        print(f"  {slug:<30} ${pnl:>+7.2f} {runs:>6} {win_pct:>5.0f}% ${avg:>+7.2f} {status:<8}")

    print(f"{'='*70}\n")


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
            print("State reset. Fresh start on next run.")
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

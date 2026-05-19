---
name: autoresearch-finalize
description: Finalize an autoresearch session into clean, reviewable branches. Use when asked to "finalize autoresearch", "clean up experiments", or "prepare autoresearch for review".
---

# Finalize Autoresearch

Turn a noisy autoresearch branch into clean, independent branches — one per logical change, each starting from the merge-base.

## Step 1 — Analyze and propose groups

1. Read `autoresearch.jsonl`. Filter to **kept** experiments only.
2. Read `autoresearch.md` for context.
3. Expand all short commit hashes to full hashes: `git rev-parse <short_hash>`
4. Get the merge-base: `git merge-base HEAD main`
5. For each kept commit, get the diff stat.
6. Group kept commits into logical changesets:
   - **Preserve application order.** Group N comes before Group N+1.
   - **No two groups may touch the same file.** Each branch is applied to merge-base independently — overlapping files would conflict. If two groups touch the same file, merge them into one group.
   - **Watch for cross-file dependencies.** If group 1 adds an API in `api.js` and group 2 calls it in `parser.js`, group 2's branch won't work in isolation. Flag dependencies.
   - **Keep each group small and focused.** One idea, one theme per group.

Present the proposed grouping:

```
Proposed branches (each from merge-base, independent):

1. **<description>** (commits abc1234, def5678)
   Files: file1.py, file2.py
   Metric: 42.3 → 38.1 (-9.9%)

2. **<description>** (commits ghi9012)
   Files: config.json
   Metric: 38.1 → 31.7 (-16.8%)
```

**Wait for approval before proceeding.**

## Step 2 — Create branches

For each approved group:

```bash
BASE=$(git merge-base HEAD main)
git checkout -b autoresearch/final/<group-name> $BASE
git cherry-pick <commit1> <commit2> ...
```

If cherry-pick conflicts, resolve by taking the experiment's version (the whole point is to preserve the improvement).

## Step 3 — Verify each branch

For each branch:
1. Run the skill command to verify it still works
2. Check that the metric improvement holds
3. If it doesn't, flag it — the improvement may have depended on another group's changes

## Step 4 — Report

```
Finalized branches:
1. autoresearch/final/<name> — <metric improvement>, ready to merge
2. autoresearch/final/<name> — <metric improvement>, depends on branch 1
```

The user can now review and merge each branch independently (or together if there are dependencies).

/**
 * Keyless reads against Simmer's public endpoints.
 *
 * Deliberately separate from `SimmerApi`: that class always sends
 * `Authorization: Bearer <key>`, and here there is no key to send. An agent
 * evaluating Simmer can browse the catalogue and the leaderboard before it
 * has an account — the discovery surface should not require one.
 *
 * Both endpoints below are unauthenticated server-side. If that ever changes,
 * these calls start returning 401 and the tools surface a BackendError rather
 * than failing silently.
 */

import { BackendError } from "./errors.js";

export interface BrowseMarketsParams {
  q?: string;
  limit?: number;
  status?: string;
  tags?: string;
  min_volume?: number;
}

export interface PublicResult {
  [key: string]: unknown;
}

// Measured against production 2026-08-15, warm and cold:
//   /api/markets?limit=2      4.4 – 5.4s
//   /api/markets?limit=200   10.3 – 10.8s
//   /api/leaderboard/all      1.7 –  6.5s
// The 5s default used elsewhere in this package aborts a plain market browse
// outright. Don't lower these without re-measuring — a too-tight timeout here
// reads to the agent as "Simmer is down", not "Simmer is slow".
const MARKETS_TIMEOUT_MS = 20_000;
const LEADERBOARD_TIMEOUT_MS = 15_000;

/**
 * GET + parse, with the abort timer held open through body parsing.
 *
 * Clearing the timer as soon as headers arrive (the pattern in api.ts) leaves
 * a hole: a server that returns 200 and then stalls mid-body hangs the tool
 * forever, because nothing is watching any more. `/api/markets` already takes
 * 4-11s, so a slow body here is not hypothetical.
 *
 * Exported for tests — the timeout constants are module-level, and the abort
 * path is only observable if a caller can pass a short one.
 */
export async function fetchPublicJson(url: string, timeoutMs: number): Promise<PublicResult> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json" },
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const body = (await resp.json()) as Record<string, unknown>;
        if (typeof body.detail === "string") detail = body.detail;
      } catch { /* non-JSON error body */ }
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as PublicResult;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Browse the public market catalogue. GET /api/markets — no auth.
 *
 * This is the discovery view. The authenticated `simmer_get_markets`
 * (GET /api/sdk/markets) returns a richer payload with venue filtering and
 * volume sorting; prefer it when a key is configured.
 */
export async function browseMarkets(
  apiUrl: string,
  params: BrowseMarketsParams = {},
): Promise<PublicResult> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.limit != null) qs.set("limit", String(Math.min(params.limit, 200)));
  if (params.status) qs.set("status", params.status);
  if (params.tags) qs.set("tags", params.tags);
  if (params.min_volume != null) qs.set("min_volume", String(params.min_volume));
  const query = qs.toString();
  return fetchPublicJson(`${apiUrl}/api/markets${query ? `?${query}` : ""}`, MARKETS_TIMEOUT_MS);
}

/**
 * Public leaderboard across all four legs. GET /api/leaderboard/all — no auth.
 * The endpoint fans out server-side; one call beats four.
 */
export async function getLeaderboard(
  apiUrl: string,
  params: { limit?: number } = {},
): Promise<PublicResult> {
  const qs = new URLSearchParams();
  if (params.limit != null) qs.set("limit", String(Math.min(params.limit, 50)));
  const query = qs.toString();
  return fetchPublicJson(`${apiUrl}/api/leaderboard/all${query ? `?${query}` : ""}`, LEADERBOARD_TIMEOUT_MS);
}

const REGISTER_TIMEOUT_MS = 10_000;

export interface RegisterAgentParams {
  name: string;
  description?: string;
  homepage?: string;
  skill_url?: string;
}

export interface RegisterAgentResult {
  agent_id: string;
  api_key: string;
  key_prefix: string;
  claim_url: string;
  claim_code: string;
  status: string;
  starting_balance: number;
  limits: Record<string, unknown>;
}

/**
 * Register a new agent without any prior account.
 * POST /api/sdk/agents/register — no auth.
 *
 * Returns api_key + agent_id + claim_url. The key is returned ONCE; the
 * caller must capture it. Claiming the agent at claim_url links it to a
 * Simmer account for real-venue trading.
 */
export async function registerAgent(
  apiUrl: string,
  params: RegisterAgentParams,
): Promise<RegisterAgentResult> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REGISTER_TIMEOUT_MS);
  try {
    const resp = await fetch(`${apiUrl}/api/sdk/agents/register`, {
      method: "POST",
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const body = (await resp.json()) as Record<string, unknown>;
        if (typeof body.detail === "string") detail = body.detail;
      } catch { /* non-JSON error body */ }
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as RegisterAgentResult;
  } finally {
    clearTimeout(timer);
  }
}

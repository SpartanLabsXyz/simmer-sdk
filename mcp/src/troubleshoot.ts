/**
 * troubleshoot_error — live /api/sdk/troubleshoot + local pattern fallback.
 * Ported from Python simmer-mcp v0.3.0 (simmer_mcp/errors.py).
 */

export interface TroubleshootResult {
  matched: boolean;
  fix: string;
  source?: string;
}

const LOCAL_FALLBACK: Array<{ patterns: string[]; fix: string }> = [
  {
    patterns: ["not enough balance", "insufficient balance", "insufficient funds"],
    fix: "Check USDC.e balance with GET /api/sdk/portfolio. If balance is less than your order size, either deposit more USDC.e or reduce order size. (Allowance errors are a separate failure class and surface as 'allowance'/'approval' — run client.set_approvals() only for those.)",
  },
  {
    patterns: ["rate limit", "too many requests", "429"],
    fix: "You're over the request limit. Use GET /api/sdk/briefing (1 call) instead of separate /context + /positions + /portfolio calls.",
  },
  {
    patterns: ["nonce", "already used", "nonce too low"],
    fix: "Nonce collision from concurrent trades. Serialize your trade calls or add a 2-3 second delay between trades.",
  },
  {
    patterns: ["daily trade limit", "trades/day"],
    fix: "The daily trade limit counts only BUYS across all venues (sim + real) — sells (exits) and redeems are exempt. Free users can self-raise this to 1,000/day (no upgrade needed) via PATCH /api/sdk/user/settings with max_trades_per_day; Pro raises the ceiling to 5,000.",
  },
];

const NO_MATCH_FIX =
  "No known pattern matched. Check the full docs at docs.simmer.markets or fetch docs.simmer.markets/llms-full.txt for the complete reference.";

/**
 * Look up a Simmer API error. Calls the live /api/sdk/troubleshoot endpoint
 * first; falls back to local patterns when the API is unreachable.
 *
 * @param errorText  The error message or response body from a failed Simmer API call.
 * @param apiUrl     Optional API base URL (defaults to production).
 */
export async function troubleshootError(
  errorText: string,
  apiUrl = "https://api.simmer.markets",
): Promise<TroubleshootResult> {
  const url = `${apiUrl}/api/sdk/troubleshoot`;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    let resp: Response;
    try {
      resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ error_text: errorText }),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    if (resp.ok) {
      return (await resp.json()) as TroubleshootResult;
    }
  } catch {
    // Network error or timeout — fall through to local patterns.
  }

  const lower = errorText.toLowerCase();
  for (const entry of LOCAL_FALLBACK) {
    if (entry.patterns.some((p) => lower.includes(p))) {
      return { matched: true, fix: entry.fix, source: "pattern_match" };
    }
  }
  return { matched: false, fix: NO_MATCH_FIX, source: "pattern_match" };
}

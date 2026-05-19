// Simmer-specific — not tracked against upstream

import { BackendError } from "./errors.js";

export interface SkillOutcome {
  trades: number;
  pnl: number;
  wins: number;
  losses: number;
}

export interface BacktestResult {
  trades_total: number;
  trades_included: number;
  trades_excluded: number;
  simulated_pnl: number;
  original_pnl: number;
  win_rate: number;
  improvement_pct: number | null;
}

export interface ResumeState {
  last_experiment_number: number;
  current_segment: number;
  best_metric: number | null;
  best_direction: "lower" | "higher" | null;
  metric_name: string | null;
  metric_unit: string | null;
  skill_slug: string;
}

export interface PostExperimentResult {
  id: string;
  experiment_number: number;
  is_new_best: boolean;
  status_mismatch?: boolean;
  zero_metric_keep?: boolean;
  prior_best_value?: number | null;
  latest_version?: string;
}

export class SimmerApi {
  private apiKey: string;
  private apiUrl: string;
  private mcpVersion: string;

  constructor(apiKey: string, apiUrl: string, mcpVersion: string) {
    this.apiKey = apiKey;
    this.apiUrl = apiUrl;
    this.mcpVersion = mcpVersion;
  }

  private headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
      "x-mcp-version": this.mcpVersion,
    };
  }

  private async timedFetch(url: string, init?: RequestInit, timeoutMs = 5000): Promise<Response> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: ctrl.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  private async extractDetail(resp: Response): Promise<string> {
    try {
      const body = (await resp.json()) as Record<string, unknown>;
      if (typeof body.detail === "string") return body.detail;
    } catch { /* ignore parse errors */ }
    return `HTTP ${resp.status}`;
  }

  /**
   * Lightweight Pro-tier check. Probes /api/sdk/autoresearch/state with a
   * canary slug. Throws BackendError(403) if the user doesn't have Pro.
   * Swallows network errors and non-403 failures — those are transient and
   * should not block local experiment execution.
   */
  async checkPro(): Promise<void> {
    try {
      const resp = await this.timedFetch(
        `${this.apiUrl}/api/sdk/autoresearch/state?skill_slug=__probe__`,
        { headers: this.headers() },
      );
      if (resp.status === 403) {
        const detail = await this.extractDetail(resp);
        throw new BackendError(403, detail, "https://simmer.markets/pro");
      }
      // Any other status (200, 404, 500, etc.) is not a Pro gate — skip.
    } catch (e) {
      if (e instanceof BackendError) throw e;
      // Network failure or timeout — non-blocking; skip the check.
    }
  }

  async getOutcomes(skillSlug: string, since: string): Promise<SkillOutcome | null> {
    try {
      const url = `${this.apiUrl}/api/sdk/outcomes?skill=${encodeURIComponent(skillSlug)}&since=${encodeURIComponent(since)}`;
      const resp = await fetch(url, { headers: this.headers() });
      if (!resp.ok) return null;
      const data = (await resp.json()) as Record<string, unknown>;
      return {
        trades: (data.trades as number) ?? 0,
        pnl: (data.pnl as number) ?? 0,
        wins: (data.wins as number) ?? 0,
        losses: (data.losses as number) ?? 0,
      };
    } catch {
      return null;
    }
  }

  async postExperiment(data: {
    skill_slug: string;
    experiment_number: number;
    segment: number;
    status: "keep" | "discard" | "crash" | "checks_failed";
    metric_name: string;
    metric_value: number | null;
    metric_unit: string;
    best_direction: "lower" | "higher";
    secondary_metrics?: Record<string, number>;
    description?: string;
    commit_hash?: string;
  }): Promise<PostExperimentResult | null> {
    try {
      const resp = await fetch(`${this.apiUrl}/api/sdk/autoresearch/experiments`, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(data),
      });
      if (!resp.ok) return null;
      return (await resp.json()) as PostExperimentResult;
    } catch {
      return null;
    }
  }

  async getResumeState(skillSlug: string): Promise<ResumeState | null> {
    try {
      const resp = await fetch(
        `${this.apiUrl}/api/sdk/autoresearch/state?skill_slug=${encodeURIComponent(skillSlug)}`,
        { headers: this.headers() },
      );
      if (!resp.ok) return null;
      return (await resp.json()) as ResumeState;
    } catch {
      return null;
    }
  }

  /**
   * Runs a backtest. Throws BackendError on 4xx/5xx so callers can relay the
   * error cleanly (403 → upgrade prompt, 401 → invalid key, etc.).
   */
  async backtest(params: {
    skill_slug: string;
    config: Record<string, number>;
    days?: number;
    venue?: string;
  }): Promise<BacktestResult> {
    const resp = await fetch(`${this.apiUrl}/api/sdk/autoresearch/backtest`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({
        skill_slug: params.skill_slug,
        config: params.config,
        days: params.days ?? 7,
        venue: params.venue ?? "sim",
      }),
    });
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as BacktestResult;
  }
}

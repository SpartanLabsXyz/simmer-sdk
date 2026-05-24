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

// Raw primitive API types

export interface TradeParams {
  market_id: string;
  side: "yes" | "no";
  action?: "buy" | "sell";
  amount?: number;
  shares?: number;
  venue?: string;
  dry_run?: boolean;
  reasoning?: string;
  source?: string;
}

export interface TradeResult {
  [key: string]: unknown;
}

export interface BriefingResult {
  [key: string]: unknown;
}

export interface MarketsResult {
  markets: unknown[];
  [key: string]: unknown;
}

export interface MarketContextResult {
  [key: string]: unknown;
}

export interface CancelOrderResult {
  [key: string]: unknown;
}

export interface PortfolioResult {
  [key: string]: unknown;
}

export interface PositionsResult {
  [key: string]: unknown;
}

export interface FleetSummaryResult {
  [key: string]: unknown;
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

  // -------------------------------------------------------------------------
  // Raw trading primitives
  // -------------------------------------------------------------------------

  /**
   * Execute or dry-run a trade. Throws BackendError on 4xx/5xx.
   */
  async trade(params: TradeParams): Promise<TradeResult> {
    const resp = await this.timedFetch(
      `${this.apiUrl}/api/sdk/trade`,
      {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(params),
      },
      30_000,
    );
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as TradeResult;
  }

  /**
   * Get the agent briefing (portfolio, positions, opportunities, performance).
   * Throws BackendError on 4xx/5xx.
   */
  async getBriefing(since?: string): Promise<BriefingResult> {
    const url = since
      ? `${this.apiUrl}/api/sdk/briefing?since=${encodeURIComponent(since)}`
      : `${this.apiUrl}/api/sdk/briefing`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as BriefingResult;
  }

  /**
   * List or search markets. Throws BackendError on 4xx/5xx.
   */
  async getMarkets(params: {
    q?: string;
    limit?: number;
    venue?: string;
    status?: string;
    tags?: string;
    sort?: string;
  }): Promise<MarketsResult> {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.venue) qs.set("venue", params.venue);
    if (params.status) qs.set("status", params.status);
    if (params.tags) qs.set("tags", params.tags);
    if (params.sort) qs.set("sort", params.sort);
    const url = `${this.apiUrl}/api/sdk/markets?${qs.toString()}`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as MarketsResult;
  }

  /**
   * Get rich context for a market. Throws BackendError on 4xx/5xx.
   */
  async getMarketContext(marketId: string, params: {
    my_probability?: number;
    venue?: string;
  } = {}): Promise<MarketContextResult> {
    const qs = new URLSearchParams();
    if (params.my_probability !== undefined) qs.set("my_probability", String(params.my_probability));
    if (params.venue) qs.set("venue", params.venue);
    const qStr = qs.toString();
    const url = `${this.apiUrl}/api/sdk/context/${encodeURIComponent(marketId)}${qStr ? `?${qStr}` : ""}`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as MarketContextResult;
  }

  /**
   * Cancel a single open order by ID. Throws BackendError on 4xx/5xx.
   */
  async cancelOrder(orderId: string): Promise<CancelOrderResult> {
    const resp = await this.timedFetch(
      `${this.apiUrl}/api/sdk/orders/${encodeURIComponent(orderId)}`,
      { method: "DELETE", headers: this.headers() },
      15_000,
    );
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as CancelOrderResult;
  }

  async getPortfolio(): Promise<PortfolioResult> {
    const resp = await this.timedFetch(
      `${this.apiUrl}/api/sdk/portfolio`,
      { headers: this.headers() },
      15_000,
    );
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as PortfolioResult;
  }

  async getPositions(params: { venue?: string } = {}): Promise<PositionsResult> {
    const qs = new URLSearchParams();
    if (params.venue) qs.set("venue", params.venue);
    const qStr = qs.toString();
    const url = `${this.apiUrl}/api/sdk/positions${qStr ? `?${qStr}` : ""}`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as PositionsResult;
  }

  async getExpiringPositions(params: { hours?: number } = {}): Promise<PositionsResult> {
    const qs = new URLSearchParams();
    if (params.hours) qs.set("hours", String(params.hours));
    const qStr = qs.toString();
    const url = `${this.apiUrl}/api/sdk/positions/expiring${qStr ? `?${qStr}` : ""}`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as PositionsResult;
  }

  async getFleetSummary(): Promise<FleetSummaryResult> {
    const resp = await this.timedFetch(
      `${this.apiUrl}/api/sdk/fleet/summary`,
      { headers: this.headers() },
      15_000,
    );
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as FleetSummaryResult;
  }

  /**
   * Get portfolio summary (balance, value, P&L breakdown).
   */
  async getPortfolio(): Promise<PortfolioResult> {
    const resp = await this.timedFetch(
      `${this.apiUrl}/api/sdk/portfolio`,
      { headers: this.headers() },
      15_000,
    );
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as PortfolioResult;
  }

  /**
   * Get open positions, optionally filtered by venue.
   */
  async getPositions(params: {
    venue?: string;
  } = {}): Promise<PositionsResult> {
    const qs = new URLSearchParams();
    if (params.venue) qs.set("venue", params.venue);
    const qStr = qs.toString();
    const url = `${this.apiUrl}/api/sdk/positions${qStr ? `?${qStr}` : ""}`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as PositionsResult;
  }

  /**
   * Get positions expiring within a window.
   */
  async getExpiringPositions(params: {
    hours?: number;
  } = {}): Promise<PositionsResult> {
    const qs = new URLSearchParams();
    if (params.hours) qs.set("hours", String(params.hours));
    const qStr = qs.toString();
    const url = `${this.apiUrl}/api/sdk/positions/expiring${qStr ? `?${qStr}` : ""}`;
    const resp = await this.timedFetch(url, { headers: this.headers() }, 15_000);
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      throw new BackendError(resp.status, detail);
    }
    return (await resp.json()) as PositionsResult;
  }

  /**
   * Get fleet summary — all agents' positions, P&L, trade counts.
   */
  async getFleetSummary(): Promise<FleetSummaryResult> {
    const resp = await this.timedFetch(
      `${this.apiUrl}/api/sdk/fleet/summary`,
      { headers: this.headers() },
      15_000,
    );
    if (!resp.ok) {
      const detail = await this.extractDetail(resp);
      const upgradeUrl = resp.status === 403 ? "https://simmer.markets/pro" : undefined;
      throw new BackendError(resp.status, detail, upgradeUrl);
    }
    return (await resp.json()) as FleetSummaryResult;
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

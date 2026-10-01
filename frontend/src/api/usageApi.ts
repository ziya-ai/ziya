/**
 * Cost-accounting API client (backend: app/routes/usage_routes.py).
 *
 * Mounted at /api/usage rather than under the /api/v1 project tree because
 * the ledger is global: "what did September cost" crosses projects.  Hence
 * plain fetch instead of the /api/v1 `api` client.
 *
 * Rendering rule (design/CostModel.md): token counts are either observed
 * (provider-reported, rendered **bold**) or estimated (Ziya's pre-flight
 * count, rendered *italic*).  A rollup containing any estimated line is
 * italic.  Cost is null — never zero — when no rate is known.
 */

export type RollupKey =
  | 'user' | 'project' | 'conversation' | 'run' | 'model' | 'provider'
  | 'tier' | 'source' | 'day' | 'month' | 'status';

export interface RollupGroup {
  key: string;
  provider?: string;
  records: number;
  estimate_records: number;
  actual_records: number;
  dims: Record<string, number>;
  estimate_dims: Record<string, number>;
  actual_dims: Record<string, number>;
  /** null when no line in the group could be priced */
  list_cost: number | null;
  effective_cost: number | null;
  priced_records: number;
  unknown_records: number;
  partial_records: number;
  provenance: Record<string, number>;
  unpriced_dims: string[];
  has_estimates: boolean;
  is_partial: boolean;
  first_ts: number | null;
  last_ts: number | null;
}

export interface RollupResponse {
  by: RollupKey;
  groups: RollupGroup[];
  totals: RollupGroup;
  catalog_version: string;
  plan: { id: string; type: string; [k: string]: unknown };
  record_count: number;
}

export interface UsageSummary {
  catalog: {
    version: string; builtin_revision: string; user_revision: string | null;
    builtin_entries: number; user_entries: number; user_file: string;
  };
  plan: { id: string; type: string };
  ledger: { path: string; records: number };
}

export interface RollupFilters {
  since?: string | number;
  until?: string | number;
  project_id?: string;
  conversation_id?: string;
  run_id?: string;
  provider?: string;
  source?: string;
}

function qs(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch { /* body not JSON */ }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export function fetchUsageSummary(): Promise<UsageSummary> {
  return getJson<UsageSummary>('/api/usage/summary');
}

export function fetchUsageRollup(by: RollupKey, filters: RollupFilters = {}): Promise<RollupResponse> {
  return getJson<RollupResponse>(`/api/usage/rollup${qs({ by, ...filters })}`);
}

/** Totals for one conversation, priced.  Rollup by status so the
 *  estimate/actual split is the grouping itself. */
export function fetchConversationCost(conversationId: string): Promise<RollupResponse> {
  return fetchUsageRollup('status', { conversation_id: conversationId });
}

// ── Pure presentation helpers (tested in __tests__/usageApi.test.ts) ─────

export type CostEmphasis = 'observed' | 'estimated';

/** Bold when every line's tokens were provider-reported; italic otherwise. */
export function costEmphasis(g: Pick<RollupGroup, 'has_estimates'>): CostEmphasis {
  return g.has_estimates ? 'estimated' : 'observed';
}

/**
 * Format a cost for display.  null is "unpriced", not "$0.00": zero is a
 * real price (local models) and must not be confused with "no rate known".
 * Sub-cent figures keep enough precision to be non-zero.
 */
export function formatCost(cost: number | null | undefined, currency = '$'): string {
  if (cost === null || cost === undefined) return 'unpriced';
  if (cost === 0) return `${currency}0.00`;
  const abs = Math.abs(cost);
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 3 : 4;
  return `${cost < 0 ? '-' : ''}${currency}${abs.toFixed(digits)}`;
}

/** Compact token count: 1.2k, 3.4M, 5.6B. */
export function formatTokens(n: number | undefined | null): string {
  if (!n) return '0';
  if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return String(n);
}

/**
 * One-line qualifier for a group's cost figure, or null when the figure
 * is complete: names the reason a number is incomplete rather than
 * presenting a smaller total as if it were the whole.
 */
export function costQualifier(g: RollupGroup): string | null {
  if (g.effective_cost === null) {
    return g.records ? `no rate for ${g.records} record${g.records === 1 ? '' : 's'}` : null;
  }
  const parts: string[] = [];
  if (g.unknown_records) parts.push(`${g.unknown_records} unpriced`);
  if (g.partial_records) parts.push(`${g.partial_records} partial (${g.unpriced_dims.join(', ')})`);
  if (g.has_estimates) parts.push(`${g.estimate_records} estimated`);
  return parts.length ? parts.join(' · ') : null;
}

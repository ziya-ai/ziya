/**
 * Cost-accounting client: the presentation rule and the wiring.
 *
 * The rule (design/CostModel.md): observed tokens render bold, any
 * estimated line makes the figure italic, and a null cost is "unpriced" —
 * never "$0.00", because zero is a real price (local models).  The helpers
 * that encode this are pure, so they are pinned here directly.
 *
 * The wiring tests read component source: a helper that exists but is not
 * mounted anywhere is the failure mode these guard against (UsageCostModal
 * not opened from anywhere; ConversationInfoModal never fetching cost).
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  costEmphasis, costQualifier, fetchConversationCost, fetchUsageRollup,
  formatCost, formatTokens, RollupGroup,
} from '../usageApi';

function group(over: Partial<RollupGroup> = {}): RollupGroup {
  return {
    key: 'k', records: 10, estimate_records: 0, actual_records: 10,
    dims: { input: 1000 }, estimate_dims: {}, actual_dims: { input: 1000 },
    list_cost: 1.5, effective_cost: 1.5, priced_records: 10, unknown_records: 0,
    partial_records: 0, provenance: { list: 10 }, unpriced_dims: [],
    has_estimates: false, is_partial: false, first_ts: 1, last_ts: 2,
    ...over,
  };
}

describe('formatCost', () => {
  it('renders null as unpriced, never as a zero', () => {
    expect(formatCost(null)).toBe('unpriced');
    expect(formatCost(undefined)).toBe('unpriced');
  });
  it('renders a real zero as $0.00 (local models)', () => {
    expect(formatCost(0)).toBe('$0.00');
  });
  it('keeps sub-cent figures non-zero', () => {
    expect(formatCost(0.000174)).toBe('$0.0002');
    expect(formatCost(0.0042)).toBe('$0.0042');
    expect(formatCost(0.42)).toBe('$0.420');
    expect(formatCost(12.345)).toBe('$12.35');
  });
});

describe('costEmphasis', () => {
  it('is observed only when no line was estimated', () => {
    expect(costEmphasis(group())).toBe('observed');
    expect(costEmphasis(group({ has_estimates: true }))).toBe('estimated');
  });
});

describe('costQualifier', () => {
  it('is null for a complete, fully-observed figure', () => {
    expect(costQualifier(group())).toBeNull();
  });
  it('names why a figure is incomplete rather than hiding it', () => {
    expect(costQualifier(group({ effective_cost: null, records: 3 }))).toBe('no rate for 3 records');
    expect(costQualifier(group({ unknown_records: 2, is_partial: true }))).toBe('2 unpriced');
    expect(costQualifier(group({ partial_records: 1, unpriced_dims: ['cache_read'], is_partial: true })))
      .toBe('1 partial (cache_read)');
    expect(costQualifier(group({ has_estimates: true, estimate_records: 4 }))).toBe('4 estimated');
  });
});

describe('formatTokens', () => {
  it('compacts with one decimal until the next order of magnitude', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(999)).toBe('999');
    expect(formatTokens(1234)).toBe('1.2k');
    expect(formatTokens(12345)).toBe('12k');
    expect(formatTokens(3_026_122_464)).toBe('3.0B');
  });
});

describe('fetch helpers', () => {
  const realFetch = global.fetch;
  afterEach(() => { global.fetch = realFetch; });

  it('hits /api/usage (global), not the /api/v1 project tree, and encodes filters', async () => {
    const calls: string[] = [];
    global.fetch = jest.fn(async (url: string) => {
      calls.push(url);
      return { ok: true, json: async () => ({ by: 'status', groups: [], totals: group() }) } as Response;
    }) as unknown as typeof fetch;
    await fetchUsageRollup('model', { since: '2026-09-01', project_id: 'p 1' });
    await fetchConversationCost('c/1');
    expect(calls[0]).toBe('/api/usage/rollup?by=model&since=2026-09-01&project_id=p%201');
    expect(calls[1]).toBe('/api/usage/rollup?by=status&conversation_id=c%2F1');
  });

  it('surfaces the server detail on a non-2xx', async () => {
    global.fetch = jest.fn(async () => ({
      ok: false, status: 400, json: async () => ({ detail: 'unknown rollup key' }),
    })) as unknown as typeof fetch;
    await expect(fetchUsageRollup('model')).rejects.toThrow('unknown rollup key');
  });
});

describe('wiring', () => {
  const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', '..', rel), 'utf8');

  it('UsageCostModal is reached only from the model config modal (no discovery link)', () => {
    // Deliberate: cost display stays out of sight until a user asks, and
    // they are pointed at the model config modal.  See
    // utils/__tests__/costDisplay.test.ts for the full wiring assertions.
    expect(read('components/ProjectManagerModal.tsx')).not.toMatch(/UsageCostModal/);
    expect(read('components/ModelConfigModal.tsx')).toMatch(/<UsageCostModal[\s\S]*visible=\{showUsageCost\}/);
  });

  it('ConversationInfoModal fetches and renders the conversation cost', () => {
    const src = read('components/ConversationInfoModal.tsx');
    expect(src).toMatch(/fetchConversationCost\(conversationId\)/);
    expect(src).toMatch(/<CostCell g=\{cost\.totals\}/);
  });

  it('UsageCostModal renders the emphasis rule through the shared helpers', () => {
    const src = read('components/UsageCostModal.tsx');
    expect(src).toMatch(/costEmphasis\(g\)/);
    expect(src).toMatch(/<Text strong>/);
    expect(src).toMatch(/<Text italic>/);
    expect(src).toMatch(/formatCost\(g\.effective_cost\)/);
  });
});

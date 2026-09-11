import {
  DEFAULT_AUTO_ADD_AGGREGATE_BUDGET,
  filterByAggregateAutoAddBudget,
} from '../autoAddTokenLimit';

describe('filterByAggregateAutoAddBudget', () => {
  const counts: Record<string, number> = {
    'a.ts': 4000,
    'b.ts': 4000,
    'c.ts': 4000,
    'd.ts': 4000,
    'unknown.bin': 0,
  };
  const getTokens = (p: string) => counts[p] ?? 0;

  it('allows everything when currentTotal + new files stay under budget', () => {
    const r = filterByAggregateAutoAddBudget(['a.ts', 'b.ts'], 0, 10000, getTokens);
    expect(r.allowed).toEqual(['a.ts', 'b.ts']);
    expect(r.skipped).toEqual([]);
  });

  it('stops accepting once the running total would exceed the budget', () => {
    // budget=10000; a.ts(4000) -> 4000, b.ts(4000) -> 8000, c.ts(4000) would
    // be 12000 > 10000 so it's skipped, d.ts likewise.
    const r = filterByAggregateAutoAddBudget(
      ['a.ts', 'b.ts', 'c.ts', 'd.ts'], 0, 10000, getTokens,
    );
    expect(r.allowed).toEqual(['a.ts', 'b.ts']);
    expect(r.skipped.map(s => s.path)).toEqual(['c.ts', 'd.ts']);
  });

  it('accounts for an already-spent currentTotal from prior auto-adds', () => {
    // This is the exact regression scenario: many earlier small auto-adds
    // (from prior turns in the same session) already consumed most of the
    // budget, so even a small new batch should now be rejected.
    const r = filterByAggregateAutoAddBudget(['a.ts'], 9000, 10000, getTokens);
    expect(r.allowed).toEqual([]);
    expect(r.skipped).toEqual([{ path: 'a.ts', tokens: 4000 }]);
  });

  it('never blocks files whose size is unknown (0), matching per-file filter contract', () => {
    const r = filterByAggregateAutoAddBudget(['unknown.bin'], 9999, 10000, getTokens);
    expect(r.allowed).toEqual(['unknown.bin']);
  });

  it('budget <= 0 disables the check entirely', () => {
    const r = filterByAggregateAutoAddBudget(['a.ts', 'b.ts', 'c.ts'], 999999, 0, getTokens);
    expect(r.allowed).toEqual(['a.ts', 'b.ts', 'c.ts']);
    expect(r.skipped).toEqual([]);
  });

  it('non-finite budget disables the check', () => {
    const r = filterByAggregateAutoAddBudget(['a.ts'], 0, Infinity, getTokens);
    expect(r.allowed).toEqual(['a.ts']);
  });

  it('default aggregate budget is 100000', () => {
    expect(DEFAULT_AUTO_ADD_AGGREGATE_BUDGET).toBe(100000);
  });

  it('handles an empty path list', () => {
    const r = filterByAggregateAutoAddBudget([], 0, 10000, getTokens);
    expect(r.allowed).toEqual([]);
    expect(r.skipped).toEqual([]);
  });
});

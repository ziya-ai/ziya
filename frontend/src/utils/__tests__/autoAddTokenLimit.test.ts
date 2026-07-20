import {
  DEFAULT_AUTO_ADD_TOKEN_LIMIT,
  filterByAutoAddTokenLimit,
} from '../autoAddTokenLimit';

describe('filterByAutoAddTokenLimit', () => {
  const counts: Record<string, number> = {
    'small.ts': 1200,
    'medium.ts': 34999,
    'exact.ts': 35000,
    'huge.html': 90000,
    'unknown.bin': 0,
  };
  const getTokens = (p: string) => counts[p] ?? 0;

  it('default limit is 35000 tokens', () => {
    expect(DEFAULT_AUTO_ADD_TOKEN_LIMIT).toBe(35000);
  });

  it('skips files over the limit and keeps the rest', () => {
    const r = filterByAutoAddTokenLimit(
      ['small.ts', 'huge.html', 'medium.ts'],
      DEFAULT_AUTO_ADD_TOKEN_LIMIT,
      getTokens,
    );
    expect(r.allowed).toEqual(['small.ts', 'medium.ts']);
    expect(r.skipped).toEqual([{ path: 'huge.html', tokens: 90000 }]);
  });

  it('allows a file exactly at the limit', () => {
    const r = filterByAutoAddTokenLimit(['exact.ts'], 35000, getTokens);
    expect(r.allowed).toEqual(['exact.ts']);
    expect(r.skipped).toEqual([]);
  });

  it('never blocks files with unknown (zero) token counts', () => {
    const r = filterByAutoAddTokenLimit(['unknown.bin'], 100, getTokens);
    expect(r.allowed).toEqual(['unknown.bin']);
  });

  it('never blocks files whose counter returns NaN', () => {
    const r = filterByAutoAddTokenLimit(['x.ts'], 100, () => NaN);
    expect(r.allowed).toEqual(['x.ts']);
    expect(r.skipped).toEqual([]);
  });

  it('limit of 0 disables filtering entirely', () => {
    const r = filterByAutoAddTokenLimit(
      ['small.ts', 'huge.html'],
      0,
      getTokens,
    );
    expect(r.allowed).toEqual(['small.ts', 'huge.html']);
    expect(r.skipped).toEqual([]);
  });

  it('negative or non-finite limits disable filtering', () => {
    expect(
      filterByAutoAddTokenLimit(['huge.html'], -1, getTokens).allowed,
    ).toEqual(['huge.html']);
    expect(
      filterByAutoAddTokenLimit(['huge.html'], Infinity, getTokens).allowed,
    ).toEqual(['huge.html']);
  });

  it('handles an empty path list', () => {
    const r = filterByAutoAddTokenLimit([], 100, getTokens);
    expect(r.allowed).toEqual([]);
    expect(r.skipped).toEqual([]);
  });
});

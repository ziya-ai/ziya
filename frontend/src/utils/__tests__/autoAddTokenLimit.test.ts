import {
  DEFAULT_AUTO_ADD_TOKEN_LIMIT,
  filterByAutoAddTokenLimit,
} from '../autoAddTokenLimit';

describe('filterByAutoAddTokenLimit', () => {
  // Sizes are expressed RELATIVE to the default limit, not hardcoded.
  // A previous change (24282dde) lowered the default 35000 -> 12500 and
  // updated only the assertion naming the constant; 'medium.ts' had been
  // pinned at 34999 to sit just under the OLD limit, so it silently began
  // being skipped and the 'skips files over the limit' case broke.
  // Deriving the fixture from the constant keeps that from recurring.
  const counts: Record<string, number> = {
    'small.ts': Math.floor(DEFAULT_AUTO_ADD_TOKEN_LIMIT * 0.1),
    'medium.ts': DEFAULT_AUTO_ADD_TOKEN_LIMIT - 1,
    'exact.ts': DEFAULT_AUTO_ADD_TOKEN_LIMIT,
    'huge.html': DEFAULT_AUTO_ADD_TOKEN_LIMIT * 7,
    'unknown.bin': 0,
  };
  const getTokens = (p: string) => counts[p] ?? 0;

  it('default limit is 12500 tokens', () => {
    expect(DEFAULT_AUTO_ADD_TOKEN_LIMIT).toBe(12500);
  });

  it('skips files over the limit and keeps the rest', () => {
    const r = filterByAutoAddTokenLimit(
      ['small.ts', 'huge.html', 'medium.ts'],
      DEFAULT_AUTO_ADD_TOKEN_LIMIT,
      getTokens,
    );
    expect(r.allowed).toEqual(['small.ts', 'medium.ts']);
    expect(r.skipped).toEqual([
      { path: 'huge.html', tokens: counts['huge.html'] },
    ]);
  });

  it('allows a file exactly at the limit', () => {
    // Use the constant, not a literal — a literal is what let this file
    // drift out of sync with the default in the first place.
    const r = filterByAutoAddTokenLimit(
      ['exact.ts'], DEFAULT_AUTO_ADD_TOKEN_LIMIT, getTokens,
    );
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

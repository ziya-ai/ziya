/**
 * buildSearchPattern mirrors app/storage/chat_search.compile_query: a query
 * with one space must find text written with two (the "⏱️  STALL" log line),
 * and the highlight must cover the text's form, not the query's.
 */
import { buildSearchPattern, buildSearchSplitPattern } from '../searchPattern';

describe('buildSearchPattern', () => {
  it('matches a double-spaced log line from a single-spaced query', () => {
    const p = buildSearchPattern('⏱️ STALL', { global: false })!;
    expect(p.test('ZIYA: WARNING  ⏱️  STALL: event loop did not run')).toBe(true);
  });

  it('matches across a newline', () => {
    expect(buildSearchPattern('event loop', { global: false })!.test('event\nloop')).toBe(true);
  });

  it('treats regex metacharacters literally', () => {
    const p = buildSearchPattern('a.b', { global: false })!;
    expect(p.test('a.b')).toBe(true);
    expect(p.test('axb')).toBe(false);
  });

  it('is case-insensitive unless asked', () => {
    expect(buildSearchPattern('stall', { global: false })!.test('STALL')).toBe(true);
    expect(buildSearchPattern('stall', { global: false, caseSensitive: true })!.test('STALL')).toBe(false);
  });

  it('returns null for a blank query', () => {
    expect(buildSearchPattern('   ')).toBeNull();
  });

  it('reports the matched span length from the text, not the query', () => {
    const text = 'log: WARNING  ⏱️  STALL: event loop';
    const [m] = Array.from(text.matchAll(buildSearchPattern('⏱️ STALL')!));
    expect(m[0]).toBe('⏱️  STALL');
    expect(m.index).toBe(text.indexOf('⏱️'));
  });
});

describe('buildSearchSplitPattern', () => {
  it('splits so odd-indexed parts are the matches', () => {
    const parts = 'a  ⏱️  STALL b ⏱️ STALL c'.split(buildSearchSplitPattern('⏱️ STALL')!);
    expect(parts).toEqual(['a  ', '⏱️  STALL', ' b ', '⏱️ STALL', ' c']);
  });
});

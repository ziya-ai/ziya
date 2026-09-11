/**
 * G-43 — chord plugin: background-token validation + semicolon-separator
 * recovery (shared files: chordPlugin.ts, forceDirectedPlugin.ts).
 *
 * Defects covered:
 *   D-047 (theme, dark)  style.background was used VERBATIM. An unresolvable
 *         design-system token ('var(--surface-bg)') painted a transparent SVG
 *         background AND was misclassified by isDarkBackground (non-hex ->
 *         assumed LIGHT), so under dark theme the labels took the light default
 *         #333 on the actually-dark page surface (#333 on #1a1a2e = 1.35:1,
 *         erased). Fix: resolveChordBackground() resolves the caller value to a
 *         hex or falls back to the per-theme default (chord-w4-14).
 *   D-048 (recovery)  lenientParseObject handled comments / unquoted keys /
 *         single quotes / trailing commas (JSON5) but NOT semicolons used as
 *         inter-element separators (invalid JSON5), so the spec stayed
 *         unclaimable -> 30s timeout. Fix: normalizeSemicolonSeparators()
 *         rewrites out-of-string ';' -> ',' as a final parse fallback
 *         (chord-w4-15).
 *
 * DIRECTION: both fixes introduce NEW exported symbols (resolveChordBackground,
 * normalizeSemicolonSeparators) absent on the unpatched tree, so this file does
 * not compile against pre-fix source; the behavioural assertions additionally
 * pin that the pre-fix code produced the broken result.
 */
import { contrastRatio } from '../chartTheme';
import {
  resolveChordBackground,
  resolveChordLabelColor,
  normalizeChordColorToHex,
} from '../chordPlugin';
import {
  lenientParseObject,
  normalizeSemicolonSeparators,
} from '../forceDirectedPlugin';

describe('D-047 chord background-token validation (theme)', () => {
  const TOKEN = 'var(--surface-bg)';

  it('an unresolvable token resolves to the per-theme default in BOTH themes', () => {
    // Pre-fix: bg = style.background || default => the raw token verbatim.
    expect(normalizeChordColorToHex(TOKEN)).toBeNull(); // token is not a colour
    expect(resolveChordBackground(TOKEN, true)).toBe('#1a1a2e');
    expect(resolveChordBackground(TOKEN, false)).toBe('#ffffff');
  });

  it('DARK (previously broken): labels are legible on the resolved dark surface', () => {
    const bg = resolveChordBackground(TOKEN, /* isDarkMode */ true);
    // labelColor is also a bogus token in w4-14 -> falls back to the theme
    // default for the RESOLVED (dark) surface, not the light default.
    const label = resolveChordLabelColor({ labelColor: 'token.text.primary' }, bg);
    expect(label).toBe('#e0e0e0');
    expect(contrastRatio(label, bg)).toBeGreaterThan(4.5); // 12.92:1
    // Pin the pre-fix defect: the light default on the dark surface was erased.
    expect(contrastRatio('#333333', bg)).toBeLessThan(3); // 1.35:1
  });

  it('LIGHT (paired, still correct): labels stay legible on the resolved light surface', () => {
    const bg = resolveChordBackground(TOKEN, /* isDarkMode */ false);
    const label = resolveChordLabelColor({ labelColor: 'token.text.primary' }, bg);
    expect(label).toBe('#333333');
    expect(contrastRatio(label, bg)).toBeGreaterThan(4.5); // 12.63:1
  });

  it('regression: a valid caller colour is honoured (light panel pinned under dark theme, D-053)', () => {
    expect(resolveChordBackground('#f7f7f7', true)).toBe('#f7f7f7');
    expect(resolveChordBackground('rgb(20, 20, 40)', false)).toBe('#141428'); // 40 dec = 0x28
    expect(resolveChordBackground(undefined, true)).toBe('#1a1a2e');
    expect(resolveChordBackground(undefined, false)).toBe('#ffffff');
  });
});

describe('D-048 chord semicolon-separator recovery', () => {
  // The chord-w4-15 body: // and /* */ comments, semicolons where commas
  // belong, unquoted keys, single-quoted strings.
  const SEMI_BODY = `{
  // chord diagram of team handoffs
  type: 'chord';
  nodes: [
    {id: 'Design'};
    {id: 'Build'};
    {id: 'Test'};
    {id: 'Ship'}
  ];
  /* flows */
  links: [
    {source: 'Design', target: 'Build', value: 14};
    {source: 'Build',  target: 'Test',  value: 11}
  ]
}`;

  it('normalizeSemicolonSeparators rewrites only out-of-string semicolons', () => {
    expect(normalizeSemicolonSeparators('{a:1;b:2}')).toBe('{a:1,b:2}');
    // a ';' inside a string literal is preserved (single and double quoted)
    expect(normalizeSemicolonSeparators(`{a:'x;y'}`)).toBe(`{a:'x;y'}`);
    expect(normalizeSemicolonSeparators(`{a:"x;y"}`)).toBe(`{a:"x;y"}`);
    // escaped quote inside a string does not end the string
    expect(normalizeSemicolonSeparators(`{a:'x\\';y'}`)).toBe(`{a:'x\\';y'}`);
  });

  it('lenientParseObject recovers a semicolon-separated JSON5 body (was undefined pre-fix)', () => {
    const parsed = lenientParseObject(SEMI_BODY);
    expect(parsed).toBeTruthy();
    expect(parsed.type).toBe('chord');
    expect(Array.isArray(parsed.nodes)).toBe(true);
    expect(parsed.nodes.map((n: any) => n.id)).toEqual(['Design', 'Build', 'Test', 'Ship']);
    expect(parsed.links[0]).toEqual({ source: 'Design', target: 'Build', value: 14 });
  });

  it('regression: a valid JSON5 body is byte-identical through the normaliser and still parses', () => {
    const valid = `{ type: 'chord', nodes: [{id: 'A'}], links: [] }`;
    expect(normalizeSemicolonSeparators(valid)).toBe(valid);
    const parsed = lenientParseObject(valid);
    expect(parsed.nodes[0].id).toBe('A');
  });

  it('regression: a semicolon inside a string VALUE survives recovery', () => {
    const parsed = lenientParseObject(`{ label: 'a;b'; value: 1 }`);
    expect(parsed).toBeTruthy();
    expect(parsed.label).toBe('a;b');
    expect(parsed.value).toBe(1);
  });
});

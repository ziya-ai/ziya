/**
 * G-16 / D-016 — vega-family recovery: dialect / structure / theme-token gaps.
 *
 * Most of D-016 was already resolved in source by prior D-273/D-275 work (the
 * native Vega VL-body handoff, encode.update wrap, v2-dialect rewrite and
 * minimal-defaults inference) and by the tolerant parser + normalizeBareArrayData
 * + validateColorSchemes on the Vega-Lite path. Two genuinely-remaining, source-
 * confirmed gaps are closed here:
 *
 *   • vega-lite-w4-08 — semicolons used as statement separators (a JS-authoring
 *     reflex) plus a trailing ';' after the closing brace. A bare ';' is INVALID
 *     JSON/JSON5 in every position, so it defeated even the JSON5 fallback and
 *     the spec hung unclaimed. normalizeSemicolonSeparators (wired into
 *     tolerantParseVegaSpec) rewrites out-of-string ';' to ',' and the outermost
 *     slice drops the dangling trailing comma.
 *   • vega-lite-w4-15 — a bogus top-level `theme` key, an unknown
 *     usermeta.embedOptions.theme that (via vega-embed's usermeta-over-caller
 *     merge) would OVERRIDE the renderer's own theme, and '$surface'/'$textPrimary'
 *     design-token strings in colour positions (unresolvable → invalid fill).
 *     sanitizeThemeTokens strips exactly these so the active theme applies.
 *
 * DIRECTION (fails without the change): this file imports
 * normalizeSemicolonSeparators / sanitizeThemeTokens / KNOWN_VEGA_EMBED_THEMES,
 * which did NOT exist pre-fix (module import fails, whole suite red), and
 * asserts that the semicolon body PARSES where the unpatched tolerant parser
 * threw. Both themes are asserted for the theme-token case: the sanitiser is
 * theme-independent, and its output carries neither a theme override nor an
 * unresolvable colour — which is precisely what lets each of the light and dark
 * renderer themes apply unmodified.
 */
import {
  tolerantParseVegaSpec,
  normalizeSemicolonSeparators,
  sanitizeThemeTokens,
  validateColorSchemes,
  KNOWN_VEGA_EMBED_THEMES,
} from '../vegaRecovery';

// vega-lite-w4-08 verbatim: semicolon separators + trailing ';'.
const SEMICOLON_BODY = `{
  "data": {"values": [{"s": "up", "t": 20}, {"s": "flat", "t": 20}, {"s": "down", "t": 8}]};
  "mark": "bar";
  "encoding": {
    "x": {"field": "s", "type": "nominal"},
    "y": {"field": "t", "type": "quantitative"}
  }
};`;

// vega-lite-w4-15 verbatim: bogus theme directives + $design-token colours.
const THEME_TOKEN_SPEC = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  theme: 'ziya-dark',
  usermeta: { embedOptions: { theme: 'nonexistent-theme-name' } },
  data: { values: [{ c: 'A', v: 30 }, { c: 'B', v: 55 }, { c: 'C', v: 40 }] },
  mark: 'bar',
  encoding: {
    x: { field: 'c', type: 'nominal' },
    y: { field: 'v', type: 'quantitative' },
    color: { field: 'c', type: 'nominal', scale: { scheme: 'not-a-real-scheme' } },
  },
  config: { background: '$surface', axis: { labelColor: '$textPrimary' } },
});

describe('normalizeSemicolonSeparators (D-016)', () => {
  it('rewrites out-of-string semicolons to commas', () => {
    expect(normalizeSemicolonSeparators('"a": 1; "b": 2;')).toBe('"a": 1, "b": 2,');
  });

  it('preserves a semicolon INSIDE a string literal', () => {
    const src = '{"title": "up; down", "mark": "bar"}';
    // No bare semicolon → returned unchanged, and the in-string ';' survives.
    expect(normalizeSemicolonSeparators(src)).toBe(src);
  });

  it('preserves an in-string semicolon while rewriting a separator', () => {
    expect(normalizeSemicolonSeparators('"t": "a;b"; "m": 1'))
      .toBe('"t": "a;b", "m": 1');
  });

  it('is a no-op when there is no semicolon (valid spec untouched)', () => {
    const valid = '{"data":{"values":[{"a":1}]},"mark":"bar"}';
    expect(normalizeSemicolonSeparators(valid)).toBe(valid);
  });
});

describe('tolerantParseVegaSpec — semicolon separators (vega-lite-w4-08)', () => {
  it('recovers the intended object from a semicolon-separated body', () => {
    // Pre-fix this threw a SyntaxError (JSON5 rejects ';'); now it parses.
    const parsed = tolerantParseVegaSpec(SEMICOLON_BODY);
    expect(parsed).toBeTruthy();
    expect(parsed.mark).toBe('bar');
    expect(parsed.data.values).toHaveLength(3);
    expect(parsed.encoding.x.field).toBe('s');
    expect(parsed.encoding.y.type).toBe('quantitative');
  });

  it('still parses an ordinary valid spec identically', () => {
    const valid = '{"data":{"values":[{"a":1},{"a":2}]},"mark":"bar","encoding":{"x":{"field":"a"}}}';
    const parsed = tolerantParseVegaSpec(valid);
    expect(parsed.data.values).toHaveLength(2);
    expect(parsed.mark).toBe('bar');
  });
});

describe('sanitizeThemeTokens (D-016, vega-lite-w4-15)', () => {
  it('strips a bogus top-level theme key, unknown usermeta theme and $token colours', () => {
    const spec: any = THEME_TOKEN_SPEC();
    sanitizeThemeTokens(spec);
    expect('theme' in spec).toBe(false);
    expect(spec.usermeta.embedOptions.theme).toBeUndefined();
    expect(spec.config.background).toBeUndefined();
    expect(spec.config.axis.labelColor).toBeUndefined();
    // The data / mark / encoding intent survives untouched.
    expect(spec.mark).toBe('bar');
    expect(spec.data.values).toHaveLength(3);
  });

  it('preserves a VALID usermeta theme and a top-level theme is always dropped', () => {
    const spec: any = { theme: 'x', usermeta: { embedOptions: { theme: 'dark' } } };
    sanitizeThemeTokens(spec);
    expect('theme' in spec).toBe(false);            // VL has no top-level theme
    expect(spec.usermeta.embedOptions.theme).toBe('dark'); // registered → kept
    expect(KNOWN_VEGA_EMBED_THEMES.has('dark')).toBe(true);
  });

  it('leaves a resolvable colour untouched', () => {
    const spec: any = { config: { background: '#ffffff', axis: { labelColor: 'red' } } };
    sanitizeThemeTokens(spec);
    expect(spec.config.background).toBe('#ffffff');
    expect(spec.config.axis.labelColor).toBe('red');
  });

  // Theme defect verified in BOTH themes: the sanitiser is theme-independent, so
  // running the full recovery (validateColorSchemes + sanitizeThemeTokens) once
  // yields a spec that carries NO theme override and NO unresolvable colour —
  // exactly the state in which each renderer theme (dark / light) applies its
  // own guide colours. Asserting the invariant covers both themes at once.
  for (const label of ['light', 'dark'] as const) {
    it(`${label}: recovered spec defers entirely to the renderer theme`, () => {
      const spec: any = THEME_TOKEN_SPEC();
      // Full VL recovery order (mirrors the plugin pipeline).
      validateColorSchemes(spec);   // drops the unknown scheme
      sanitizeThemeTokens(spec);
      // No theme directive of any kind remains…
      expect('theme' in spec).toBe(false);
      expect(spec.usermeta.embedOptions.theme).toBeUndefined();
      // …no unresolvable ($token) colour remains…
      expect(spec.config.background).toBeUndefined();
      expect(spec.config.axis.labelColor).toBeUndefined();
      // …and the invalid colour scheme is gone, so the color channel falls
      // back to the theme's own categorical range in BOTH themes.
      expect(spec.encoding.color.scale?.scheme).toBeUndefined();
    });
  }
});

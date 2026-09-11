/**
 * G-VEGALITE-RECOVERY — vega-lite recovery cluster (backlog defects D-233,
 * D-242, D-243, D-244). All four are kind=recovery and THEME-INDEPENDENT: the
 * helpers under test take no theme argument and emit no colour, so the repair
 * is identical in light and dark (parsing / spec-shape normalisation precedes
 * theming). Each assertion is written to FAIL against pre-fix code — direction
 * noted inline.
 *
 *   D-233  json-repair semicolons (vega-lite-w4-08)  — CONFIRMED already
 *          remediated: normalizeSemicolonSeparators inside tolerantParseVegaSpec.
 *   D-242  v2 $schema drops legend (vega-lite-w4-05)  — CONFIRMED already
 *          remediated by upgradeStaleVegaLiteSchema (also covered by vegaG53).
 *   D-243  missing encoding type -> nominal (vega-lite-w4-07) — FIXED here:
 *          inferEncodingTypes (new). Import of it does not exist on the
 *          unpatched tree, so this whole file fails to compile pre-fix.
 *   D-244  unsanitized $colour token drops axis labels (vega-lite-w4-15) —
 *          CONFIRMED already remediated by sanitizeThemeTokens +
 *          validateColorSchemes.
 */
import {
  tolerantParseVegaSpec,
  sanitizeThemeTokens,
  validateColorSchemes,
  normalizeBareArrayData,
  inferEncodingTypes,
} from '../vegaRecovery';
import { upgradeStaleVegaLiteSchema } from '../vegaLitePlugin';

// ── D-233: semicolon separators + trailing ';' (vega-lite-w4-08) ────────────
describe('D-233 tolerantParseVegaSpec recovers semicolon-separated spec', () => {
  const W4_08 = `{
  "data": {"values": [{"s": "up", "t": 20}, {"s": "flat", "t": 20}, {"s": "down", "t": 8}]};
  "mark": "bar";
  "encoding": {
    "x": {"field": "s", "type": "nominal"},
    "y": {"field": "t", "type": "quantitative"}
  }
};`;

  it('plain JSON.parse rejects it (direction: the bug is real)', () => {
    expect(() => JSON.parse(W4_08)).toThrow();
  });

  it('recovers the 3-bar up/flat/down spec (semicolons -> commas, trailing ; dropped)', () => {
    const spec = tolerantParseVegaSpec(W4_08);
    expect(spec.mark).toBe('bar');
    expect(spec.data.values).toHaveLength(3);
    expect(spec.encoding.x.field).toBe('s');
    expect(spec.encoding.y.field).toBe('t');
  });
});

// ── D-242: v2 schema declared with v5-only syntax (vega-lite-w4-05) ──────────
describe('D-242 upgradeStaleVegaLiteSchema lifts the v2 schema to v5', () => {
  it('upgrades v2 so legend.orient / cornerRadiusEnd are honoured', () => {
    // Direction: unpatched code left a v2 URL stale, so vega-embed compiled
    // under v2 and silently dropped the declared legend.
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v2.json'))
      .toBe('https://vega.github.io/schema/vega-lite/v5.json');
  });
  it('does not touch an already-v5 schema', () => {
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v5.json')).toBeNull();
  });
});

// ── D-243: missing encoding type -> numeric field mis-inferred as nominal ────
describe('D-243 inferEncodingTypes infers quantitative from numeric data', () => {
  // vega-lite-w4-07: bare-array data, no types. Normalise the bare array first,
  // exactly as the render pipeline does, then infer types.
  const buildW4_07 = () => normalizeBareArrayData({
    data: [
      { city: 'Oslo', pop: 700 },
      { city: 'Bergen', pop: 280 },
      { city: 'Tromso', pop: 77 },
    ],
    mark: 'bar',
    encoding: {
      x: { field: 'city' },
      y: { field: 'pop' },
    },
  });

  it('marks the numeric y field quantitative (pre-fix left it nominal -> equal-height bars)', () => {
    const spec = buildW4_07();
    const filled = inferEncodingTypes(spec);
    expect(spec.encoding.y.type).toBe('quantitative');
    expect(filled).toBeGreaterThanOrEqual(1);
  });

  it('leaves a non-numeric field to the nominal default (no over-reach)', () => {
    const spec = buildW4_07();
    inferEncodingTypes(spec);
    // A categorical string field keeps Vega-Lite's own nominal default — we do
    // not stamp a redundant type onto it.
    expect(spec.encoding.x.type).toBeUndefined();
  });

  it('infers temporal for an ISO-date field', () => {
    const spec: any = {
      data: { values: [{ d: '2024-01-01', v: 1 }, { d: '2024-02-01', v: 2 }] },
      mark: 'line',
      encoding: { x: { field: 'd' }, y: { field: 'v' } },
    };
    inferEncodingTypes(spec);
    expect(spec.encoding.x.type).toBe('temporal');
    expect(spec.encoding.y.type).toBe('quantitative');
  });

  it('never touches an already-typed channel or a native Vega spec', () => {
    const typed: any = {
      data: { values: [{ a: 1 }] },
      encoding: { y: { field: 'a', type: 'nominal' } },
    };
    expect(inferEncodingTypes(typed)).toBe(0);
    expect(typed.encoding.y.type).toBe('nominal');

    const nativeVega: any = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      marks: [{ type: 'rect' }],
      encoding: { y: { field: 'a' } },
    };
    expect(inferEncodingTypes(nativeVega)).toBe(0);
    expect(nativeVega.encoding.y.type).toBeUndefined();
  });

  it('does not infer temporal from a bare numeric-looking string ("700")', () => {
    const spec: any = {
      data: { values: [{ k: '700' }, { k: '280' }] },
      encoding: { x: { field: 'k' } },
    };
    inferEncodingTypes(spec);
    // "700" has no date separator -> not temporal; it is also not a JS number,
    // so it stays on the nominal default rather than mis-inferring.
    expect(spec.encoding.x.type).toBeUndefined();
  });
});

// ── D-244: unresolved $design-token colour drops axis labels (w4-15) ─────────
describe('D-244 sanitizeThemeTokens + validateColorSchemes neutralise bad tokens', () => {
  const buildW4_15 = (): any => ({
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

  it('drops config.axis.labelColor "$textPrimary" so axis labels survive', () => {
    const spec = buildW4_15();
    sanitizeThemeTokens(spec);
    // Direction: unpatched code passed '$textPrimary' to Vega, which voided all
    // axis tick labels; deleting it lets the theme default colour paint them.
    expect(spec.config.axis.labelColor).toBeUndefined();
  });

  it('drops config.background "$surface" and the bogus theme keys', () => {
    const spec = buildW4_15();
    sanitizeThemeTokens(spec);
    expect(spec.config.background).toBeUndefined();
    expect(spec.theme).toBeUndefined();
    expect(spec.usermeta.embedOptions.theme).toBeUndefined();
  });

  it('drops the unknown colour scheme (else the render collapses to blank)', () => {
    const spec = buildW4_15();
    const dropped = validateColorSchemes(spec);
    expect(dropped).toBeGreaterThanOrEqual(1);
    expect(spec.encoding.color.scale.scheme).toBeUndefined();
  });

  it('leaves a resolvable colour and a known scheme untouched', () => {
    const spec: any = {
      config: { background: '#ffffff', axis: { labelColor: '#333333' } },
      encoding: { color: { scale: { scheme: 'category10' } } },
    };
    sanitizeThemeTokens(spec);
    validateColorSchemes(spec);
    expect(spec.config.background).toBe('#ffffff');
    expect(spec.config.axis.labelColor).toBe('#333333');
    expect(spec.encoding.color.scale.scheme).toBe('category10');
  });
});

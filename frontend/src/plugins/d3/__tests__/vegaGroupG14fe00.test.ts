/**
 * Group G-14fe00 — vega / vega-lite recovery + native-theme cluster.
 *
 * The six defects in this group (D-254, D-263, D-264, D-275, D-276, D-279) are
 * DUPLICATE SIGNATURES of defects already remediated in prior iterations under
 * other ids; the legacy import logged the same spec ids a second time. This
 * file pins each G-14fe00 defect id to the LIVE fix function so the group has
 * its own direction-verified guard: every assertion below throws on a tree
 * where the corresponding fix is reverted (the numeric-type inference and the
 * native reconcilers do not even exist pre-fix, so the imports go undefined and
 * the file fails outright). Root cause per defect confirmed against source:
 *
 *   D-254 == D-233  json-repair semicolons (vega-lite-w4-08)
 *                    → normalizeSemicolonSeparators inside tolerantParseVegaSpec
 *   D-263 == D-242  v2 $schema drops legend (vega-lite-w4-05)
 *                    → upgradeStaleVegaLiteSchema (wired in vegaLitePlugin)
 *   D-264 == D-243  missing encoding type -> nominal (vega-lite-w4-07)
 *                    → normalizeBareArrayData + inferEncodingTypes
 *   D-275 == D-015  native path skips theme colour reconciliation (vega-w4-11/12)
 *                    → reconcileVegaThemeBackground + reconcileVegaGuideColors
 *                      (BOTH themes asserted)
 *   D-276 == D-228  native json syntax not repaired (vega-w4-01..06)
 *                    → tolerantParseVegaSpec
 *   D-279 == D-231  bespoke {scheme:'ziyaDark'} blanks canvas (vega-w4-14)
 *                    → sanitizeVegaSchemes drops the unknown scheme + emptied range
 */
import {
  tolerantParseVegaSpec,
  normalizeBareArrayData,
  inferEncodingTypes,
  resolveColorToRgb,
  contrastRatio,
} from '../vegaRecovery';
import { upgradeStaleVegaLiteSchema } from '../vegaLitePlugin';
import {
  sanitizeVegaSchemes,
  reconcileVegaThemeBackground,
  reconcileVegaGuideColors,
} from '../vegaPlugin';

// ── D-254 (== D-233): semicolon separators + trailing ';' (vega-lite-w4-08) ──
describe('D-254 semicolon-separated vega-lite spec recovers', () => {
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

  it('tolerantParseVegaSpec rewrites ; -> , and drops the trailing ;', () => {
    const spec = tolerantParseVegaSpec(W4_08);
    expect(spec.mark).toBe('bar');
    expect(spec.data.values).toHaveLength(3);
    expect(spec.encoding.y.field).toBe('t');
  });
});

// ── D-263 (== D-242): stale v2 $schema silently drops v5 legend (w4-05) ──────
describe('D-263 stale v2 vega-lite schema is upgraded to v5', () => {
  it('lifts v2 -> v5 so legend.orient / cornerRadiusEnd are honoured', () => {
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v2.json'))
      .toBe('https://vega.github.io/schema/vega-lite/v5.json');
  });
  it('leaves an already-v5 schema untouched (no over-reach)', () => {
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v5.json')).toBeNull();
  });
});

// ── D-264 (== D-243): numeric field with no type mis-inferred nominal (w4-07) ─
describe('D-264 numeric encoding gains a quantitative type', () => {
  // vega-lite-w4-07: bare-array data, no $schema, no encoding types.
  const w4_07 = () => ({
    data: [
      { city: 'Oslo', pop: 700 },
      { city: 'Bergen', pop: 280 },
      { city: 'Tromso', pop: 77 },
    ],
    mark: 'bar',
    encoding: { x: { field: 'city' }, y: { field: 'pop' } },
  });

  it('wraps the bare array then infers pop -> quantitative, city stays nominal', () => {
    const spec: any = normalizeBareArrayData(w4_07());
    expect(spec.data.values).toHaveLength(3); // bare array normalised
    const filled = inferEncodingTypes(spec);
    expect(spec.encoding.y.type).toBe('quantitative'); // pre-fix: undefined -> nominal bands
    expect(spec.encoding.x.type).toBeUndefined();       // nominal default is correct
    expect(filled).toBeGreaterThanOrEqual(1);
  });
});

// ── D-275 (== D-015): native Vega path skips theme colour reconciliation ─────
// vega-w4-11: authored light `#fff` background + near-black guide colours on a
// native (marks[]) spec. Must reconcile in BOTH themes.
describe('D-275 native Vega theme reconciliation (both themes)', () => {
  const w4_11 = (): any => ({
    $schema: 'https://vega.github.io/schema/vega/v5.json',
    background: '#fff',
    data: [{ name: 't', values: [{ c: 'a', v: 30 }] }],
    axes: [{ orient: 'bottom', scale: 'x', labelColor: '#111111', gridColor: '#0a0a0a' }],
    marks: [],
  });

  it('DARK: drops the light #fff background so the dark panel shows through', () => {
    const spec = reconcileVegaThemeBackground(w4_11(), /*isDarkMode*/ true);
    expect(spec.background).toBeUndefined();
  });

  it('LIGHT: keeps the #fff background (correct for the light canvas — no over-correction)', () => {
    const spec = reconcileVegaThemeBackground(w4_11(), /*isDarkMode*/ false);
    expect(spec.background).toBe('#fff');
  });

  it('DARK: nudges sub-floor guide colours to >=3:1 on the #333 dark panel', () => {
    const spec = reconcileVegaThemeBackground(w4_11(), true);
    reconcileVegaGuideColors(spec, true);
    const panel = resolveColorToRgb('#333333')!;
    const label = resolveColorToRgb(spec.axes[0].labelColor)!;
    const grid = resolveColorToRgb(spec.axes[0].gridColor)!;
    expect(contrastRatio(label, panel)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(grid, panel)).toBeGreaterThanOrEqual(3);
  });

  it('LIGHT: leaves already-legible dark guide colours on white untouched', () => {
    const spec = reconcileVegaThemeBackground(w4_11(), false);
    reconcileVegaGuideColors(spec, false);
    const white = resolveColorToRgb('#ffffff')!;
    // #111 on white is ~18.9:1 — must remain legible (not flipped to a pale dark-fix value).
    expect(contrastRatio(resolveColorToRgb(spec.axes[0].labelColor)!, white)).toBeGreaterThanOrEqual(4.5);
  });
});

// ── D-276 (== D-228): native Vega JSON syntax repaired ───────────────────────
describe('D-276 tolerant parse recovers near-miss native Vega JSON', () => {
  const cases: Record<string, string> = {
    'trailing comma': '{"$schema":"https://vega.github.io/schema/vega/v5.json","marks":[],}',
    'single quotes':  "{'$schema':'https://vega.github.io/schema/vega/v5.json','marks':[]}",
    'json fence':     '```json\n{"$schema":"https://vega.github.io/schema/vega/v5.json","marks":[]}\n```',
  };
  for (const [name, raw] of Object.entries(cases)) {
    it(`recovers ${name} (plain JSON.parse throws on the raw text)`, () => {
      // Direction: JSON.parse of the raw model output fails (fence / trailing
      // comma / single quotes), so the recovery path is genuinely exercised.
      expect(() => JSON.parse(raw)).toThrow();
      const spec = tolerantParseVegaSpec(raw);
      expect(Array.isArray(spec.marks)).toBe(true);
    });
  }
});

// ── D-279 (== D-231): bespoke {scheme:'ziyaDark'} blanks the canvas (w4-14) ──
describe('D-279 unknown native colour scheme is dropped to Vega default', () => {
  it('deletes the scheme AND the emptied range so the default scheme applies', () => {
    const spec: any = {
      scales: [{ name: 'col', type: 'ordinal', domain: { data: 't', field: 'c' }, range: { scheme: 'ziyaDark' } }],
    };
    const dropped = sanitizeVegaSchemes(spec);
    expect(dropped).toBe(1);
    expect(spec.scales[0].range).toBeUndefined(); // emptied range removed (else itself fatal)
  });
  it('leaves a known scheme and a #hex range untouched', () => {
    const spec: any = {
      scales: [
        { name: 'a', type: 'ordinal', range: { scheme: 'category10' } },
        { name: 'b', type: 'linear', range: { scheme: '#ff0000' } },
      ],
    };
    expect(sanitizeVegaSchemes(spec)).toBe(0);
    expect(spec.scales[0].range).toEqual({ scheme: 'category10' });
  });
});

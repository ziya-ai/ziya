/**
 * G-39 / D-042 — Plotly preprocessor recovery: json / title / root-keys /
 * colour / alpha / size normalisation.
 *
 * This is a CONSOLIDATED REGRESSION GUARD, not a new fix. The D-042 defect
 * (16 plotly spec_ids) is already resolved in source by the previously-landed
 * D-230 (tolerant parse), D-231a (string-title -> {text}), D-231b (promote root
 * layout keys / demote trace-root marker keys), D-232 (invalid-colour-token ->
 * theme default, not library white), D-234 (four-arg rgb()->rgba()), D-235
 * (back-fill missing x), D-242 (category string axes) and D-238 (viewport dim
 * clamp) work — this iteration CONFIRMED all 16 against source and their real
 * on-disk spec definitions and found NO remaining gap, so no source change was
 * made (same outcome as D-019/D-020's already-in-source clusters).
 *
 * These tests therefore pass on the CURRENT tree by design; their value is to
 * FAIL if any of those recovery passes regress. They drive the ACTUAL failing
 * spec definitions captured under .ziya/gfx-sweep/specs/plotly through the real
 * parse + preprocess + theme pipeline and pin the corrected outcome. The one
 * theme-torn case (D-042 w4-10: an invalid layout colour token that used to
 * fall back to library WHITE on the dark page) is asserted in BOTH themes:
 * the previously-broken dark surface now resolves to the dark theme surface,
 * PAIRED with the light surface staying light — a per-theme resolution, not a
 * constant swap.
 */

import * as fs from 'fs';
import * as path from 'path';
import {
  parsePlotlyDefinition,
  preprocessPlotlySpec,
  clampLayoutDimensions,
  isValidColorToken,
} from '../plotlyPreprocessor';
import { applyPlotlyTheme } from '../plotlyPlugin';

const SPEC_DIR = path.resolve(__dirname, '../../../../../.ziya/gfx-sweep/specs/plotly');

function loadDefinition(id: string): string {
  const raw = fs.readFileSync(path.join(SPEC_DIR, `plotly-${id}.json`), 'utf8');
  return JSON.parse(raw).definition;
}
/** Full pipeline: tolerant parse -> preprocess (theme-independent passes). */
function pipeline(id: string): any {
  const parsed = parsePlotlyDefinition(loadDefinition(id));
  expect(parsed && typeof parsed === 'object').toBe(true);
  return preprocessPlotlySpec(parsed);
}

// ── D-230: tolerant parse of near-miss JSON dialects ─────────────────────────
describe('D-230 tolerant parse (recovery, both themes / theme-blind)', () => {
  const dialects: Array<[string, string]> = [
    ['w4-01', 'trailing commas'],
    ['w4-02', 'markdown ```json fence'],
    ['w4-03', 'unquoted object keys'],
    ['w4-04', 'single-quoted strings'],
    ['w4-05', 'smart/curly quotes'],
    ['w4-14', 'var= wrapper + // and /* */ comments + trailing ;'],
    ['w4-15', 'python repr: single quotes + None/True/False/nan'],
  ];
  it.each(dialects)('%s (%s) parses to a spec with a non-empty data[]', (id) => {
    // Direction: the raw definition is NOT strict JSON (JSON.parse throws).
    expect(() => JSON.parse(loadDefinition(id))).toThrow();
    const spec = pipeline(id);
    expect(Array.isArray(spec.data)).toBe(true);
    expect(spec.data.length).toBeGreaterThan(0);
  });

  it('w4-15 folds python literals to gaps (None/nan -> null/NaN), keeps bools', () => {
    const spec = pipeline('w4-15');
    const y = spec.data[0].y;
    expect(y[0]).toBe(2);
    expect(y[1]).toBeNull();          // None -> null
    expect(Number.isNaN(y[2] as any) || y[2] === 5).toBe(true);
    expect(y[3] === null || Number.isNaN(y[3] as any)).toBe(true); // nan -> NaN gap
    expect(spec.data[0].visible).toBe(true);      // True -> true
    expect(spec.data[0].connectgaps).toBe(false); // False -> false
  });
});

// ── D-231a: string-shorthand titles -> {text} ────────────────────────────────
describe('D-231a string titles (w2-01, w4-12)', () => {
  it('w2-01 layout.title string -> object {text}', () => {
    const spec = pipeline('w2-01');
    expect(typeof spec.layout.title).toBe('object');
    expect(typeof spec.layout.title.text).toBe('string');
  });
  it('w4-12 v1 titlefont folded into title.font at layout AND axis; titlefont removed', () => {
    const spec = pipeline('w4-12');
    expect(spec.layout.title.text).toBe('Deprecated v1 Dialect');
    expect(spec.layout.title.font.size).toBe(22); // titlefont.size folded in
    expect('titlefont' in spec.layout).toBe(false);
    expect(spec.layout.xaxis.title.text).toBe('time');
    expect(spec.layout.xaxis.title.font.size).toBe(16);
    expect('titlefont' in spec.layout.xaxis).toBe(false);
  });
});

// ── D-231b: promote root layout keys / demote trace-root marker keys ─────────
describe('D-231b nesting-depth off by one (w4-11)', () => {
  it('promotes title/xaxis/yaxis/showlegend into layout and demotes color/size into marker', () => {
    const spec = pipeline('w4-11');
    // root layout keys promoted (and the pre-existing layout.barmode preserved)
    expect(spec.layout.title.text).toBe('Nesting Depth Off By One');
    expect(spec.layout.xaxis.title.text).toBe('category');
    expect(spec.layout.yaxis.title.text).toBe('count');
    expect(spec.layout.showlegend).toBe(true);
    expect(spec.layout.barmode).toBe('group');
    // dead root copies stripped
    expect('title' in spec).toBe(false);
    expect('xaxis' in spec).toBe(false);
    // trace-root color/size demoted into marker; opacity (a valid trace attr) kept
    const t = spec.data[0];
    expect(t.marker.color).toBe('#4c78a8');
    expect(t.marker.size).toBe(12);
    expect('color' in t).toBe(false);
    expect('size' in t).toBe(false);
    expect(t.opacity).toBe(0.85);
  });
});

// ── D-232: invalid colour tokens (w4-10) — the ONE theme-torn recovery case ──
describe('D-232 invalid colour tokens (w4-10) — asserted in BOTH themes', () => {
  it('trace-level design-system tokens are stripped so plotly assigns a palette colour', () => {
    const spec = pipeline('w4-10');
    const t = spec.data[0];
    // 'var(--accent-color)' and 'primary' are NOT resolvable colours -> stripped
    expect(isValidColorToken('var(--accent-color)')).toBe(false);
    expect(isValidColorToken('primary')).toBe(false);
    expect(t.line.color).toBeUndefined();
    expect(t.marker.color).toBeUndefined();
  });

  it('DARK (was broken: white slab): invalid layout tokens resolve to the DARK theme surface', () => {
    const spec = pipeline('w4-10');
    const dark = applyPlotlyTheme(spec.layout, true);
    // pre-fix these tokens fell back to library WHITE on the dark page
    expect(dark.paper_bgcolor).toBe('#1e1e1e');
    expect(dark.plot_bgcolor).toBe('#1e1e1e');
    expect(dark.font.color).toBe('#e0e0e0');
    expect(dark.template).toBeUndefined();       // bogus 'plotly_dark_v2' dropped
    // gridcolor 'neutral-300' was invalid -> replaced with a real, legible colour
    expect(isValidColorToken(dark.xaxis.gridcolor)).toBe(true);
    expect(dark.xaxis.gridcolor).not.toBe('neutral-300');
  });

  it('LIGHT (still correct): the SAME tokens resolve to the LIGHT surface — a per-theme resolution, not a constant swap', () => {
    const spec = pipeline('w4-10');
    const light = applyPlotlyTheme(spec.layout, false);
    expect(light.paper_bgcolor).toBe('#ffffff');
    expect(light.plot_bgcolor).toBe('#ffffff');
    expect(light.font.color).toBe('#333333');
    expect(light.template).toBeUndefined();
    // light surface differs from dark surface -> resolved per theme
    const dark = applyPlotlyTheme(pipeline('w4-10').layout, true);
    expect(light.paper_bgcolor).not.toBe(dark.paper_bgcolor);
  });
});

// ── D-234: four-arg rgb(r,g,b,a) -> rgba (w4-08) ──────────────────────────────
describe('D-234 four-arg rgb alpha (w4-08, theme-blind)', () => {
  it('rewrites fillcolor rgb(r,g,b,a) to rgba(...) so the fill is translucent, not opaque', () => {
    const spec = pipeline('w4-08');
    expect(spec.data[0].fillcolor).toBe('rgba(214,39,40,0.2)');
    // a valid three-part rgba() elsewhere is left untouched
    expect(spec.data[0].line.color).toBe('rgba(214, 39, 40, 1.0)');
    // leading-dot alpha rgba(...,.35) is a valid colour token and is preserved
    expect(isValidColorToken('rgba(128,128,128,.35)')).toBe(true);
  });
});

// ── D-235: back-fill missing x on a categorical sibling (w4-13) ──────────────
describe('D-235 missing x back-fill (w4-13, theme-blind)', () => {
  it('the second trace inherits the first trace x so both series overlay the same categories', () => {
    const spec = pipeline('w4-13');
    expect(spec.data[0].x).toEqual(['a', 'b', 'c', 'd']);
    expect(spec.data[1].x).toEqual(spec.data[0].x); // was implicit 0,1,2,3 -> disjoint half
  });
});

// ── D-242: category string axes not date-coerced (w1-03) ─────────────────────
describe('D-242 category axis date-coercion (w1-03, theme-blind)', () => {
  it("heatmap y ['00-06','06-12',…] is forced type:'category' so it is not read as years", () => {
    const spec = pipeline('w1-03');
    expect(spec.layout.yaxis.type).toBe('category');
    expect(spec.layout.xaxis.type).toBe('category');
  });
});

// ── D-238: explicit oversize width/height clamped to the capture viewport ────
describe('D-238 explicit-size clamp (w2-10, w2-11, theme-blind geometry)', () => {
  it('w2-10 (4000x260) width clamps to the viewport, height preserved', () => {
    const spec = pipeline('w2-10');
    expect(spec.layout.width).toBe(4000); // preprocess keeps it; clamp is capture-gated
    const clamped = clampLayoutDimensions(spec.layout, 1280, 1024);
    expect(clamped.width).toBe(1280);
    expect(clamped.height).toBe(260);
  });
  it('w2-11 (240x2600) height clamps to the viewport, width preserved', () => {
    const spec = pipeline('w2-11');
    const clamped = clampLayoutDimensions(spec.layout, 1280, 1024);
    expect(clamped.width).toBe(240);
    expect(clamped.height).toBe(1024);
  });
});

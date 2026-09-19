/**
 * G-b594ca — vega-lite theme + legend reconciliation.
 *
 * D-263 (recovery, dialect-v2-schema-drops-legend, MISDIAGNOSED): the v2 $schema
 *   is stripped before embed and the bundled v5 compiler already honours
 *   legend.orient. The legend actually vanished because the redundant-colour-
 *   legend pass nulled an EXPLICITLY authored legend when the colour field
 *   duplicated the x field. Fix: hideRedundantColorLegend only suppresses when
 *   the author configured no legend at all.
 * D-317 (theme, authored-text-color-unreconciled): an authored text-MARK ink is
 *   reconciled against the effective canvas in BOTH themes.
 * D-319 (theme, authored-fill-vanishes-into-canvas): mark fills / scale ranges /
 *   gradient stops that dissolve into the canvas are reconciled.
 * D-318 (theme, text-on-mark-contrast, arc-inheritance sub-case): a layered arc
 *   text label that would inherit the shared series colour is pinned to a
 *   canvas-readable ink so it is not painted in its own slice's fill.
 *
 * Every theme assertion is made in BOTH the light and dark render themes.
 */
import {
  reconcileThemeColors,
  reconcileTextMarkColors,
  reconcileMarkFillsVsCanvas,
  reconcileInheritedArcLabelColors,
  reconcileFieldDrivenTextOnFill,
  fixBogusColorNameValues,
  resolveColorToRgb,
  contrastRatio,
  isIndistinguishableRange,
  SATURATED_CATEGORY_10,
} from '../vegaRecovery';
import { hideRedundantColorLegend } from '../vegaLitePlugin';

const cr = (a: string, b: string): number => {
  const ra = resolveColorToRgb(a)!, rb = resolveColorToRgb(b)!;
  return contrastRatio(ra, rb);
};
const LIGHT = '#ffffff';
const DARK = '#333333';
const clone = (o: any) => JSON.parse(JSON.stringify(o));

// ── D-263 ────────────────────────────────────────────────────────────────
describe('D-263 hideRedundantColorLegend preserves an explicitly authored legend', () => {
  // The w4-05 shape: colour field duplicates the x field, author asked for a top legend.
  const authored = () => ({
    encoding: {
      x: { field: 'g', type: 'nominal' },
      y: { field: 'y', type: 'quantitative' },
      color: { field: 'g', type: 'nominal', legend: { orient: 'top' } },
    },
  });

  it('keeps an explicit legend OBJECT (the w4-05 bug)', () => {
    const enc = authored().encoding;
    const hidden = hideRedundantColorLegend(enc);
    // pre-fix code returned/forced legend=null here; the fix must NOT hide it.
    expect(hidden).toBe(false);
    expect(enc.color.legend).toEqual({ orient: 'top' });
  });

  it('still suppresses a redundant legend the author never configured', () => {
    const enc: any = {
      x: { field: 'g' }, y: { field: 'y' },
      color: { field: 'g' }, // legend === undefined
    };
    const hidden = hideRedundantColorLegend(enc);
    expect(hidden).toBe(true);
    expect(enc.color.legend).toBeNull();
  });

  it('respects an explicit legend:null (author hid it) and non-redundant colour', () => {
    const nulled: any = { x: { field: 'g' }, color: { field: 'g', legend: null } };
    expect(hideRedundantColorLegend(nulled)).toBe(false);
    const distinct: any = { x: { field: 'a' }, color: { field: 'b' } };
    expect(hideRedundantColorLegend(distinct)).toBe(false);
    expect(distinct.color.legend).toBeUndefined();
  });
});

// ── D-317 ────────────────────────────────────────────────────────────────
describe('D-317 authored text-mark ink reconciled against the canvas in both themes', () => {
  // w3-01: #333333 text over an explicit white card. w3-02: #f0f0f0 text, no bg.
  const w301 = () => ({
    background: '#ffffff',
    layer: [
      { mark: { type: 'bar', fill: '#f4f4f4' } },
      { mark: { type: 'text', color: '#333333' }, encoding: { text: { field: 'v', type: 'quantitative' } } },
    ],
    encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } },
  });
  const w302 = () => ({
    layer: [
      { mark: { type: 'bar', fill: '#14213d' } },
      { mark: { type: 'text', color: '#f0f0f0' }, encoding: { text: { field: 'v', type: 'quantitative' } } },
    ],
    encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } },
  });

  it('w3-01: #333 label stays on the light card, becomes readable on the dark canvas', () => {
    const light = reconcileThemeColors(clone(w301()), false);
    const lightInk = light.layer[1].mark.color;
    expect(cr(lightInk, LIGHT)).toBeGreaterThanOrEqual(3); // stayed legible (~12.6:1)
    expect(lightInk).toBe('#333333');

    const dark = reconcileThemeColors(clone(w301()), true);
    // background of wrong polarity dropped -> canvas is #333; #333 ink would be 1:1.
    const darkInk = dark.layer[1].mark.color;
    expect(darkInk).not.toBe('#333333');
    expect(cr(darkInk, DARK)).toBeGreaterThanOrEqual(3);
  });

  it('w3-02: #f0f0f0 label stays on the dark canvas, becomes readable on the light canvas', () => {
    const dark = reconcileThemeColors(clone(w302()), true);
    expect(dark.layer[1].mark.color).toBe('#f0f0f0');
    expect(cr(dark.layer[1].mark.color, DARK)).toBeGreaterThanOrEqual(3);

    const light = reconcileThemeColors(clone(w302()), false);
    const lightInk = light.layer[1].mark.color;
    expect(lightInk).not.toBe('#f0f0f0');
    expect(cr(lightInk, LIGHT)).toBeGreaterThanOrEqual(3);
  });

  it('reconcileTextMarkColors leaves an already-legible ink untouched (no light regression)', () => {
    const spec = { mark: { type: 'text', color: '#000000' }, encoding: {} };
    reconcileTextMarkColors(spec, resolveColorToRgb(LIGHT)!, '#333333');
    expect(spec.mark.color).toBe('#000000');
  });
});

// ── D-319 ────────────────────────────────────────────────────────────────
describe('D-319 mark fills that vanish into the canvas are reconciled in both themes', () => {
  // w3-06: an all-pastel categorical scale.range; w3-07: gradient fills.
  const w306 = () => ({
    mark: { type: 'bar' },
    encoding: {
      x: { field: 'c', type: 'nominal' },
      y: { field: 'v', type: 'quantitative' },
      color: { field: 's', type: 'nominal', scale: { range: ['#fdf6e3', '#eee8d5', '#f5f5dc', '#faf0e6', '#fffaf0'] } },
    },
  });

  it('w3-06: an all-invisible pastel range on white is swapped for the saturated palette', () => {
    const light = reconcileThemeColors(clone(w306()), false);
    const range = light.encoding.color.scale.range;
    expect(range).toEqual(SATURATED_CATEGORY_10.slice(0, 5));
    // the pastels were all ~1.08:1 (invisible); the saturated swap lifts every
    // entry well clear of that (a categorical palette optimises for HUE
    // distinctness, so the floor is ~2.5:1 rather than the 3:1 text floor).
    for (const c of range) expect(cr(c, LIGHT)).toBeGreaterThanOrEqual(2.0);
    expect(Math.min(...range.map((c: string) => cr(c, LIGHT)))).toBeGreaterThan(cr('#fdf6e3', LIGHT));
  });

  it('D-504: w3-06 dark — the 5 creams are all VISIBLE on #333 yet mutually indistinguishable, so the range is swapped', () => {
    // Direction: every cream is 10-12:1 on the #333 dark card, so the
    // canvas-visibility swap does NOT fire; only the mutual-separability swap
    // does. On the pre-fix tree (no separability test) the dark range is
    // preserved and the 5 grouped series/legend swatches collapse together —
    // this assertion FAILS. With the fix the range is the saturated palette.
    const dark = reconcileThemeColors(clone(w306()), true);
    expect(dark.encoding.color.scale.range).toEqual(SATURATED_CATEGORY_10.slice(0, 5));
    // and the swapped palette is genuinely separable (a distinguishable pair).
    expect(isIndistinguishableRange(dark.encoding.color.scale.range)).toBe(false);
  });

  it('isIndistinguishableRange: flags the cream cluster, spares a real palette and a mixed range', () => {
    expect(isIndistinguishableRange(['#fdf6e3', '#eee8d5', '#f5f5dc', '#faf0e6', '#fffaf0'])).toBe(true);
    expect(isIndistinguishableRange(SATURATED_CATEGORY_10.slice(0, 5))).toBe(false);
    // even ONE distinguishable pair spares the whole range from a swap.
    expect(isIndistinguishableRange(['#fdf6e3', '#eee8d5', '#1f4e79'])).toBe(false);
    expect(isIndistinguishableRange(['#4572a7'])).toBe(false); // <2 entries: never
  });

  it('w3-07: an invisible gradient stop on the light canvas is nudged to >=3:1; a visible stop is kept', () => {
    const spec = {
      layer: [
        { mark: { type: 'bar', fill: { gradient: 'linear', stops: [{ offset: 0, color: '#f6f6f6' }, { offset: 1, color: '#4572a7' }] } } },
      ],
    };
    const light = reconcileThemeColors(clone(spec), false);
    const stops = light.layer[0].mark.fill.stops;
    expect(cr(stops[0].color, LIGHT)).toBeGreaterThanOrEqual(3); // #f6f6f6 was ~1.05:1
    expect(stops[1].color).toBe('#4572a7'); // already visible -> untouched
  });

  it('reconcileMarkFillsVsCanvas leaves a legible solid fill untouched (no regression)', () => {
    const spec: any = { mark: { type: 'bar', fill: '#4572a7' } };
    reconcileMarkFillsVsCanvas(spec, resolveColorToRgb(LIGHT)!, false);
    expect(spec.mark.fill).toBe('#4572a7');
  });
});

// ── D-318 (arc-inheritance sub-case) ───────────────────────────────────────
describe('D-318 layered arc labels do not inherit the series colour', () => {
  // w3-11 / w3-15: layered arc + text, top-level encoding.color shared to both.
  const w311 = () => ({
    layer: [
      { mark: { type: 'arc', innerRadius: 60, outerRadius: 110 } },
      { mark: { type: 'text', radius: 135 }, encoding: { text: { field: 'k', type: 'nominal' } } },
    ],
    encoding: { theta: { field: 'v', type: 'quantitative', stack: true }, color: { field: 'k', type: 'nominal' } },
  });

  it('pins an explicit readable label colour on the inheriting text layer, both themes', () => {
    for (const isDark of [false, true]) {
      const s = reconcileInheritedArcLabelColors(clone(w311()), isDark);
      const textLayer = s.layer[1];
      expect(textLayer.encoding.color).toBeDefined();
      // the label is now a fixed value, NOT the inherited {field:'k'} series scale.
      expect(textLayer.encoding.color.value).toBeDefined();
      expect(textLayer.encoding.color.field).toBeUndefined();
      const bg = isDark ? DARK : LIGHT;
      expect(cr(textLayer.encoding.color.value, bg)).toBeGreaterThanOrEqual(3);
    }
  });

  it('does not touch a text layer that pinned its OWN colour channel', () => {
    const spec: any = {
      layer: [
        { mark: { type: 'arc' } },
        { mark: { type: 'text' }, encoding: { color: { value: '#123456' } } },
      ],
      encoding: { theta: { field: 'v' }, color: { field: 'k' } },
    };
    reconcileInheritedArcLabelColors(spec, false);
    expect(spec.layer[1].encoding.color).toEqual({ value: '#123456' });
  });

  it('is a no-op for a non-arc layered spec', () => {
    const spec: any = {
      layer: [{ mark: { type: 'bar' } }, { mark: { type: 'text' }, encoding: {} }],
      encoding: { color: { field: 'k' } },
    };
    reconcileInheritedArcLabelColors(spec, false);
    expect(spec.layer[1].encoding.color).toBeUndefined();
  });
});

// ── D-318 (field-driven text-on-mark sub-case, w3-04) ──────────────────────
describe('D-318 field-driven value labels are reconciled against their own bar fill', () => {
  // w3-04: bar fill AND text ink are both data-driven literal colours (scale:null).
  // Every row's text was authored near-isoluminant with its bar (~1.0-1.1:1).
  const w304 = () => ({
    data: {
      values: [
        { c: 'dark-1', v: 60, f: '#1b2a41', t: '#222222' },
        { c: 'pale-1', v: 58, f: '#f7f7f2', t: '#ffffff' },
        { c: 'mid', v: 50, f: '#7f8c8d', t: '#7f8c8d' },
      ],
    },
    layer: [
      { mark: { type: 'bar' }, encoding: { color: { field: 'f', type: 'nominal', scale: null } } },
      {
        mark: { type: 'text' },
        encoding: {
          text: { field: 'v', type: 'quantitative' },
          color: { field: 't', type: 'nominal', scale: null },
        },
      },
    ],
    encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } },
  });

  // Theme-independent invariant: the label sits on the mark, so it must contrast
  // with its OWN (possibly canvas-reconciled) bar fill in both render themes —
  // AND (D-503) the bar fill itself must clear the graphical floor against the
  // canvas, so a pale/dark literal fill no longer vanishes.
  const CANVAS = { light: '#ffffff', dark: '#333333' } as const;
  it.each([['light', false], ['dark', true]] as const)(
    'each label contrasts >=3:1 with its own bar fill, and each fill >=3:1 on the %s canvas',
    (name, isDark) => {
      const s = reconcileThemeColors(clone(w304()), isDark);
      const rows = s.data.values;
      const bg = CANVAS[name];
      for (const row of rows) {
        expect(cr(row.t, row.f)).toBeGreaterThanOrEqual(3); // label vs its fill
        expect(cr(row.f, bg)).toBeGreaterThanOrEqual(3);     // fill vs canvas (D-503)
      }
      // the once-invisible-on-mark labels were reconciled away from their
      // isoluminant original (mid row #7f8c8d on itself was 1.00:1).
      expect(rows[2].t).not.toBe('#7f8c8d');
    },
  );

  it('leaves an already-legible on-bar label untouched (no over-correction)', () => {
    const spec: any = {
      data: { values: [{ f: '#14213d', t: '#ffffff' }] }, // white on navy ~15:1
      layer: [
        { mark: { type: 'bar' }, encoding: { color: { field: 'f', scale: null } } },
        { mark: { type: 'text' }, encoding: { color: { field: 't', scale: null } } },
      ],
    };
    reconcileFieldDrivenTextOnFill(spec);
    expect(spec.data.values[0].t).toBe('#ffffff');
  });

  it('does not hijack a genuinely categorical colour field (non-colour values)', () => {
    const spec: any = {
      data: { values: [{ f: '#1b2a41', k: 'Alpha' }] },
      layer: [
        { mark: { type: 'bar' }, encoding: { color: { field: 'f', scale: null } } },
        { mark: { type: 'text' }, encoding: { color: { field: 'k', type: 'nominal' } } },
      ],
    };
    reconcileFieldDrivenTextOnFill(spec);
    expect(spec.data.values[0].k).toBe('Alpha'); // 'Alpha' is not a colour -> left alone
  });
});

// ── D-319 (gradient survives preprocessing, w3-07) ─────────────────────────
describe('D-319 fixBogusColorNameValues preserves a real gradient object key', () => {
  // w3-07: a valid linear gradient fill. The old inline replace turned the KEY
  // "gradient" into "#4ecdc4", destroying the fill so the mark vanished.
  const w307 = () => ({
    layer: [
      {
        mark: {
          type: 'bar',
          fill: { gradient: 'linear', x1: 0, y1: 1, x2: 0, y2: 0, stops: [{ offset: 0, color: '#f6f6f6' }, { offset: 1, color: '#4572a7' }] },
        },
      },
      {
        mark: {
          type: 'point',
          fill: { gradient: 'radial', stops: [{ offset: 0, color: '#ffffff' }, { offset: 1, color: '#aa4643' }] },
        },
      },
    ],
    encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } },
  });

  it('keeps the "gradient" KEY intact (mark still has a gradient fill)', () => {
    const out = fixBogusColorNameValues(clone(w307()));
    expect(out.layer[0].mark.fill.gradient).toBe('linear');
    expect(out.layer[1].mark.fill.gradient).toBe('radial');
    // the fill object was NOT collapsed into a bogus-keyed object
    expect(out.layer[0].mark.fill['#4ecdc4']).toBeUndefined();
    expect(Array.isArray(out.layer[0].mark.fill.stops)).toBe(true);
  });

  it('still corrects a bogus colour-NAME VALUE like fill:"gradient"', () => {
    const out = fixBogusColorNameValues({ mark: { type: 'bar', fill: 'gradient' } });
    expect(out.mark.fill).toBe('#4ecdc4');
  });

  it('still corrects "rainbow"/"multicolor" values and "#green" names', () => {
    const out = fixBogusColorNameValues({
      a: { mark: { fill: 'rainbow' } },
      b: { mark: { fill: 'multicolor' } },
      c: { mark: { fill: '#green' } },
    });
    expect(out.a.mark.fill).toBe('#ff6b6b');
    expect(out.b.mark.fill).toBe('#45b7d1');
    expect(out.c.mark.fill).toBe('green');
  });
});

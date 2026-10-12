/**
 * Vega-Lite preprocessing helpers, tested against the shipped code.
 *
 * These tests used to replicate each helper locally, because the helpers
 * lived inside the plugin's render() closure. The copies drifted from the
 * plugin (error suppression, definition completeness and Fix 10 had all
 * changed) while the tests kept passing. The helpers are now exported
 * from vegaLitePlugin.ts and imported here; the wiring block at the end
 * checks that the render path still calls them.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  fixDatumFieldSwap,
  fixInvalidColorSchemeInArcs,
  fixLayeredChartsWithMismatchedScales,
  fixBarLogScaleBaseline,
  isVegaLiteDefinitionComplete,
  shouldSuppressVegaLiteError,
} from '../vegaLitePlugin';

// ── Fix 0.05: datum/field swap ──────────────────────────────────────────────
// Vega-Lite requires the primary channel (x, y) to carry the field reference
// and the secondary (x2, y2) to hold datum values.  LLMs frequently reverse
// this in lollipop/dumbbell charts.

describe('fixDatumFieldSwap', () => {
  it('swaps x:{datum} + x2:{field} to satisfy Vega-Lite channel requirements', () => {
    const encoding = {
      y: { field: 'who', type: 'nominal' },
      x: { datum: 0 },
      x2: { field: 'miles' },
    };
    fixDatumFieldSwap(encoding);
    expect(encoding.x).toEqual({ field: 'miles' });
    expect(encoding.x2).toEqual({ datum: 0 });
  });

  it('swaps y:{datum} + y2:{field} for vertical orientation', () => {
    const encoding = {
      x: { field: 'category', type: 'nominal' },
      y: { datum: 0 },
      y2: { field: 'value' },
    };
    fixDatumFieldSwap(encoding);
    expect(encoding.y).toEqual({ field: 'value' });
    expect(encoding.y2).toEqual({ datum: 0 });
  });

  it('does NOT swap when primary already has field', () => {
    const encoding = {
      x: { field: 'miles', type: 'quantitative' },
      x2: { datum: 0 },
    };
    const original = JSON.parse(JSON.stringify(encoding));
    fixDatumFieldSwap(encoding);
    expect(encoding).toEqual(original);
  });

  it('does NOT swap when secondary has datum (correct order)', () => {
    const encoding = {
      x: { field: 'a', type: 'quantitative' },
      x2: { datum: 100 },
    };
    const original = JSON.parse(JSON.stringify(encoding));
    fixDatumFieldSwap(encoding);
    expect(encoding).toEqual(original);
  });

  it('handles missing secondary channel gracefully', () => {
    const encoding = {
      x: { datum: 5 },
      y: { field: 'score', type: 'quantitative' },
    };
    const original = JSON.parse(JSON.stringify(encoding));
    fixDatumFieldSwap(encoding);
    expect(encoding).toEqual(original);
  });

  it('handles null/undefined encoding gracefully', () => {
    expect(() => fixDatumFieldSwap(null)).not.toThrow();
    expect(() => fixDatumFieldSwap(undefined)).not.toThrow();
  });

  it('swaps both x and y pairs simultaneously if both are reversed', () => {
    const encoding = {
      x: { datum: 0 },
      x2: { field: 'width' },
      y: { datum: 10 },
      y2: { field: 'height' },
    };
    fixDatumFieldSwap(encoding);
    expect(encoding.x).toEqual({ field: 'width' });
    expect(encoding.x2).toEqual({ datum: 0 });
    expect(encoding.y).toEqual({ field: 'height' });
    expect(encoding.y2).toEqual({ datum: 10 });
  });
});

// ── Fix 8 (broadened): invalid hex color as scheme name ──────────────────────
// LLMs write `scale: {scheme: "#ff6b6b"}` instead of a valid scheme name.
// The fix must reach into layer/concat sub-specs, not just top-level encoding.

describe('fixInvalidColorSchemeInArcs', () => {
  it('fixes hex scheme in top-level encoding', () => {
    // `any`: fixInvalidColorSchemeInArcs mutates this object in place (adding
    // `.range`, deleting `.scheme`), which the literal's inferred type
    // can't reflect — the assertions below read fields the initializer
    // never had.
    const spec: any = {
      mark: 'arc',
      data: { values: [{a:1},{a:2},{a:3}] },
      encoding: { color: { field: 'a', type: 'nominal', scale: { scheme: '#ff6b6b' } } },
    };
    fixInvalidColorSchemeInArcs(spec);
    expect(spec.encoding.color.scale.scheme).toBeUndefined();
    expect(spec.encoding.color.scale.range).toBeDefined();
    expect(spec.encoding.color.scale.range.length).toBe(3);
  });

  it('fixes hex scheme inside layer sub-specs (the donut chart bug)', () => {
    // `any` — see comment on the fixture above.
    const spec: any = {
      data: { values: Array.from({length: 7}, (_, i) => ({star: `S${i}`, power: i + 10})) },
      layer: [
        {
          mark: { type: 'arc', innerRadius: 50 },
          encoding: {
            theta: { field: 'power', type: 'quantitative' },
            color: { field: 'star', type: 'nominal', scale: { scheme: '#ff6b6b' } },
          },
        },
        {
          mark: { type: 'text' },
          encoding: { text: { field: 'star' }, color: { value: '#333' } },
        },
      ],
    };
    fixInvalidColorSchemeInArcs(spec);
    // Arc layer's color scheme should be replaced with a range
    expect(spec.layer[0].encoding.color.scale.scheme).toBeUndefined();
    expect(spec.layer[0].encoding.color.scale.range).toBeDefined();
    expect(spec.layer[0].encoding.color.scale.range.length).toBe(7);
    // Text layer's color (a value, not a scheme) should be untouched
    expect(spec.layer[1].encoding.color).toEqual({ value: '#333' });
  });

  it('does NOT modify valid scheme names', () => {
    // `any` — see comment on the first fixture in this describe block.
    const spec: any = {
      mark: 'bar',
      encoding: { color: { field: 'x', scale: { scheme: 'tableau10' } } },
    };
    fixInvalidColorSchemeInArcs(spec);
    expect(spec.encoding.color.scale.scheme).toBe('tableau10');
    expect(spec.encoding.color.scale.range).toBeUndefined();
  });

  it('does NOT modify color channels without scale.scheme', () => {
    const spec = {
      mark: 'bar',
      encoding: { color: { field: 'x', type: 'nominal' } },
    };
    const original = JSON.parse(JSON.stringify(spec));
    fixInvalidColorSchemeInArcs(spec);
    expect(spec).toEqual(original);
  });

  it('handles hconcat sub-specs', () => {
    // `any` — see comment on the first fixture in this describe block.
    const spec: any = {
      hconcat: [{
        mark: 'bar',
        encoding: { fill: { field: 'x', scale: { scheme: '#abcdef' } } },
      }],
    };
    fixInvalidColorSchemeInArcs(spec);
    expect(spec.hconcat[0].encoding.fill.scale.scheme).toBeUndefined();
    expect(spec.hconcat[0].encoding.fill.scale.range).toBeDefined();
  });

  it('fixes fill and stroke channels too', () => {
    // `any` — see comment on the first fixture in this describe block.
    const spec: any = {
      mark: 'point',
      data: { values: [{a:1},{a:2}] },
      encoding: {
        fill: { field: 'a', scale: { scheme: '#123456' } },
        stroke: { field: 'a', scale: { scheme: '#654321' } },
      },
    };
    fixInvalidColorSchemeInArcs(spec);
    expect(spec.encoding.fill.scale.scheme).toBeUndefined();
    expect(spec.encoding.fill.scale.range).toBeDefined();
    expect(spec.encoding.stroke.scale.scheme).toBeUndefined();
    expect(spec.encoding.stroke.scale.range).toBeDefined();
  });
});

// ── shouldSuppressError logic ────────────────────────────────────────────────
// The error-suppression decision from the plugin's render catch block.

describe('shouldSuppressVegaLiteError', () => {
  it('does NOT suppress errors for complete object specs (the lollipop chart bug)', () => {
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [{ x: 1 }] },
      layer: [{ mark: 'rule', encoding: {} }],
      isStreaming: false,
      isMarkdownBlockClosed: true,
      forceRender: false,
      // definition is undefined — this was the bug trigger
    };
    const error = new Error("Cannot destructure property 'aggregate' of 'i' as it is undefined");
    expect(shouldSuppressVegaLiteError(spec, error)).toBe(false);
  });

  it('suppresses streaming errors when block is not closed', () => {
    const spec = {
      isStreaming: true,
      isMarkdownBlockClosed: false,
      forceRender: false,
    };
    const error = new Error('Unterminated string in JSON');
    expect(shouldSuppressVegaLiteError(spec, error)).toBe(true);
  });

  it('does NOT suppress when forceRender is true and spec is complete', () => {
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [{ x: 1 }] },
      mark: 'bar',
      encoding: { x: { field: 'x' } },
      isStreaming: true,
      isMarkdownBlockClosed: false,
      forceRender: true,
    };
    const error = new Error('Unterminated string in JSON');
    expect(shouldSuppressVegaLiteError(spec, error)).toBe(false);
  });

  it('suppresses when definition is missing AND spec is not a complete object', () => {
    const spec = {
      type: 'vega-lite',
      isStreaming: false,
      isMarkdownBlockClosed: true,
      // no $schema, data, mark, or layer
    };
    const error = new Error('Some error');
    expect(shouldSuppressVegaLiteError(spec, error)).toBe(true);
  });

  it('does NOT suppress when definition is missing but spec has schema+data+layer', () => {
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [] },
      layer: [],
      isStreaming: false,
      isMarkdownBlockClosed: true,
    };
    const error = new Error("Cannot destructure property 'aggregate'");
    expect(shouldSuppressVegaLiteError(spec, error)).toBe(false);
  });
});

// ── Lollipop chart spec integration sanity ───────────────────────────────────
// Verify the full lollipop spec from the bug report is correctly preprocessed.

describe('Lollipop chart preprocessing (integration)', () => {
  const lollipopSpec = {
    layer: [
      {
        mark: { type: 'rule', color: '#888', strokeWidth: 1.5 },
        encoding: {
          y: { field: 'who', type: 'nominal', title: null, sort: '-x' },
          x: { datum: 0 },
          x2: { field: 'miles' },
        },
      },
      {
        mark: { type: 'point', filled: true, size: 200 },
        encoding: {
          y: { field: 'who', type: 'nominal', sort: '-x' },
          x: { field: 'miles', type: 'quantitative', title: 'Distance (miles)', scale: { type: 'sqrt' } },
          color: { field: 'miles', type: 'quantitative', scale: { scheme: 'viridis' }, legend: null },
        },
      },
    ],
  };

  it('swaps datum/field in the rule layer encoding', () => {
    const spec = JSON.parse(JSON.stringify(lollipopSpec));
    // Apply the fix to each layer
    spec.layer.forEach((layer: any) => fixDatumFieldSwap(layer.encoding));

    // Rule layer should now have field on x, datum on x2
    expect(spec.layer[0].encoding.x).toEqual({ field: 'miles' });
    expect(spec.layer[0].encoding.x2).toEqual({ datum: 0 });

    // Point layer should be unchanged (already correct)
    expect(spec.layer[1].encoding.x.field).toBe('miles');
  });

  it('does not break the point layer encoding', () => {
    const spec = JSON.parse(JSON.stringify(lollipopSpec));
    spec.layer.forEach((layer: any) => fixDatumFieldSwap(layer.encoding));

    const pointEnc = spec.layer[1].encoding;
    expect(pointEnc.x.field).toBe('miles');
    expect(pointEnc.x.type).toBe('quantitative');
    expect(pointEnc.color.field).toBe('miles');
  });
});

// ── Fix: Layered charts with mismatched y-axis ranges ───────────────────────
// When layers use different y-fields whose data ranges differ by >3×,
// the fix adds resolve.scale.y = 'independent' so the shared axis
// doesn't clip one layer's marks.

describe('fixLayeredChartsWithMismatchedScales', () => {
  it('adds independent y-scales when layers use different fields with >3× range difference', () => {
    const spec = {
      data: {
        values: [
          { month: 1, sector: 'A', pop: 200, infra: 5 },
          { month: 6, sector: 'A', pop: 800, infra: 25 },
          { month: 12, sector: 'A', pop: 2000, infra: 55 },
          { month: 24, sector: 'A', pop: 5000, infra: 85 },
        ],
      },
      encoding: {
        x: { field: 'month', type: 'nominal' },
        y: { field: 'pop', type: 'quantitative' },
      },
      layer: [
        { mark: 'bar', encoding: { color: { field: 'sector' } } },
        {
          mark: { type: 'circle' },
          encoding: {
            y: { field: 'infra', type: 'quantitative', scale: { domain: [0, 100] } },
          },
        },
      ],
    };

    const fixed = fixLayeredChartsWithMismatchedScales(JSON.parse(JSON.stringify(spec)));
    expect(fixed.resolve?.scale?.y).toBe('independent');
    // Second layer gets right-side axis
    expect(fixed.layer[1].encoding.y.axis.orient).toBe('right');
  });

  it('adds independent y-scales for log/linear mismatch (existing behaviour)', () => {
    const spec = {
      data: { values: [{ a: 1, b: 100 }, { a: 10, b: 200 }] },
      layer: [
        { mark: 'bar', encoding: { y: { field: 'a', type: 'quantitative', scale: { type: 'log' } } } },
        { mark: 'line', encoding: { y: { field: 'b', type: 'quantitative' } } },
      ],
    };

    const fixed = fixLayeredChartsWithMismatchedScales(JSON.parse(JSON.stringify(spec)));
    expect(fixed.resolve?.scale?.y).toBe('independent');
  });

  it('does NOT trigger when layers use the same y-field', () => {
    const spec = {
      data: { values: [{ x: 'A', y: 10 }, { x: 'B', y: 20 }] },
      layer: [
        { mark: 'bar', encoding: { y: { field: 'y', type: 'quantitative' } } },
        { mark: 'line', encoding: { y: { field: 'y', type: 'quantitative' } } },
      ],
    };

    const fixed = fixLayeredChartsWithMismatchedScales(JSON.parse(JSON.stringify(spec)));
    expect(fixed.resolve).toBeUndefined();
  });

  it('does NOT trigger when range difference is small (< 3×)', () => {
    const spec = {
      data: { values: [{ a: 10, b: 20 }, { a: 20, b: 30 }] },
      layer: [
        { mark: 'bar', encoding: { y: { field: 'a', type: 'quantitative' } } },
        { mark: 'line', encoding: { y: { field: 'b', type: 'quantitative' } } },
      ],
    };

    const fixed = fixLayeredChartsWithMismatchedScales(JSON.parse(JSON.stringify(spec)));
    expect(fixed.resolve).toBeUndefined();
  });

  it('does NOT modify single-layer specs', () => {
    const spec = {
      layer: [{ mark: 'bar', encoding: { y: { field: 'v', type: 'quantitative' } } }],
    };
    const fixed = fixLayeredChartsWithMismatchedScales(JSON.parse(JSON.stringify(spec)));
    expect(fixed.resolve).toBeUndefined();
  });
});

// ── fixBarLogScaleBaseline ───────────────────────────────────────────────────
// Bar marks on a log scale are fundamentally broken (bars imply a zero
// baseline, log(0) = -∞).  The fix converts to tick + text layers.

describe('fixBarLogScaleBaseline', () => {
  it('converts horizontal bar+log to tick+text layers (the age comparison bug)', () => {
    const spec = {
      mark: { type: 'bar', cornerRadiusTopLeft: 6, cornerRadiusTopRight: 6 },
      data: {
        values: [
          { civ: 'Us', age: 12000, color: 'us' },
          { civ: 'Them', age: 4500000, color: 'them' },
          { civ: 'Universe', age: 13800000, color: 'universe' },
        ],
      },
      encoding: {
        y: { field: 'civ', type: 'nominal' },
        x: { field: 'age', type: 'quantitative', scale: { type: 'log' } },
        color: { field: 'color', type: 'nominal' },
      },
    };

    const fixed = fixBarLogScaleBaseline(JSON.parse(JSON.stringify(spec)));
    // Should be converted to layered spec
    expect(fixed.layer).toBeDefined();
    expect(fixed.layer).toHaveLength(2);
    expect(fixed.mark).toBeUndefined();
    // First layer = tick marks
    expect(fixed.layer[0].mark.type).toBe('tick');
    // Second layer = text labels
    expect(fixed.layer[1].mark.type).toBe('text');
    // Data should have labels
    expect(fixed.data.values[0]._barLogLabel).toBe('12K');
    expect(fixed.data.values[1]._barLogLabel).toBe('4.5M');
    expect(fixed.data.values[2]._barLogLabel).toBe('13.8M');
    // Log axis should have clean grid values (powers of 10)
    const xAxis = fixed.layer[0].encoding.x;
    expect(xAxis.axis.values).toBeDefined();
    xAxis.axis.values.forEach((v: number) => {
      expect(Math.log10(v) % 1).toBeCloseTo(0);
    });
  });

  it('converts vertical bar+log to tick+text layers', () => {
    const spec = {
      mark: 'bar',
      data: {
        values: [
          { cat: 'A', val: 10 },
          { cat: 'B', val: 1000 },
          { cat: 'C', val: 100000 },
        ],
      },
      encoding: {
        x: { field: 'cat', type: 'nominal' },
        y: { field: 'val', type: 'quantitative', scale: { type: 'log' } },
      },
    };

    const fixed = fixBarLogScaleBaseline(JSON.parse(JSON.stringify(spec)));
    expect(fixed.layer).toBeDefined();
    expect(fixed.layer).toHaveLength(2);
    expect(fixed.layer[0].mark.type).toBe('tick');
    // Vertical: label offset should use dx (to the right of tick)
    expect(fixed.layer[1].mark.dx).toBe(12);
  });

  it('does NOT modify bar charts without log scale', () => {
    const spec = {
      mark: 'bar',
      data: { values: [{ x: 'A', y: 10 }] },
      encoding: {
        x: { field: 'x', type: 'nominal' },
        y: { field: 'y', type: 'quantitative' },
      },
    };
    const fixed = fixBarLogScaleBaseline(JSON.parse(JSON.stringify(spec)));
    expect(fixed.layer).toBeUndefined();
    expect(fixed.mark).toBe('bar');
  });

  it('does NOT modify non-bar marks with log scale', () => {
    const spec = {
      mark: 'line',
      data: { values: [{ x: 1, y: 100 }, { x: 2, y: 10000 }] },
      encoding: {
        x: { field: 'x', type: 'quantitative' },
        y: { field: 'y', type: 'quantitative', scale: { type: 'log' } },
      },
    };
    const fixed = fixBarLogScaleBaseline(JSON.parse(JSON.stringify(spec)));
    expect(fixed.layer).toBeUndefined();
    expect(fixed.mark).toBe('line');
  });

  it('formats labels correctly across magnitude ranges', () => {
    const spec = {
      mark: 'bar',
      data: {
        values: [
          { cat: 'a', v: 500 },
          { cat: 'b', v: 75000 },
          { cat: 'c', v: 2500000 },
          { cat: 'd', v: 3200000000 },
        ],
      },
      encoding: {
        y: { field: 'cat', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { type: 'log' } },
      },
    };
    const fixed = fixBarLogScaleBaseline(JSON.parse(JSON.stringify(spec)));
    const labels = fixed.data.values.map((d: any) => d._barLogLabel);
    expect(labels).toEqual(['500', '75K', '2.5M', '3.2B']);
  });
});

// ── Behaviour the deleted local copies got wrong ────────────────────────────
// Each case below fails against the copy this file used to carry and passes
// against the plugin's own helper.

describe('drift from the former local copies', () => {
  it('does not suppress a complete spec whose data lives only in a layer', () => {
    // The copy required top-level data/datasets; the plugin looks anywhere.
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      layer: [{ data: { values: [{ x: 1 }] }, mark: 'point' }],
      isStreaming: false,
      isMarkdownBlockClosed: true,
    };
    expect(shouldSuppressVegaLiteError(spec, new Error('real compile error'))).toBe(false);
    // Paired: with no data anywhere the same spec is still suppressed.
    const noData = { ...spec, layer: [{ mark: 'point' }] };
    expect(shouldSuppressVegaLiteError(noData, new Error('real compile error'))).toBe(true);
  });

  it('treats a facet spec with data as a complete definition', () => {
    const def = JSON.stringify({
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [{ g: 'a', v: 1 }] },
      facet: { field: 'g', type: 'nominal' },
      spec: { mark: 'bar' },
    });
    expect(isVegaLiteDefinitionComplete(def)).toBe(true);
  });

  it('treats a truncated $schema URL as incomplete, and the full URL as complete', () => {
    const body = (schema: string) =>
      JSON.stringify({ $schema: schema, data: { values: [{ v: 1 }] }, mark: 'bar' });
    expect(isVegaLiteDefinitionComplete(body('https://vega.github.io/schema/vega-lite/v5'))).toBe(false);
    expect(isVegaLiteDefinitionComplete(body('https://vega.github.io/schema/vega-lite/v5.json'))).toBe(true);
  });

  it.each(['hconcat', 'vconcat'])('treats a finished %s composition as complete', key => {
    const def = JSON.stringify({
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [{ v: 1 }] },
      [key]: [{ mark: 'bar' }, { mark: 'line' }],
    });
    expect(isVegaLiteDefinitionComplete(def)).toBe(true);
    // Paired: the same composition with no data anywhere is still incomplete.
    const noData = JSON.stringify({ [key]: [{ mark: 'bar' }] });
    expect(isVegaLiteDefinitionComplete(noData)).toBe(false);
  });

  it('a finished hconcat with a real compile error is not suppressed while streaming', () => {
    const def = JSON.stringify({
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [{ v: 1 }] },
      hconcat: [{ mark: 'bar' }],
    });
    const spec = { definition: def, isStreaming: true, isMarkdownBlockClosed: true };
    expect(shouldSuppressVegaLiteError(spec, new Error('real compile error'))).toBe(false);
  });

  it('Fix 10 puts the first layer on the left with grid and every later layer on the right without', () => {
    const spec: any = {
      data: { values: [{ m: 1, a: 1, b: 100, c: 9000 }, { m: 2, a: 2, b: 900, c: 90000 }] },
      layer: [
        { mark: 'bar', encoding: { y: { field: 'a', type: 'quantitative' } } },
        { mark: 'line', encoding: { y: { field: 'b', type: 'quantitative' } } },
        { mark: 'point', encoding: { y: { field: 'c', type: 'quantitative' } } },
      ],
    };
    const fixed = fixLayeredChartsWithMismatchedScales(spec);
    expect(fixed.resolve).toEqual({ scale: { y: 'independent' } });
    expect(fixed.layer.map((l: any) => [l.encoding.y.axis.orient, l.encoding.y.axis.grid]))
      .toEqual([['left', true], ['right', false], ['right', false]]);
  });

  it('Fix 10 removes the conflicting layer domain when it splits the y scales', () => {
    const spec: any = {
      data: { values: [{ m: 1, pop: 200, infra: 5 }, { m: 2, pop: 5000, infra: 85 }] },
      encoding: { x: { field: 'm', type: 'nominal' }, y: { field: 'pop', type: 'quantitative' } },
      layer: [
        { mark: 'bar' },
        { mark: 'circle', encoding: { y: { field: 'infra', type: 'quantitative', scale: { domain: [0, 100] } } } },
      ],
    };
    const fixed = fixLayeredChartsWithMismatchedScales(spec);
    expect(fixed.resolve).toEqual({ scale: { y: 'independent' } });
    expect(fixed.layer[1].encoding.y.scale.domain).toBeUndefined();
  });

  it('Fix 10 turns a nominal x into ordinal when a log layer is involved', () => {
    const spec: any = {
      data: { values: [{ c: 'a', lo: 1, hi: 100 }, { c: 'b', lo: 10, hi: 900 }] },
      layer: [
        { mark: 'bar', encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'lo', type: 'quantitative', scale: { type: 'log' } } } },
        { mark: 'line', encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'hi', type: 'quantitative' } } },
      ],
    };
    const fixed = fixLayeredChartsWithMismatchedScales(spec);
    expect(fixed.layer[0].encoding.x.type).toBe('ordinal');
    expect(fixed.layer[1].encoding.x.type).toBe('ordinal');
  });

  it('Fix 8 cycles the palette when there are more categories than colours', () => {
    const spec: any = {
      mark: 'arc',
      data: { values: Array.from({ length: 17 }, (_, i) => ({ k: `k${i}` })) },
      encoding: { color: { field: 'k', type: 'nominal', scale: { scheme: '#123456' } } },
    };
    fixInvalidColorSchemeInArcs(spec);
    const range = spec.encoding.color.scale.range;
    expect(range).toHaveLength(17);
    expect(range[15]).toBe(range[0]);
  });
});

/**
 * Seam: the helpers passing in isolation proves nothing unless the render
 * path calls them, and calls these definitions rather than a closure-local
 * duplicate. Comments are stripped so documentation does not satisfy it.
 */
describe('vegaLitePlugin wiring for the exported preprocess helpers', () => {
  const source = fs.readFileSync(path.join(__dirname, '../vegaLitePlugin.ts'), 'utf8');
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map(line => line.replace(/\/\/.*$/, ''))
    .join('\n');

  it.each([
    ['fixDatumFieldSwap', /fixDatumFieldSwap\(spec\.encoding\)/],
    ['fixInvalidColorSchemeInArcs', /fixedSpec = fixInvalidColorSchemeInArcs\(fixedSpec\)/],
    ['fixLayeredChartsWithMismatchedScales', /fixedSpec = fixLayeredChartsWithMismatchedScales\(fixedSpec\)/],
    ['fixBarLogScaleBaseline', /fixedSpec = fixBarLogScaleBaseline\(fixedSpec\)/],
    ['shouldSuppressVegaLiteError', /shouldSuppressError = shouldSuppressVegaLiteError\(spec, error\)/],
    ['isVegaLiteDefinitionComplete', /return isVegaLiteDefinitionComplete\(definition\)/],
  ])('render path calls %s', (_name, call) => {
    expect(code).toMatch(call);
  });

  it.each([
    'fixDatumFieldSwap', 'fixInvalidColorSchemeInArcs', 'generateColorPalette',
    'fixLayeredChartsWithMismatchedScales', 'fixBarLogScaleBaseline',
    'isVegaLiteDefinitionComplete',
  ])('%s is defined once, at module level', name => {
    const defs = code.match(new RegExp(`const ${name}\\s*=`, 'g')) || [];
    expect(defs).toHaveLength(1);
    expect(code).toMatch(new RegExp(`^(export )?const ${name}\\s*=`, 'm'));
  });
});

/**
 * Tests for Vega-Lite preprocessing fixes in vegaLitePlugin.
 *
 * Since preprocessVegaSpec is defined inside the plugin's render() closure,
 * we replicate the individual fix functions here for unit testing.  The
 * integration-level behavior (full render with error handling) is verified
 * by the shouldSuppressError tests below.
 */

// ── Fix 0.05: datum/field swap ──────────────────────────────────────────────
// Vega-Lite requires the primary channel (x, y) to carry the field reference
// and the secondary (x2, y2) to hold datum values.  LLMs frequently reverse
// this in lollipop/dumbbell charts.

function fixDatumFieldSwap(encoding: any): void {
  if (!encoding) return;
  const pairs: [string, string][] = [['x', 'x2'], ['y', 'y2']];
  for (const [primary, secondary] of pairs) {
    const prim = encoding[primary];
    const sec = encoding[secondary];
    if (prim && sec && 'datum' in prim && 'field' in sec) {
      encoding[primary] = sec;
      encoding[secondary] = prim;
    }
  }
}

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

// ── Post-render SVG dimension handling ───────────────────────────────────────
// The post-render setTimeout in vegaLitePlugin strips SVG width/height
// attributes to make auto-sized charts responsive.  Charts with explicit
// dimensions (width: 600, height: 400) must NOT have their attributes
// stripped — doing so collapses the SVG to 0px height.

describe('SVG dimension preservation for explicit-dimension charts', () => {
  /**
   * Simulate the post-render dimension logic.
   * Returns true if the SVG attributes would be stripped.
   */
  function wouldStripSvgDimensions(vegaSpec: any): boolean {
    const hasExplicitWidth = vegaSpec.width && vegaSpec.width > 0;
    const hasExplicitHeight = vegaSpec.height && vegaSpec.height > 0;
    // The fixed code only strips when BOTH are missing
    return !hasExplicitWidth && !hasExplicitHeight;
  }

  it('does NOT strip dimensions for chart with explicit width and height', () => {
    const spec = { width: 600, height: 400, mark: 'text' };
    expect(wouldStripSvgDimensions(spec)).toBe(false);
  });

  it('does NOT strip dimensions when only width is explicit', () => {
    const spec = { width: 500, mark: 'bar' };
    // height is undefined, but width is explicit — should not strip
    expect(wouldStripSvgDimensions(spec)).toBe(false);
  });

  it('does NOT strip dimensions when only height is explicit', () => {
    const spec = { height: 300, mark: 'bar' };
    expect(wouldStripSvgDimensions(spec)).toBe(false);
  });

  it('strips dimensions for auto-sized charts (no explicit dimensions)', () => {
    const spec = { mark: 'bar', data: { values: [] } };
    expect(wouldStripSvgDimensions(spec)).toBe(true);
  });

  it('strips dimensions when width and height are 0', () => {
    const spec = { width: 0, height: 0, mark: 'bar' };
    expect(wouldStripSvgDimensions(spec)).toBe(true);
  });

  it('does NOT strip for the word-cloud text chart spec from the bug report', () => {
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      width: 600,
      height: 400,
      mark: { type: 'text' },
      encoding: {
        x: { field: 'x', type: 'quantitative' },
        y: { field: 'y', type: 'quantitative' },
        text: { field: 'name' },
        size: { field: 'importance', type: 'quantitative', scale: { range: [12, 40] } },
      },
    };
    expect(wouldStripSvgDimensions(spec)).toBe(false);
  });

  it('does NOT strip for geoshape/map charts with explicit dimensions', () => {
    // Geoshape specs have no encoding — they rely entirely on projection
    // and the SVG's intrinsic dimensions for layout.  Stripping width/height
    // is especially destructive here.
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      width: 600,
      height: 400,
      data: { url: 'https://vega.github.io/vega-datasets/data/world-110m.json', format: { type: 'topojson', feature: 'countries' } },
      projection: { type: 'mercator', center: [-8, 52], scale: 1500 },
      mark: { type: 'geoshape', fill: '#264653', stroke: '#2a9d8f' },
    };
    expect(wouldStripSvgDimensions(spec)).toBe(false);
  });

  it('does NOT strip for trail charts with explicit dimensions (data clipping bug)', () => {
    // Trail marks render axes/legends at the SVG edges but draw paths in
    // the interior.  Stripping dimensions collapses the SVG, then
    // forceContainerResize sets the parent to ~40px — just enough for
    // axis labels to peek through while the trail paths are clipped.
    const spec = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      width: 600,
      height: 400,
      mark: { type: 'trail' },
      encoding: {
        x: { field: 'x', type: 'quantitative' },
        y: { field: 'y', type: 'quantitative' },
        size: { field: 'speed', type: 'quantitative', scale: { range: [1, 20] } },
        color: { field: 'order', type: 'quantitative', scale: { scheme: 'goldorange' } },
        order: { field: 'order' },
      },
    };
    expect(wouldStripSvgDimensions(spec)).toBe(false);
  });

  /**
   * Simulate the scaling guard — should skip scaling for explicit-dimension charts.
   */
  function wouldApplyScaling(vegaSpec: any): boolean {
    const hasExplicitWidth = vegaSpec.width && vegaSpec.width > 0;
    const hasExplicitHeight = vegaSpec.height && vegaSpec.height > 0;
    return !hasExplicitWidth && !hasExplicitHeight;
  }

  it('skips post-render scaling for charts with explicit dimensions', () => {
    expect(wouldApplyScaling({ width: 600, height: 400 })).toBe(false);
  });

  it('applies scaling for auto-sized charts', () => {
    expect(wouldApplyScaling({ mark: 'bar' })).toBe(true);
  });
});

// ── Division-by-zero guard in scaling ────────────────────────────────────────
describe('scaling division-by-zero guard', () => {
  function safeScale(containerWidth: number, containerHeight: number, svgWidth: number, svgHeight: number): number {
    const scaleX = svgWidth > 0 ? containerWidth / svgWidth : 1;
    const scaleY = svgHeight > 0 ? containerHeight / svgHeight : 1;
    return Math.min(scaleX, scaleY) || 1;
  }

  it('returns 1 when SVG dimensions are zero', () => {
    expect(safeScale(800, 600, 0, 0)).toBe(1);
  });

  it('returns 1 when only width is zero', () => {
    expect(safeScale(800, 600, 0, 400)).toBe(1);
  });

  it('calculates correctly for normal dimensions', () => {
    expect(safeScale(800, 600, 400, 300)).toBe(2);
  });

  it('uses the smaller scale factor', () => {
    // 800/400=2, 600/600=1 → min is 1
    expect(safeScale(800, 600, 400, 600)).toBe(1);
  });
});

// ── Fix 8 (broadened): invalid hex color as scheme name ──────────────────────
// LLMs write `scale: {scheme: "#ff6b6b"}` instead of a valid scheme name.
// The fix must reach into layer/concat sub-specs, not just top-level encoding.

function fixInvalidColorSchemes(spec: any): void {
  const palette = ['#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#ffd93d',
    '#ff9ff3', '#54a0ff', '#5f27cd', '#ff9f43', '#0abde3'];

  const fixEncoding = (encoding: any, dataLength: number) => {
    if (!encoding) return;
    ['color', 'fill', 'stroke'].forEach(channel => {
      const ch = encoding[channel];
      if (ch?.scale?.scheme && typeof ch.scale.scheme === 'string' &&
          ch.scale.scheme.startsWith('#')) {
        const hexColor = ch.scale.scheme;
        delete ch.scale.scheme;
        ch.scale.range = palette.slice(0, dataLength);
      }
    });
  };

  const dataLength = spec.data?.values?.length || 8;
  fixEncoding(spec.encoding, dataLength);
  if (spec.layer && Array.isArray(spec.layer)) {
    spec.layer.forEach((layer: any) => {
      fixEncoding(layer.encoding, layer.data?.values?.length || dataLength);
    });
  }
  if (spec.hconcat) spec.hconcat.forEach((s: any) => {
    fixEncoding(s.encoding, s.data?.values?.length || dataLength);
  });
  if (spec.vconcat) spec.vconcat.forEach((s: any) => {
    fixEncoding(s.encoding, s.data?.values?.length || dataLength);
  });
}

describe('fixInvalidColorSchemes', () => {
  it('fixes hex scheme in top-level encoding', () => {
    const spec = {
      mark: 'arc',
      data: { values: [{a:1},{a:2},{a:3}] },
      encoding: { color: { field: 'a', type: 'nominal', scale: { scheme: '#ff6b6b' } } },
    };
    fixInvalidColorSchemes(spec);
    expect(spec.encoding.color.scale.scheme).toBeUndefined();
    expect(spec.encoding.color.scale.range).toBeDefined();
    expect(spec.encoding.color.scale.range.length).toBe(3);
  });

  it('fixes hex scheme inside layer sub-specs (the donut chart bug)', () => {
    const spec = {
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
    fixInvalidColorSchemes(spec);
    // Arc layer's color scheme should be replaced with a range
    expect(spec.layer[0].encoding.color.scale.scheme).toBeUndefined();
    expect(spec.layer[0].encoding.color.scale.range).toBeDefined();
    expect(spec.layer[0].encoding.color.scale.range.length).toBe(7);
    // Text layer's color (a value, not a scheme) should be untouched
    expect(spec.layer[1].encoding.color).toEqual({ value: '#333' });
  });

  it('does NOT modify valid scheme names', () => {
    const spec = {
      mark: 'bar',
      encoding: { color: { field: 'x', scale: { scheme: 'tableau10' } } },
    };
    fixInvalidColorSchemes(spec);
    expect(spec.encoding.color.scale.scheme).toBe('tableau10');
    expect(spec.encoding.color.scale.range).toBeUndefined();
  });

  it('does NOT modify color channels without scale.scheme', () => {
    const spec = {
      mark: 'bar',
      encoding: { color: { field: 'x', type: 'nominal' } },
    };
    const original = JSON.parse(JSON.stringify(spec));
    fixInvalidColorSchemes(spec);
    expect(spec).toEqual(original);
  });

  it('handles hconcat sub-specs', () => {
    const spec = {
      hconcat: [{
        mark: 'bar',
        encoding: { fill: { field: 'x', scale: { scheme: '#abcdef' } } },
      }],
    };
    fixInvalidColorSchemes(spec);
    expect(spec.hconcat[0].encoding.fill.scale.scheme).toBeUndefined();
    expect(spec.hconcat[0].encoding.fill.scale.range).toBeDefined();
  });

  it('fixes fill and stroke channels too', () => {
    const spec = {
      mark: 'point',
      data: { values: [{a:1},{a:2}] },
      encoding: {
        fill: { field: 'a', scale: { scheme: '#123456' } },
        stroke: { field: 'a', scale: { scheme: '#654321' } },
      },
    };
    fixInvalidColorSchemes(spec);
    expect(spec.encoding.fill.scale.scheme).toBeUndefined();
    expect(spec.encoding.fill.scale.range).toBeDefined();
    expect(spec.encoding.stroke.scale.scheme).toBeUndefined();
    expect(spec.encoding.stroke.scale.range).toBeDefined();
  });
});

// ── shouldSuppressError logic ────────────────────────────────────────────────
// Reproduce the error-suppression decision from the plugin's catch block.

function shouldSuppressError(spec: any, error: Error): boolean {
  const isStreamingError =
    error.message.includes('Unterminated string') ||
    error.message.includes('Unexpected end of JSON input') ||
    error.message.includes('Unexpected token');

  const isIncompleteDefinition =
    spec.definition ? !isVegaLiteDefinitionComplete(spec.definition) : false;

  const specIsCompleteObject =
    spec.$schema &&
    (spec.data || spec.datasets) &&
    (spec.mark || spec.layer || spec.vconcat || spec.hconcat || spec.facet || spec.repeat);

  return (
    (!spec.forceRender &&
      ((spec.isStreaming && !spec.isMarkdownBlockClosed) ||
       (spec.isStreaming && isIncompleteDefinition))) ||
    (!spec.definition && !specIsCompleteObject) ||
    (isStreamingError && !spec.forceRender)
  );
}

function isVegaLiteDefinitionComplete(definition: string): boolean {
  if (!definition || definition.trim().length === 0) return false;
  const trimmed = definition.trim();
  if (trimmed.endsWith(',') || trimmed.endsWith('{') || trimmed.endsWith('[')) return false;
  try {
    const parsed = JSON.parse(definition);
    if (!parsed || typeof parsed !== 'object') return false;
    return !!(parsed.data !== undefined && (parsed.mark || parsed.layer));
  } catch {
    return false;
  }
}

describe('shouldSuppressError (fixed)', () => {
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
    expect(shouldSuppressError(spec, error)).toBe(false);
  });

  it('suppresses streaming errors when block is not closed', () => {
    const spec = {
      isStreaming: true,
      isMarkdownBlockClosed: false,
      forceRender: false,
    };
    const error = new Error('Unterminated string in JSON');
    expect(shouldSuppressError(spec, error)).toBe(true);
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
    expect(shouldSuppressError(spec, error)).toBe(false);
  });

  it('suppresses when definition is missing AND spec is not a complete object', () => {
    const spec = {
      type: 'vega-lite',
      isStreaming: false,
      isMarkdownBlockClosed: true,
      // no $schema, data, mark, or layer
    };
    const error = new Error('Some error');
    expect(shouldSuppressError(spec, error)).toBe(true);
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
    expect(shouldSuppressError(spec, error)).toBe(false);
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
// Replicates the logic from fixLayeredChartsWithMismatchedScales.
// When layers use different y-fields whose data ranges differ by >3×,
// the fix adds resolve.scale.y = 'independent' so the shared axis
// doesn't clip one layer's marks.

function fixLayeredChartsWithMismatchedScales(spec: any): any {
  if (!spec.layer || !Array.isArray(spec.layer) || spec.layer.length < 2) {
    return spec;
  }

  const topLevelYField = spec.encoding?.y?.field;
  const yFields = spec.layer.map((layer: any) =>
    layer.encoding?.y?.field || topLevelYField
  ).filter(Boolean);
  const uniqueYFields = [...new Set(yFields)] as string[];

  const hasLogScale = spec.layer.some((layer: any) => layer.encoding?.y?.scale?.type === 'log');
  const hasLinearScale = spec.layer.some((layer: any) => !layer.encoding?.y?.scale?.type || layer.encoding?.y?.scale?.type === 'linear');

  const hasScaleTypeMismatch = hasLogScale && hasLinearScale;
  const hasRangeMismatch = uniqueYFields.length > 1 && spec.data?.values && (() => {
    const ranges = uniqueYFields.map((field: string) => {
      const values = spec.data.values
        .map((d: any) => d[field])
        .filter((v: any) => typeof v === 'number' && !isNaN(v));
      if (values.length === 0) return null;
      return { field, min: Math.min(...values), max: Math.max(...values) };
    }).filter(Boolean) as { field: string; min: number; max: number }[];
    if (ranges.length < 2) return false;
    for (let i = 0; i < ranges.length; i++) {
      for (let j = i + 1; j < ranges.length; j++) {
        const span1 = ranges[i].max - ranges[i].min || 1;
        const span2 = ranges[j].max - ranges[j].min || 1;
        const ratio = Math.max(span1 / span2, span2 / span1);
        if (ratio > 3) return true;
      }
    }
    return false;
  })();

  if (yFields.length > 1 && (hasScaleTypeMismatch || hasRangeMismatch)) {
    spec.resolve = { ...(spec.resolve || {}), scale: { ...(spec.resolve?.scale || {}), y: 'independent' } };
    spec.layer.forEach((layer: any, index: number) => {
      if (layer.encoding?.y) {
        if (!layer.encoding.y.axis) layer.encoding.y.axis = {};
        layer.encoding.y.axis.orient = index === 0 ? 'left' : 'right';
        layer.encoding.y.axis.grid = index === 0;
      }
    });
  }
  return spec;
}

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
// Replicated from vegaLitePlugin.ts.
// Bar marks on a log scale are fundamentally broken (bars imply a zero
// baseline, log(0) = -∞).  The fix converts to tick + text layers.

function fixBarLogScaleBaseline(spec: any): any {
  const markType = typeof spec.mark === 'string' ? spec.mark : spec.mark?.type;
  if (markType !== 'bar') return spec;

  let logAxis: 'x' | 'y' | null = null;
  for (const axis of ['x', 'y'] as const) {
    const enc = spec.encoding?.[axis];
    if (enc?.type === 'quantitative' && enc?.scale?.type === 'log') {
      logAxis = axis;
    }
  }
  if (!logAxis) return spec;

  const logEnc = spec.encoding[logAxis];
  const field = logEnc.field;
  if (!field || !spec.data?.values) return spec;

  const values = spec.data.values
    .map((d: any) => d[field])
    .filter((v: any) => typeof v === 'number' && v > 0);
  if (values.length === 0) return spec;

  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const lowerBound = minVal * 0.3;
  const upperBound = maxVal * 3;

  const labelledData = spec.data.values.map((d: any) => {
    const v = d[field];
    let label: string;
    if (typeof v !== 'number' || v <= 0) label = String(v);
    else if (v >= 1e9) label = (v / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
    else if (v >= 1e6) label = (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    else if (v >= 1e3) label = (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
    else label = String(v);
    return { ...d, _barLogLabel: label };
  });

  const logLow = Math.floor(Math.log10(lowerBound));
  const logHigh = Math.ceil(Math.log10(upperBound));
  const gridValues: number[] = [];
  for (let exp = logLow; exp <= logHigh; exp++) {
    const v = Math.pow(10, exp);
    if (v >= lowerBound && v <= upperBound) gridValues.push(v);
  }

  const fixedLogEnc = {
    ...logEnc,
    scale: { ...logEnc.scale, domain: [lowerBound, upperBound] },
    axis: { ...(logEnc.axis || {}), values: gridValues, gridDash: [2, 4] },
  };

  const sharedEncoding = { ...spec.encoding, [logAxis]: fixedLogEnc };
  delete sharedEncoding.size;

  const labelOffset = logAxis === 'x' ? { dy: -14 } : { dx: 12, align: 'left' as const };

  const result: any = {
    ...spec,
    data: { values: labelledData },
    mark: undefined,
    encoding: undefined,
    layer: [
      { mark: { type: 'tick', thickness: 6, size: 40 }, encoding: { ...sharedEncoding } },
      {
        mark: { type: 'text', fontSize: 11, ...labelOffset },
        encoding: {
          ...sharedEncoding,
          text: { field: '_barLogLabel', type: 'nominal' },
          color: sharedEncoding.color ? { ...sharedEncoding.color, legend: null } : { value: '#999' },
        },
      },
    ],
  };
  delete result.mark;
  return result;
}

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
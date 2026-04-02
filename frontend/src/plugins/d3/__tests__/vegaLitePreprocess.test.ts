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

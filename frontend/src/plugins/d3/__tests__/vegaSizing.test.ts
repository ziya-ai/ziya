/**
 * Vega-Lite width/autosize resolution.
 *
 * Regression coverage for the "chart renders small and centered with wasted
 * whitespace on both sides" bug. Root cause was twofold:
 *   1. vegaLitePlugin was the only registered diagram plugin with no
 *      sizingConfig, so D3Renderer's isFlexible fallback (false) pinned the
 *      container to the 600px width prop and centered it.
 *   2. An authored "width": 600 in the spec was honoured verbatim, so even a
 *      full-width container got a 600px chart.
 *
 * These tests exercise the real exported helpers, not a re-implementation.
 */
import {
  applyHeightFloor,
  applySizing,
  isCompositeSpec,
  resolveAutosize,
  resolveSpecWidth,
} from '../vegaSizing';

const CONTAINER_W = 1200;

describe('isCompositeSpec', () => {
  it.each(['vconcat', 'hconcat', 'concat', 'facet', 'repeat'])(
    'treats %s as composite', (key) => {
      expect(isCompositeSpec({ [key]: [{ mark: 'bar' }] })).toBe(true);
    });

  it('treats a simple single-view spec as non-composite', () => {
    expect(isCompositeSpec({ mark: 'line', encoding: {} })).toBe(false);
  });

  it('treats a layered (but not composite) spec as non-composite', () => {
    // layer shares one set of scales/dimensions, so container width is fine.
    expect(isCompositeSpec({ layer: [{ mark: 'bar' }] })).toBe(false);
  });

  it('is safe on null / non-object input', () => {
    expect(isCompositeSpec(null)).toBe(false);
    expect(isCompositeSpec(undefined)).toBe(false);
    expect(isCompositeSpec('nope')).toBe(false);
  });
});

describe('resolveSpecWidth', () => {
  it("replaces an authored fixed pixel width with 'container'", () => {
    // THE bug: this spec shape rendered as a 600px centered chart.
    const spec = { mark: 'line', width: 600 };
    expect(resolveSpecWidth(spec, CONTAINER_W)).toBe('container');
  });

  it("uses 'container' when no width is authored", () => {
    expect(resolveSpecWidth({ mark: 'bar' }, CONTAINER_W)).toBe('container');
  });

  it("leaves an already-'container' width alone", () => {
    expect(resolveSpecWidth({ mark: 'bar', width: 'container' }, CONTAINER_W))
      .toBe('container');
  });

  it('ignores the measured container width for simple specs', () => {
    // Returning availableWidth here would reintroduce a fixed pixel width.
    expect(resolveSpecWidth({ mark: 'bar' }, 640)).not.toBe(640);
  });

  it('keeps an authored numeric width for composite specs', () => {
    // Vega-Lite requires a concrete per-sub-view width for these.
    const spec = { vconcat: [{ mark: 'bar' }], width: 300 };
    expect(resolveSpecWidth(spec, CONTAINER_W)).toBe(300);
  });

  it('falls back to the measured width for concat specs with no width', () => {
    // Concat sub-views each span the full width, so the container measurement
    // IS the right per-sub-view value. Faceted specs are not in this class —
    // their top-level width sizes a single cell. See vegaFacetLayout.test.ts.
    const spec = { vconcat: [{ mark: 'bar' }] };
    expect(resolveSpecWidth(spec, CONTAINER_W)).toBe(CONTAINER_W);
  });

  it('resolves a channel-faceted spec to a per-cell width, not container', () => {
    // THE reported bug: encoding.row went unrecognised, so this returned
    // 'container' and Vega-Lite emitted "Width 'container' only works for
    // single views and layered views" once per cell plus once for the spec.
    const spec = {
      mark: 'bar',
      encoding: { row: { field: 'dim' }, x: { field: 'score' } },
    };
    const width = resolveSpecWidth(spec, CONTAINER_W);
    expect(width).not.toBe('container');
    expect(typeof width).toBe('number');
    expect(width as number).toBeLessThan(CONTAINER_W);
  });

  it('never returns a non-positive or non-finite composite width', () => {
    for (const bad of [0, -50, NaN, Infinity]) {
      const got = resolveSpecWidth({ vconcat: [], width: bad }, CONTAINER_W);
      expect(got).toBe(CONTAINER_W);
    }
  });

  it('does not mutate the input spec', () => {
    const spec = { mark: 'line', width: 600 };
    resolveSpecWidth(spec, CONTAINER_W);
    expect(spec.width).toBe(600);
  });
});

describe('resolveAutosize', () => {
  it("pairs 'container' width with fit-x/padding", () => {
    // Vega-Lite warns that width:'container' "only works well with autosize
    // 'fit' or 'fit-x'". 'pad' adds axis/label extent outside a plot area
    // already sized to the full container, so the view overflows.
    expect(resolveAutosize({}, 'container'))
      .toEqual({ type: 'fit-x', contains: 'padding' });
  });

  it("overrides an authored 'pad' autosize when width is 'container'", () => {
    // A spec authored with pad + container width overflows its container;
    // the resolver corrects the pairing rather than honouring it.
    const spec = { autosize: { type: 'pad', contains: 'padding' } };
    expect(resolveAutosize(spec, 'container'))
      .toEqual({ type: 'fit-x', contains: 'padding' });
  });

  it("overrides an authored 'fit' for a pixel (composite) width", () => {
    // Verified against vega-lite 6.4: a composite spec carrying autosize 'fit'
    // warns "Autosize 'fit' only works for single views and layered views" and
    // is silently rewritten to 'pad'. Returning 'fit' asked for something
    // Vega-Lite would never honour, at the cost of a warning on every spec.
    const spec = { autosize: { type: 'fit', contains: 'content' } };
    expect(resolveAutosize(spec, 800))
      .toEqual({ type: 'pad', contains: 'padding' });
  });

  it("keeps an authored 'pad' autosize for a pixel width", () => {
    // 'pad' is accepted for composite specs, so the authored value stands.
    const spec = { autosize: { type: 'pad', contains: 'content' } };
    expect(resolveAutosize(spec, 800))
      .toEqual({ type: 'pad', contains: 'content' });
  });

  it('defaults to pad/padding for a pixel width with no authored autosize', () => {
    expect(resolveAutosize({}, 800))
      .toEqual({ type: 'pad', contains: 'padding' });
  });

  it('copies rather than aliases an authored autosize object', () => {
    const authored = { type: 'pad', contains: 'content' };
    const out = resolveAutosize({ autosize: authored }, 800);
    expect(out).not.toBe(authored);
    expect(out).toEqual(authored);
  });
});

describe('applySizing', () => {
  it('resolves the exact reported spec to container width + pad autosize', () => {
    // Verbatim shape from the bug report: explicit 600x400 + fit/content.
    const spec: any = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      mark: { type: 'line', point: true },
      encoding: { x: { field: 'night' }, y: { field: 'rows' } },
      width: 600,
      height: 400,
      autosize: { type: 'fit', contains: 'content' },
    };

    const report = applySizing(spec, CONTAINER_W);

    expect(spec.width).toBe('container');
    expect(spec.autosize).toEqual({ type: 'fit-x', contains: 'padding' });
    expect(report.replacedWidth).toBe(600);
    // Height is untouched — it remains a concrete pixel value by design.
    expect(spec.height).toBe(400);
  });

  it('reports no replacement when width was not authored numerically', () => {
    const spec: any = { mark: 'bar' };
    expect(applySizing(spec, CONTAINER_W).replacedWidth).toBeNull();
  });

  it('reports no replacement when a composite width is preserved', () => {
    const spec: any = { vconcat: [{ mark: 'bar' }], width: 300 };
    const report = applySizing(spec, CONTAINER_W);
    expect(spec.width).toBe(300);
    expect(report.replacedWidth).toBeNull();
  });

  it('always leaves width and autosize mutually compatible', () => {
    // The invariant that makes the container-width change safe.
    const specs: any[] = [
      { mark: 'bar' },
      { mark: 'bar', width: 600 },
      { mark: 'bar', width: 'container' },
      { layer: [{ mark: 'bar' }], width: 900 },
      { vconcat: [{ mark: 'bar' }] },
      { vconcat: [{ mark: 'bar' }], width: 300 },
      { facet: { field: 'a' }, spec: { mark: 'bar' }, width: 250 },
      { mark: 'bar', encoding: { row: { field: 'a' }, x: { field: 'b' } } },
      { mark: 'bar', encoding: { column: { field: 'a' }, x: { field: 'b' } } },
      { mark: 'bar', encoding: { facet: { field: 'a' }, x: { field: 'b' } } },
    ];
    for (const spec of specs) {
      applySizing(spec, CONTAINER_W);
      if (spec.width === 'container') {
        expect(spec.autosize).toEqual({ type: 'fit-x', contains: 'padding' });
      } else {
        // Composite specs get a pixel width and 'pad' — the only autosize
        // Vega-Lite accepts there; a 'fit' variant warns and is rewritten.
        expect(typeof spec.width).toBe('number');
        expect(spec.autosize.type).toBe('pad');
      }
    }
  });

  it('writes the facet operator cell width where Vega-Lite reads it', () => {
    // Measured: a row-faceted operator spec given width:1160 at the TOP level
    // assembled to 463px, i.e. the value was discarded and the default cell
    // width used. The width has to land on the inner `spec`.
    const spec: any = {
      facet: { row: { field: 'dim' } },
      spec: { mark: 'bar', encoding: { x: { field: 'score' } } },
    };
    const report = applySizing(spec, CONTAINER_W);

    expect(report.widthTarget).toBe('spec');
    expect(typeof spec.spec.width).toBe('number');
    expect(spec.spec.width).toBe(report.width);
    // Mirrored, not moved: vega-embed injects its own width option when
    // spec.width is absent, which would overwrite the cell width above.
    expect(spec.width).toBe(report.width);
  });

  it('does not treat a yOffset-grouped single view as composite', () => {
    // The positive counterpart: the single-view rewrite of the reported chart
    // must still get container width, or the fix would have over-reached.
    const spec: any = {
      mark: 'bar',
      encoding: {
        y: { field: 'dim' },
        yOffset: { field: 'tool' },
        x: { field: 'score' },
      },
    };
    applySizing(spec, CONTAINER_W);
    expect(spec.width).toBe('container');
    expect(spec.autosize).toEqual({ type: 'fit-x', contains: 'padding' });
  });

  it('is idempotent', () => {
    const spec: any = { mark: 'line', width: 600 };
    applySizing(spec, CONTAINER_W);
    const first = { width: spec.width, autosize: { ...spec.autosize } };
    const second = applySizing(spec, CONTAINER_W);
    expect(spec.width).toBe(first.width);
    expect(spec.autosize).toEqual(first.autosize);
    // Second pass has nothing left to replace.
    expect(second.replacedWidth).toBeNull();
  });
});

describe('applyHeightFloor (D-240 authored-height honoured)', () => {
  // D-240: authored top-level heights 40 and 28 (sparkline / wide-and-short
  // aspect ratios) were inflated to ~300px, destroying the requested ratio.
  // The floor must fire ONLY for a height the plugin defaulted, never one the
  // author supplied. The old code ran `if (height < 250) height = 300`
  // unconditionally, so these authored-preservation assertions fail against
  // that pre-D-267 behaviour and pass with the wasAuthored short-circuit.
  it.each([40, 28, 1, 249])(
    'honours an authored small height (%dpx) verbatim',
    (h) => {
      expect(applyHeightFloor(h, /* wasAuthored */ true)).toBe(h);
    },
  );

  it('honours an authored height at/above the floor verbatim', () => {
    expect(applyHeightFloor(600, true)).toBe(600);
    expect(applyHeightFloor(250, true)).toBe(250);
  });

  it('floors a DEFAULTED small height up to the flooredTo value', () => {
    // A height the plugin itself defaulted from a short container is a squashed
    // plot and is still corrected.
    expect(applyHeightFloor(240, false)).toBe(300);
    expect(applyHeightFloor(40, false)).toBe(300);
  });

  it('leaves a DEFAULTED height at/above the floor untouched', () => {
    expect(applyHeightFloor(250, false)).toBe(250);
    expect(applyHeightFloor(500, false)).toBe(500);
  });
});

describe('D-261 authored-height preserved end-to-end for the sweep specs', () => {
  // gfx-sweep G-5b9ba1 / D-261: the recorded verdict shows authored top-level
  // heights 40 (vega-lite-w2-06) and 28 (vega-lite-w2-08) rendering at ~305px,
  // destroying the wide-and-short / sparkline aspect ratio. The suspect was
  // applyHeightFloor firing on an authored height. This reproduces the exact
  // plugin sequence — capture _heightWasAuthored BEFORE any default, run
  // applySizing (which resolves width/autosize but must NOT touch height), then
  // apply the floor with that flag — and pins that the authored height survives.
  //
  // Direction: these assert the wasAuthored short-circuit; against the pre-D-267
  // unconditional `if (height < 250) height = 300` floor the 40px/28px cases
  // would come back as 300 and fail. w2-08 additionally proves the authored
  // fixed WIDTH (45) is still swapped for 'container' without perturbing height.
  const runPluginHeightPath = (spec: any, containerW: number): number => {
    const heightWasAuthored =
      typeof spec.height === 'number' && spec.height > 0;
    applySizing(spec, containerW); // mutates width/autosize in place
    if (spec.height) {
      spec.height = applyHeightFloor(spec.height, heightWasAuthored);
    }
    return spec.height;
  };

  it('w2-06: honours authored height 40 (no width) and uses container width', () => {
    const spec: any = { mark: 'line', height: 40 };
    const h = runPluginHeightPath(spec, 1180);
    expect(h).toBe(40);
    expect(spec.height).toBe(40);
    expect(spec.width).toBe('container');
    expect(spec.autosize).toEqual({ type: 'fit-x', contains: 'padding' });
  });

  it('w2-08: honours authored height 28 and replaces the authored width 45', () => {
    const spec: any = { mark: 'bar', width: 45, height: 28 };
    const h = runPluginHeightPath(spec, 1180);
    expect(h).toBe(28);
    expect(spec.height).toBe(28);
    // A fixed pixel width is never used for an inline chart.
    expect(spec.width).toBe('container');
    expect(spec.autosize).toEqual({ type: 'fit-x', contains: 'padding' });
  });
});

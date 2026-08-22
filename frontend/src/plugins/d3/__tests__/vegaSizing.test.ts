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

  it('falls back to the measured width for composite specs with no width', () => {
    const spec = { facet: { field: 'a' }, spec: { mark: 'bar' } };
    expect(resolveSpecWidth(spec, CONTAINER_W)).toBe(CONTAINER_W);
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

  it('keeps an authored autosize for a pixel (composite) width', () => {
    const spec = { autosize: { type: 'fit', contains: 'content' } };
    expect(resolveAutosize(spec, 800))
      .toEqual({ type: 'fit', contains: 'content' });
  });

  it('defaults to fit/content for a pixel width with no authored autosize', () => {
    expect(resolveAutosize({}, 800))
      .toEqual({ type: 'fit', contains: 'content' });
  });

  it('copies rather than aliases an authored autosize object', () => {
    const authored = { type: 'fit', contains: 'content' };
    const out = resolveAutosize({ autosize: authored }, 800);
    expect(out).not.toBe(authored);
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
    ];
    for (const spec of specs) {
      applySizing(spec, CONTAINER_W);
      if (spec.width === 'container') {
        expect(spec.autosize).toEqual({ type: 'fit-x', contains: 'padding' });
      } else {
        expect(typeof spec.width).toBe('number');
        expect(spec.autosize.type).not.toBe('pad');
      }
    }
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

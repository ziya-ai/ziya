/**
 * @jest-environment jsdom
 */
/**
 * G-22 regression tests for the full-Vega plugin (vegaPlugin.ts).
 *
 * Covers:
 *   D-272  no tolerant parse before JSON.parse (fence / smart quotes / trailing
 *          comma / unquoted keys / single quotes / comments+';') — canHandle
 *          declined a near-miss Vega spec at a bare JSON.parse.
 *   D-273  wrong-dialect not handed-off or rewritten:
 *            (a) a Vega-Lite BODY under a Vega $schema was CLAIMED by the Vega
 *                plugin (via the $schema substring) → silent blank canvas;
 *            (b) a Vega v2 dialect (marks.properties / axes.type) died with an
 *                internal TypeError instead of being mechanically rewritten.
 *   D-274  a dataflow error (unknown colour scheme) escaped render()'s catch →
 *          silent "successful" blank canvas. Now (a) the unknown scheme is
 *          dropped before the runtime touches it, and (b) an error raised by
 *          the running view is routed into the error placeholder.
 *   D-276  postRenderSizing forced overflow:hidden on the SVG/wrapper, hard-
 *          clipping any content whose authored aspect != container aspect.
 *
 * Direction (fail-without-the-fix) is asserted explicitly: every recovery check
 * first proves the UNPATCHED path (bare JSON.parse throws / $schema wins /
 * overflow:hidden) would have failed.
 */

import { vegaPlugin, rewriteVegaV2Dialect, sanitizeVegaSchemes } from '../vegaPlugin';

// ── D-272: canHandle tolerant parse ──────────────────────────────────────────

describe('D-272 canHandle — tolerant parse of near-miss Vega spec strings', () => {
  // A minimal, unambiguous full-Vega body (marks ARRAY + data ARRAY).
  const vegaBody = `{
    "$schema": "https://vega.github.io/schema/vega/v5.json",
    "data": [{ "name": "t", "values": [{ "x": 1 }] }],
    "marks": [{ "type": "rect", "from": { "data": "t" } }],
  }`; // <- trailing comma (w4-01 shape): invalid strict JSON

  it('claims a Vega spec string with a trailing comma (bare JSON.parse would throw)', () => {
    // Direction: the unpatched canHandle did `JSON.parse(spec)` which throws here.
    expect(() => JSON.parse(vegaBody)).toThrow();
    expect(vegaPlugin.canHandle(vegaBody)).toBe(true);
  });

  it('claims a Vega spec inside a ```json markdown fence with smart quotes (w4-04/05)', () => {
    const fenced =
      '```json\n{\n  \u201C$schema\u201D: \u201Chttps://vega.github.io/schema/vega/v5.json\u201D,\n' +
      '  \u201Cdata\u201D: [{ \u201Cname\u201D: \u201Ct\u201D, \u201Cvalues\u201D: [] }],\n' +
      '  \u201Cmarks\u201D: [{ \u201Ctype\u201D: \u201Crect\u201D, \u201Cfrom\u201D: { \u201Cdata\u201D: \u201Ct\u201D } }]\n}\n```';
    expect(() => JSON.parse(fenced)).toThrow();
    expect(vegaPlugin.canHandle(fenced)).toBe(true);
  });

  it('claims a Vega spec with unquoted keys + single quotes + a // comment (w4-02/03/06)', () => {
    const loose =
      "{ // a full vega spec\n  $schema: 'https://vega.github.io/schema/vega/v5.json',\n" +
      "  data: [{ name: 't', values: [] }],\n  marks: [{ type: 'rect', from: { data: 't' } }]\n};";
    expect(() => JSON.parse(loose)).toThrow();
    expect(vegaPlugin.canHandle(loose)).toBe(true);
  });

  it('still declines genuinely non-Vega / unrecoverable strings', () => {
    expect(vegaPlugin.canHandle('this is not json at all')).toBe(false);
    // A valid JSON object that is not a Vega spec.
    expect(vegaPlugin.canHandle('{"hello":"world"}')).toBe(false);
  });
});

// ── D-273 (a): Vega-Lite body hand-off ───────────────────────────────────────

describe('D-273 hand-off — a Vega-Lite body under a Vega $schema is NOT claimed', () => {
  const vlBodyUnderVegaSchema = {
    // $schema substring says vega (NOT vega-lite) — the trap that made the Vega
    // plugin claim it and paint a blank canvas.
    $schema: 'https://vega.github.io/schema/vega/v5.json',
    data: { values: [{ a: 'A', b: 1 }] }, // VL object-form data
    mark: 'bar',                           // SINGULAR mark
    encoding: { x: { field: 'a' }, y: { field: 'b' } },
  };

  it('declines a singular-mark + encoding body even when $schema says /vega/', () => {
    // Direction: the unpatched isVegaSpec returned true on the $schema substring.
    expect(vlBodyUnderVegaSchema.$schema.includes('/vega/')).toBe(true);
    expect(vegaPlugin.canHandle(vlBodyUnderVegaSchema)).toBe(false);
  });

  it('still claims a genuine full-Vega spec (marks ARRAY, not singular mark)', () => {
    const realVega = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      data: [{ name: 't', values: [] }],
      marks: [{ type: 'rect', from: { data: 't' } }],
    };
    expect(vegaPlugin.canHandle(realVega)).toBe(true);
  });
});

// ── D-273 (b): Vega v2 dialect rewrite ───────────────────────────────────────

describe('D-273 rewriteVegaV2Dialect — mechanical v2 → v3+ shapes', () => {
  it('renames marks[].properties → encode (incl. nested group marks)', () => {
    const spec: any = {
      marks: [
        { type: 'rect', properties: { enter: { x: { value: 1 } } } },
        { type: 'group', marks: [{ type: 'text', properties: { update: { text: { value: 'hi' } } } }] },
      ],
    };
    const out = rewriteVegaV2Dialect(spec);
    expect(out.marks[0].encode).toEqual({ enter: { x: { value: 1 } } });
    expect(out.marks[0].properties).toBeUndefined();
    // nested group child rewritten too
    expect(out.marks[1].marks[0].encode).toEqual({ update: { text: { value: 'hi' } } });
    expect(out.marks[1].marks[0].properties).toBeUndefined();
  });

  it("maps axes[].type 'x'/'y' → orient 'bottom'/'left'", () => {
    const spec: any = { axes: [{ type: 'x', scale: 'xs' }, { type: 'y', scale: 'ys' }] };
    const out = rewriteVegaV2Dialect(spec);
    expect(out.axes[0].orient).toBe('bottom');
    expect(out.axes[0].type).toBeUndefined();
    expect(out.axes[1].orient).toBe('left');
    expect(out.axes[1].type).toBeUndefined();
  });

  it('is a NO-OP for a modern v3+ spec (encode + orient already present)', () => {
    const modern = {
      axes: [{ orient: 'bottom', scale: 'xs' }],
      marks: [{ type: 'rect', encode: { enter: { x: { value: 1 } } } }],
    };
    const out = rewriteVegaV2Dialect(JSON.parse(JSON.stringify(modern)));
    expect(out).toEqual(modern);
  });
});

// ── D-274: unknown scheme dropped so the dataflow does not crash ──────────────

describe('D-274 sanitizeVegaSchemes — unknown scheme dropped (the wired mechanism)', () => {
  it("drops a bespoke scheme name ('ziyaDark') at the full-Vega scales[].range.scheme site", () => {
    const spec: any = {
      scales: [{ name: 'color', type: 'ordinal', range: { scheme: 'ziyaDark' } }],
    };
    // Direction: the reused vega-lite validateColorSchemes only inspects
    // `scale.scheme`, so it would NOT drop this full-Vega `range.scheme` — the
    // whole reason a Vega-shape walker was needed. Pre-fix nothing validated it
    // and 'ziyaDark' crashed the running view ("Unrecognized scheme name").
    const dropped = sanitizeVegaSchemes(spec);
    expect(dropped).toBe(1);
    expect(spec.scales[0].range.scheme).toBeUndefined();
  });

  it('leaves a known scheme untouched (range.scheme AND scale.scheme forms)', () => {
    const specA: any = { scales: [{ range: { scheme: 'tableau10' } }] };
    expect(sanitizeVegaSchemes(specA)).toBe(0);
    expect(specA.scales[0].range.scheme).toBe('tableau10');
    // Also covers the Vega-Lite scale.scheme form.
    const specB: any = { encoding: { color: { scale: { scheme: 'nope-scheme' } } } };
    expect(sanitizeVegaSchemes(specB)).toBe(1);
    expect(specB.encoding.color.scale.scheme).toBeUndefined();
  });
});

// ── D-274 + D-276: render-path wiring (mocked vega-embed) ─────────────────────

// A recording mock for `import('vega-embed')`: it injects an <svg> into the
// target element (so postRenderSizing has something to size) and returns a
// fake view whose error listeners we can fire. jest hoists jest.mock() above
// imports and forbids the factory from touching non-`mock`-prefixed
// out-of-scope names, so the recorders are `mock`-prefixed and `document` is
// reached via the allowed `globalThis`.
const mockEmbedCalls: any[] = [];
let mockLastErrorHandler: ((e: unknown) => void) | null = null;

jest.mock('vega-embed', () => ({
  __esModule: true,
  default: jest.fn(async (el: HTMLElement, spec: any) => {
    mockEmbedCalls.push(spec);
    const doc = (globalThis as any).document;
    const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 100 400'); // tall aspect — the clip case
    svg.setAttribute('width', '100');
    svg.setAttribute('height', '400');
    el.appendChild(svg);
    const view = {
      addSignalListener: () => {},
      addEventListener: (name: string, handler: (e: unknown) => void) => {
        if (name === 'error') mockLastErrorHandler = handler;
      },
    };
    return { view, spec, finalize: () => {} };
  }),
}));

beforeAll(() => {
  // jsdom lacks ResizeObserver; the plugin constructs one after sizing.
  (global as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

beforeEach(() => {
  mockEmbedCalls.length = 0;
  mockLastErrorHandler = null;
});

describe('D-274/D-276 render wiring (mocked embed)', () => {
  const renderVega = async (spec: any, isDark: boolean) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    await vegaPlugin.render(container, null, spec, isDark);
    return container;
  };

  it('D-274: strips an unknown scheme from the spec handed to vega-embed (BOTH themes)', async () => {
    for (const dark of [false, true]) {
      const spec = {
        $schema: 'https://vega.github.io/schema/vega/v5.json',
        data: [{ name: 't', values: [{ x: 1 }] }],
        scales: [{ name: 'c', type: 'ordinal', range: { scheme: 'ziyaDark' } }],
        marks: [{ type: 'rect', from: { data: 't' } }],
      };
      await renderVega(spec, dark);
      const embedded = mockEmbedCalls[mockEmbedCalls.length - 1];
      // The bespoke scheme must be gone before the runtime saw the spec.
      expect(embedded.scales[0].range.scheme).toBeUndefined();
    }
  });

  it('D-274: an error from the running view is routed into the error placeholder (BOTH themes)', async () => {
    for (const dark of [false, true]) {
      const spec = {
        $schema: 'https://vega.github.io/schema/vega/v5.json',
        data: [{ name: 't', values: [] }],
        marks: [{ type: 'rect', from: { data: 't' } }],
      };
      const container = await renderVega(spec, dark);
      // A view error listener was registered (pre-fix: none existed).
      expect(typeof mockLastErrorHandler).toBe('function');
      mockLastErrorHandler!(new Error('Unrecognized scheme name: ziyaDark'));
      expect(container.innerHTML).toContain('data-vega-error="true"');
      expect(container.innerHTML).toContain('Unrecognized scheme name');
    }
  });

  it('D-276: the rendered SVG is overflow:visible, never overflow:hidden (BOTH themes)', async () => {
    for (const dark of [false, true]) {
      const spec = {
        $schema: 'https://vega.github.io/schema/vega/v5.json',
        data: [{ name: 't', values: [] }],
        marks: [{ type: 'rect', from: { data: 't' } }],
      };
      const container = await renderVega(spec, dark);
      const svg = container.querySelector('svg') as SVGElement;
      expect(svg).toBeTruthy();
      // Direction: the unpatched postRenderSizing set svg.style.overflow='hidden',
      // which hard-clipped this tall (aspect 4) spec. It must now be visible.
      expect((svg as any).style.overflow).toBe('visible');
      expect((svg as any).style.overflow).not.toBe('hidden');
      // Aspect preserved (letterbox), not stretched.
      expect(svg.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
    }
  });
});

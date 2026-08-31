/**
 * @jest-environment jsdom
 */
/**
 * G-20 regression tests — plotly recovery / structural / theme fixes.
 *
 * Covers four defects, each with an explicit DIRECTION check (the assertion
 * fails against the pre-fix code so it certifies the fix, not the bug):
 *
 *   D-230 tolerant spec-string parse (fence / trailing comma / unquoted /
 *         single-quoted / smart-quote / `var x =` wrapper+comments / Python
 *         repr) — pre-fix these threw on a bare JSON.parse and the capture
 *         hung 30s with an empty DOM.  `parsePlotlyDefinition` does not exist
 *         pre-fix, so the import itself fails against pre-fix code.
 *   D-236 axis/title automargin default (fixed {t,r,b,l} margin box clipped
 *         axis titles / rotated labels / overprinting ticks).
 *   D-238 clamp explicit width/height to the capture viewport (oversized
 *         figures cropped content silently).
 *   D-243 dark-theme parity: dark global font on an un-themed light polar /
 *         table surface (≈1.32:1).  BOTH themes asserted: dark is now readable
 *         AND light is left byte-identical.
 */
import {
  parsePlotlyDefinition,
  stripPlotlyFence,
  normalizePlotlySmartQuotes,
  normalizePythonLiterals,
  enableAxisAutomargin,
  clampLayoutDimensions,
  clampDimensionsToViewportForCapture,
} from '../plotlyPreprocessor';
import { applyPlotlyTheme, applyPlotlyTraceTheme } from '../plotlyPlugin';

/** Set/clear navigator.webdriver for the duration of one test. */
function withWebdriver<T>(value: boolean | undefined, fn: () => T): T {
  const nav = navigator as any;
  const had = Object.prototype.hasOwnProperty.call(nav, 'webdriver');
  const prior = nav.webdriver;
  try {
    Object.defineProperty(nav, 'webdriver', { value, configurable: true, writable: true });
    return fn();
  } finally {
    if (had) {
      Object.defineProperty(nav, 'webdriver', { value: prior, configurable: true, writable: true });
    } else {
      delete nav.webdriver;
    }
  }
}

// --- WCAG contrast helper (verifies the D-243 numbers in-test) -------------
function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const ch = [0, 2, 4].map(i => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// ==========================================================================
// D-230 — tolerant spec-string parse
// ==========================================================================
describe('D-230 parsePlotlyDefinition — tolerant recovery of one-lexeme-off input', () => {
  const cases: Array<[string, string]> = [
    ['trailing commas (w4-01)',
      '{ "data": [ {"type":"bar","x":["A","B",],"y":[3,7,],} ], "layout": {"title":{"text":"T"},} }'],
    ['markdown fence (w4-02)',
      '```json\n{ "data": [ {"type":"scatter","x":[1,2],"y":[2,5]} ], "layout": {} }\n```'],
    ['unquoted object keys (w4-03)',
      '{ data: [ { type: "scatter", x: [1,2], y: [2,5] } ], layout: { title: { text: "U" } } }'],
    ['single-quoted strings (w4-04)',
      "{'data': [{'type': 'bar', 'x': ['Q1'], 'y': [12], 'name': 'rev'}], 'layout': {}}"],
    ['smart quotes (w4-05)',
      '{ \u201Cdata\u201D: [ { \u201Ctype\u201D: \u201Cbar\u201D, \u201Cx\u201D: [\u201CN\u201D], \u201Cy\u201D: [8] } ] }'],
    ['var wrapper + comments (w4-14)',
      '// header\nvar fig = {\n  "data": [ /* c */ {"type":"bar","x":["NA"],"y":[120]} ],\n  "layout": {}\n};'],
    ['python repr None/nan/True/False (w4-15)',
      "{'data': [{'type': 'scatter', 'x': [1,2,3], 'y': [2, None, nan], 'connectgaps': False, 'visible': True}], 'layout': {}}"],
  ];

  for (const [name, raw] of cases) {
    it(`recovers: ${name}`, () => {
      // DIRECTION: the raw payload is NOT parseable by the pre-fix bare JSON.parse.
      expect(() => JSON.parse(raw)).toThrow();
      const parsed = parsePlotlyDefinition(raw);
      expect(parsed).toBeTruthy();
      expect(Array.isArray(parsed.data)).toBe(true);
      expect(parsed.data.length).toBeGreaterThan(0);
      expect(parsed.data[0].type).toBeTruthy();
    });
  }

  it('returns undefined (not a throw, not a hang) for genuinely unrecoverable input', () => {
    expect(parsePlotlyDefinition('this is not a spec at all')).toBeUndefined();
    expect(parsePlotlyDefinition('')).toBeUndefined();
    expect(parsePlotlyDefinition(42 as any)).toBeUndefined();
  });

  it('passes an already-parsed object straight through', () => {
    const obj = { data: [{ type: 'bar', x: [1], y: [2] }] };
    expect(parsePlotlyDefinition(obj)).toBe(obj);
  });

  it('leaves a Python literal inside a STRING value untouched (value-position only)', () => {
    // "None of the above" must survive the python-literal fold.
    const folded = normalizePythonLiterals('{"title": "None of the above", "v": None}');
    expect(folded).toContain('"None of the above"');
    expect(folded).toContain(': null');
  });

  it('helper direction: strip fence and fold smart quotes are real transforms', () => {
    expect(stripPlotlyFence('```json\n{"a":1}\n```')).toBe('{"a":1}');
    expect(normalizePlotlySmartQuotes('\u201Cx\u201D')).toBe('"x"');
  });
});

// ==========================================================================
// D-236 — axis / title automargin
// ==========================================================================
describe('D-236 enableAxisAutomargin — no more fixed-margin clipping', () => {
  it('turns on automargin for an existing x/y axis with a title (the collision case)', () => {
    const out = enableAxisAutomargin({ xaxis: { title: { text: 'x' } }, yaxis: {} });
    expect(out.xaxis.automargin).toBe(true);
    expect(out.yaxis.automargin).toBe(true);
  });

  it('covers multi-axis variants (xaxis2 / yaxis2 title over its own ticks)', () => {
    const out = enableAxisAutomargin({ xaxis2: { title: { text: 'secondary' } }, yaxis2: {} });
    expect(out.xaxis2.automargin).toBe(true);
    expect(out.yaxis2.automargin).toBe(true);
  });

  it('reserves a band for a LONG main title (title.automargin)', () => {
    const longTitle = 'A very long single-line title that would otherwise clip at the right paper edge of the canvas';
    const out = enableAxisAutomargin({ title: { text: longTitle } });
    expect(out.title.automargin).toBe(true);
  });

  it('SURGICAL: does not inject phantom axes or touch a short title (pure-3D / bare specs untouched)', () => {
    const layout = { title: 'Simple', scene: { domain: { x: [0, 1], y: [0, 0.88] } } };
    const out = enableAxisAutomargin(layout);
    expect(out).toBe(layout);            // reference-stable no-op
    expect(out.xaxis).toBeUndefined();   // no phantom cartesian axis on a 3D spec
  });

  it('DIRECTION: does NOT override an explicit author automargin:false', () => {
    const out = enableAxisAutomargin({ xaxis: { automargin: false } });
    expect(out.xaxis.automargin).toBe(false);
  });
});

// ==========================================================================
// D-238 — clamp explicit width/height to viewport under capture
// ==========================================================================
describe('D-238 dimension clamp — oversized figures fit the viewport', () => {
  it('clampLayoutDimensions shrinks an oversize width/height to the ceiling', () => {
    const out = clampLayoutDimensions({ width: 4000, height: 2600 }, 1280, 1024);
    expect(out.width).toBe(1280);
    expect(out.height).toBe(1024);
  });

  it('clampLayoutDimensions leaves in-range dims byte-identical (reference-stable)', () => {
    const layout = { width: 800, height: 600 };
    expect(clampLayoutDimensions(layout, 1280, 1024)).toBe(layout);
  });

  it('under headless capture, an oversize figure is clamped', () => {
    const out = withWebdriver(true, () =>
      clampDimensionsToViewportForCapture({ width: 4000, height: 2600 }));
    // window.innerWidth/Height in jsdom default to 1024x768; either way < spec.
    expect(out.width).toBeLessThan(4000);
    expect(out.height).toBeLessThan(2600);
  });

  it('DIRECTION: in the interactive UI (webdriver falsy) large dims pass through', () => {
    const layout = { width: 4000, height: 2600 };
    const out = withWebdriver(false, () => clampDimensionsToViewportForCapture(layout));
    expect(out).toBe(layout); // untouched
    expect(out.width).toBe(4000);
  });
});

// ==========================================================================
// D-243 — dark-theme parity for trace-owned / non-cartesian surfaces
// ==========================================================================
describe('D-243 dark parity — polar/ternary/geo + table surfaces themed', () => {
  it('dark applyPlotlyTheme now themes the polar surface (was un-themed white)', () => {
    const dark = applyPlotlyTheme({}, true);
    // DIRECTION: pre-fix the dark branch had NO polar key.
    expect(dark.polar).toBeDefined();
    expect(dark.polar.bgcolor).toBe('#1e1e1e');
    expect(dark.ternary.bgcolor).toBe('#1e1e1e');
    expect(dark.geo.bgcolor).toBe('#1e1e1e');
    // Dark font on the newly-themed polar surface clears the 4.5 text floor.
    expect(contrast('#e0e0e0', dark.polar.bgcolor)).toBeGreaterThan(4.5);
    // Old broken pairing (dark font on the un-themed white surface) was ~1.32.
    expect(contrast('#e0e0e0', '#ffffff')).toBeLessThan(1.5);
  });

  it('light applyPlotlyTheme is unchanged — no polar/ternary/geo override', () => {
    const light = applyPlotlyTheme({}, false);
    expect(light.paper_bgcolor).toBe('#ffffff');
    expect(light.font.color).toBe('#333333');
    // Light must NOT gain the dark subplot surfaces.
    expect(light.polar).toBeUndefined();
  });

  it('honours an explicit author polar bgcolor even in dark', () => {
    const dark = applyPlotlyTheme({ polar: { bgcolor: '#003366' } }, true);
    expect(dark.polar.bgcolor).toBe('#003366');
  });

  const tableSpec = () => ([{ type: 'table', header: { values: ['A', 'B'] }, cells: { values: [['1'], ['2']] } }]);

  it('DARK: table fills + fonts are themed to the dark surface (was white body)', () => {
    const out = applyPlotlyTraceTheme(tableSpec(), true);
    expect(out[0].cells.fill.color).toBe('#1e1e1e');
    expect(out[0].header.fill.color).toBe('#2a2a2a');
    expect(out[0].cells.font.color).toBe('#e0e0e0');
    // dark cell text clears the 4.5 text floor; light-default (#333 on #fff) was the parity partner.
    expect(contrast('#e0e0e0', '#1e1e1e')).toBeGreaterThan(4.5);
  });

  it('LIGHT: table data is returned byte-identical (dark-gated; provably no light regression)', () => {
    const data = tableSpec();
    expect(applyPlotlyTraceTheme(data, false)).toBe(data);
  });

  it('DARK: an explicit author cell fill is preserved (conservative)', () => {
    const data = [{ type: 'table', cells: { values: [['1']], fill: { color: '#123456' } } }];
    const out = applyPlotlyTraceTheme(data, true);
    expect(out[0].cells.fill.color).toBe('#123456');
  });
});

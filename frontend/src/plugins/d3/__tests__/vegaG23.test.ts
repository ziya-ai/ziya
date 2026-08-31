/**
 * G-23 regression tests for the full-Vega plugin (vegaPlugin.ts).
 *
 * Covers four defects, each with an explicit fail-without-the-fix direction
 * assertion:
 *
 *   D-279  rewriteMethodCallsInExpr() dropped the `datum.` qualifier of a
 *          dotted member path (`datum.name.slice(0,5)` -> `slice(name,0,5)`),
 *          turning a working v5 spec fatal ("Unrecognized signal name: name").
 *   D-286  the dark theme never set a default {type:'text'} mark fill, so raw
 *          text annotation marks kept default-black (1.66:1 on the ~#333 dark
 *          panel). buildVegaEmbedOptions() now injects config.text.fill in dark
 *          ONLY (light unchanged) — a both-themes theme fix.
 *   D-277  postRenderSizing scaled authored geometry uniformly with no text
 *          floor; computeReTickDimensions() re-ticks the view at the delivered
 *          size for an intrinsically un-scalable canvas, and is a NO-OP for
 *          normal specs (the regression-safety direction).
 *   D-283  a geographic flood expanded the getBBox viewBox and shrank the map
 *          to a few percent; resolveVegaViewBox() clamps to the authored
 *          viewport + clip only when content floods far beyond it, and keeps
 *          the D-276 getBBox path for ordinary (small) label overflow.
 */

import {
  rewriteMethodCallsInExpr,
  buildVegaEmbedOptions,
  computeReTickDimensions,
  resolveVegaViewBox,
} from '../vegaPlugin';

// ── local WCAG contrast helper (no external dep) ─────────────────────────────
const lin = (c: number): number => {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
};
const relLum = (hex: string): number => {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};
const contrast = (a: string, b: string): number => {
  const la = relLum(a), lb = relLum(b);
  const hi = Math.max(la, lb), lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
};

// ── D-279: dotted member path kept whole ─────────────────────────────────────

describe('rewriteMethodCallsInExpr — dotted member path (D-279)', () => {
  it('keeps the datum. qualifier of a dotted field path', () => {
    const out = rewriteMethodCallsInExpr('datum.name.slice(0, 5)');
    expect(out).toBe('slice(datum.name, 0, 5)');
    // Direction: the unpatched scan stopped at the '.', dropping `datum.` and
    // leaving a bare `name` the v6 runtime resolves as a signal.
    expect(out).not.toMatch(/\bslice\(name\b/);
    expect(out).toContain('datum.name');
  });

  it('rewrites a chained call on a dotted path (the w1-14 caption case)', () => {
    // datum.name.slice(0,5).toUpperCase()  ->  upper(slice(datum.name, 0, 5))
    const out = rewriteMethodCallsInExpr('datum.name.slice(0, 5).toUpperCase()');
    expect(out).toBe('upper(slice(datum.name, 0, 5))');
    expect(out).not.toContain('slice(name');
  });

  it('still rewrites a plain (non-dotted) identifier LHS', () => {
    expect(rewriteMethodCallsInExpr('str.slice(0, 5)')).toBe('slice(str, 0, 5)');
    expect(rewriteMethodCallsInExpr('arr.reverse()')).toBe('reverse(arr)');
  });

  it('still rewrites a method chained on a call result', () => {
    expect(rewriteMethodCallsInExpr('lower(datum.x).slice(0, 1)'))
      .toBe('slice(lower(datum.x), 0, 1)');
  });

  it('leaves a spec with no method calls untouched', () => {
    expect(rewriteMethodCallsInExpr("datum.p99 > threshold ? 'a' : 'b'"))
      .toBe("datum.p99 > threshold ? 'a' : 'b'");
  });
});

// ── D-286: dark text-mark fill, both themes ──────────────────────────────────

describe('buildVegaEmbedOptions — dark text-mark fill (D-286)', () => {
  const DARK_PANEL = '#333333';

  it('DARK: injects a readable default text-mark fill (broken theme now correct)', () => {
    const opts = buildVegaEmbedOptions(true) as any;
    expect(opts.theme).toBe('dark');
    const fill = opts.config?.text?.fill as string;
    expect(fill).toBe('#e6e6e6');
    // The fix clears the 4.5 text floor on the dark panel...
    expect(contrast(fill, DARK_PANEL)).toBeGreaterThanOrEqual(4.5);
    // ...whereas the pre-fix default (#000000) did NOT (direction).
    expect(contrast('#000000', DARK_PANEL)).toBeLessThan(4.5);
  });

  it('LIGHT: adds no dark text override (other theme still correct)', () => {
    const opts = buildVegaEmbedOptions(false) as any;
    expect(opts.theme).toBeUndefined();
    // No config.text.fill leaks into light — light output is unchanged, so the
    // default-black text keeps its >=4.5 contrast on the white panel.
    expect(opts.config?.text?.fill).toBeUndefined();
    expect(contrast('#000000', '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });
});

// ── D-277: re-tick only for un-scalable canvases ─────────────────────────────

describe('computeReTickDimensions — text-size floor via re-tick (D-277)', () => {
  const CONTAINER = 600;

  it('re-ticks an ultra-wide oversized canvas (w2-07 2400x90)', () => {
    const r = computeReTickDimensions(2400, 90, CONTAINER);
    expect(r).not.toBeNull();
    expect(r!.width).toBe(600);          // clamped into the legible band
    expect(r!.width).toBeLessThanOrEqual(1600);
    // aspect preserved (2400:90) -> ~600:22
    expect(r!.height).toBe(Math.round(90 * (600 / 2400)));
  });

  it('re-ticks a tiny undersized canvas (w2-09 70x45)', () => {
    const r = computeReTickDimensions(70, 45, CONTAINER);
    expect(r).not.toBeNull();
    expect(r!.width).toBe(600);
    expect(r!.height).toBe(Math.round(45 * (600 / 70)));
  });

  it('is a NO-OP for a normal-sized spec (regression safety — w1-14 440x240)', () => {
    // Direction: normal specs must be left EXACTLY as before (null => no
    // re-tick), so ordinary charts and the regression set do not change.
    expect(computeReTickDimensions(440, 240, CONTAINER)).toBeNull();
    expect(computeReTickDimensions(420, 220, CONTAINER)).toBeNull();
    expect(computeReTickDimensions(460, 300, CONTAINER)).toBeNull(); // geo w1-11
  });

  it('falls back to a sane width when the container has not laid out yet', () => {
    const r = computeReTickDimensions(3600, 300, 0);
    expect(r).not.toBeNull();
    expect(r!.width).toBeGreaterThanOrEqual(200);
    expect(r!.width).toBeLessThanOrEqual(1600);
  });
});

// ── D-283: clamp the flood, keep the label-overflow path ─────────────────────

describe('resolveVegaViewBox — geoshape flood vs label overflow (D-283)', () => {
  it('clamps to the authored viewport + clip when content floods (geo w1-11)', () => {
    // mercator + world graticule projects a bbox many multiples of 460x300.
    const vb = resolveVegaViewBox(460, 300, -2000, -1800, 6000, 5000);
    expect(vb.clip).toBe(true);
    expect(vb).toMatchObject({ x: 0, y: 0, w: 460, h: 300 });
    // Direction: WITHOUT the clamp the viewBox would be the 6000x5000 bbox,
    // shrinking the real map to a few percent.
    expect(vb.w).not.toBe(6000);
  });

  it('keeps the getBBox extent for ordinary label overflow (D-276 preserved)', () => {
    // rotated axis labels overflow the 440x240 viewport a LITTLE (<3x).
    const vb = resolveVegaViewBox(440, 240, -8, -6, 470, 262);
    expect(vb.clip).toBe(false);
    expect(vb).toMatchObject({ x: -8, y: -6, w: 470, h: 262 });
  });

  it('is safe on a degenerate/zero bbox (never collapse)', () => {
    const vb = resolveVegaViewBox(440, 240, 0, 0, 0, 0);
    expect(vb.clip).toBe(false);
    expect(vb.w).toBe(0); // returns the (zero) bbox unchanged; caller guards zero
  });
});

/**
 * G-67 — force-directed plugin: label font-size floor after the viewBox/fit
 * scale, and canvas-relative node-radius clamp (shared file:
 * forceDirectedPlugin.ts).
 *
 * Defects covered:
 *   D-122  `const fontSize = style.fontSize || 10` was applied verbatim as
 *          `${fontSize}px` and never scaled against the fit-to-extent zoom, and
 *          had no minimum floor — so a large extent fitted into the frame
 *          (fit.k << 1) collapsed a 10px label to a sub-pixel smudge, and a tiny
 *          caller style.fontSize (4) rendered unreadable 4px glyphs.
 *          effectiveLabelFontSize enlarges the APPLIED size so the on-screen
 *          size (applied * fit.k) clears FORCE_MIN_LABEL_ON_SCREEN_PX, without
 *          shrinking a larger caller choice.
 *   D-123  FORCE_MAX_NODE_RADIUS = 200 permitted a 400px-diameter disc inside a
 *          500px canvas; a few such discs + forceCollide evicted every other
 *          node. clampNodeRadiusToCanvas caps a radius at a fraction of the
 *          shorter canvas dimension instead of the fixed constant.
 *
 * Both defects are structural and theme-independent (no colour is changed), so
 * the render-level assertions are run in BOTH themes to prove the fix is not
 * theme-coupled.
 *
 * Direction: the two helpers did not exist before this change, and the pre-fix
 * render applied `${fontSize}px` verbatim (never floored) and `d.size || 8`
 * verbatim (clamped only to the 200 constant in the sanitizer). Each assertion
 * below checks a value the unpatched code did not produce.
 */
import {
  forceDirectedPlugin,
  effectiveLabelFontSize,
  clampNodeRadiusToCanvas,
  FORCE_MIN_LABEL_ON_SCREEN_PX,
  FORCE_MAX_NODE_RADIUS,
  FORCE_NODE_RADIUS_CANVAS_FRACTION,
} from '../forceDirectedPlugin';

// ── minimal mock d3 that records .attr()/.text() calls without a DOM ──────────
function makeMockD3() {
  const record: any = { attrs: [] as Array<[string, any]> };
  const target: any = function () {};
  const proxy: any = new Proxy(target, {
    get(_t, prop) {
      if (prop === 'zoomIdentity') return proxy;
      const name = String(prop);
      return (...args: any[]) => {
        if (name === 'attr' && args.length >= 1) record.attrs.push([args[0], args[1]]);
        return proxy;
      };
    },
    apply() {
      return proxy;
    },
  });
  return { d3: proxy, record };
}

function runRender(spec: any, isDark = false) {
  const { d3, record } = makeMockD3();
  const cleanup = forceDirectedPlugin.render({} as any, d3, spec, isDark);
  if (typeof cleanup === 'function') cleanup();
  return record;
}

// last recorded value for an attribute name
function lastAttr(record: any, name: string): any {
  const hits = record.attrs.filter((a: [string, any]) => a[0] === name);
  return hits.length ? hits[hits.length - 1][1] : undefined;
}

// ── D-122 effectiveLabelFontSize ─────────────────────────────────────────────

describe('D-122 — label font-size is floored against the fit scale', () => {
  it('collapses to a sub-pixel smudge under the unpatched formula (direction check)', () => {
    // Pre-fix: on-screen size = applied(10) * k(0.213) ≈ 2.13px — below the floor.
    expect(10 * 0.213).toBeLessThan(FORCE_MIN_LABEL_ON_SCREEN_PX);
  });

  it('enlarges the applied size so on-screen clears the floor when fit.k < 1', () => {
    const k = 0.213;
    const applied = effectiveLabelFontSize(10, k);
    // on-screen = applied * k must clear the floor
    expect(applied * k).toBeGreaterThanOrEqual(FORCE_MIN_LABEL_ON_SCREEN_PX - 1e-9);
    // and it had to grow beyond the authored 10px to do so
    expect(applied).toBeGreaterThan(10);
  });

  it('floors a tiny caller fontSize (4) even at k = 1', () => {
    const applied = effectiveLabelFontSize(4, 1);
    expect(applied).toBe(FORCE_MIN_LABEL_ON_SCREEN_PX);
    expect(applied).toBeGreaterThan(4);
  });

  it('never shrinks a larger caller fontSize', () => {
    expect(effectiveLabelFontSize(24, 1)).toBe(24);
    // even zoomed in (k>1) a large caller size is preserved
    expect(effectiveLabelFontSize(24, 2)).toBe(24);
  });

  it('degrades non-finite inputs to sane defaults', () => {
    expect(effectiveLabelFontSize(NaN, 1)).toBe(10); // base default
    // k<=0 / NaN coerces k to 1, so floor/1 = 9 < base 10 -> base 10 wins
    expect(effectiveLabelFontSize(10, 0)).toBe(10);
    expect(effectiveLabelFontSize(10, NaN)).toBe(10);
  });
});

// ── D-123 clampNodeRadiusToCanvas ────────────────────────────────────────────

describe('D-123 — node radius clamped to a fraction of the canvas', () => {
  it('the fixed constant is far more permissive than the canvas-relative cap (direction check)', () => {
    // Pre-fix cap was FORCE_MAX_NODE_RADIUS=200: a 400px-diameter disc = 80% of a
    // 500px canvas, and the constant is over 2x the canvas-relative cap (90px),
    // which is exactly why clamping to the constant alone was insufficient.
    expect(FORCE_MAX_NODE_RADIUS).toBeGreaterThan(500 * FORCE_NODE_RADIUS_CANVAS_FRACTION);
    expect((FORCE_MAX_NODE_RADIUS * 2) / 500).toBeGreaterThan(0.5);
  });

  it('caps a huge radius at the canvas-relative fraction', () => {
    const cap = 500 * FORCE_NODE_RADIUS_CANVAS_FRACTION; // 90 for the default 700x500
    expect(clampNodeRadiusToCanvas(312, 700, 500)).toBeCloseTo(cap, 6);
    expect(clampNodeRadiusToCanvas(312, 700, 500)).toBeLessThan(312);
    // and comfortably fits within the shorter dimension (diameter < canvas)
    expect(2 * clampNodeRadiusToCanvas(312, 700, 500)).toBeLessThan(500);
  });

  it('leaves a small default radius unchanged', () => {
    expect(clampNodeRadiusToCanvas(8, 700, 500)).toBe(8);
  });

  it('never exceeds the absolute FORCE_MAX_NODE_RADIUS on a huge canvas', () => {
    // 0.18 * 4000 = 720 > 200, so the absolute cap wins.
    expect(clampNodeRadiusToCanvas(1000, 4000, 4000)).toBe(FORCE_MAX_NODE_RADIUS);
  });

  it('keeps a small floor on a tiny canvas so nodes stay visible', () => {
    expect(clampNodeRadiusToCanvas(50, 40, 40)).toBe(12);
  });

  it('coerces non-finite radius to 0', () => {
    expect(clampNodeRadiusToCanvas(NaN as any, 700, 500)).toBe(0);
    expect(clampNodeRadiusToCanvas(-5, 700, 500)).toBe(0);
  });
});

// ── render-level wiring, asserted in BOTH themes ─────────────────────────────

const bigNodeSpec = {
  type: 'force-directed',
  width: 700,
  height: 500,
  style: { fontSize: 4 },
  nodes: [
    { id: 'a', size: 312 },
    { id: 'b', size: 8 },
  ],
  links: [{ source: 'a', target: 'b' }],
};

describe.each([
  ['light', false],
  ['dark', true],
])('render wiring — %s theme', (_label, isDark) => {
  it('applies a floored font-size (not the raw 4px)', () => {
    const rec = runRender(bigNodeSpec, isDark as boolean);
    const fs = lastAttr(rec, 'font-size');
    // fit.k resolves to 1 in the DOM-less mock (no settled positions), so the
    // floor is FORCE_MIN_LABEL_ON_SCREEN_PX = 9px — never the authored 4px.
    expect(fs).toBe(`${FORCE_MIN_LABEL_ON_SCREEN_PX}px`);
    expect(fs).not.toBe('4px');
  });

  it('clamps the circle radius accessor below the raw node size', () => {
    const rec = runRender(bigNodeSpec, isDark as boolean);
    const rAttr = lastAttr(rec, 'r');
    expect(typeof rAttr).toBe('function');
    const clamped = rAttr({ id: 'a', size: 312 });
    expect(clamped).toBeLessThan(312);
    expect(clamped).toBeCloseTo(500 * FORCE_NODE_RADIUS_CANVAS_FRACTION, 6);
    // an ordinary small node is untouched
    expect(rAttr({ id: 'b', size: 8 })).toBe(8);
  });
});

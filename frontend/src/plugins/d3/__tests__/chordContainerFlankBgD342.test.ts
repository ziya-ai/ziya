/**
 * @jest-environment jsdom
 */
/**
 * D-342 (theme, dark) — container-bg-mismatch-exposed-around-small-canvas.
 *
 * When the chord canvas is far smaller than the ~632px capture frame
 * (chord-w2-11 100x100 clamped to a ~40px ring; w2-12 9:1 and w2-13 1:7 both
 * aspect-capped to a narrow strip), the plugin's own render container is wider /
 * taller than the SVG. The container was transparent, so the flank around the
 * canvas showed the AntD dark 'paper' surface painted behind it (#1f1f1f) — a
 * visibly different grey rectangle against the diagram/page surface (#1a1a2e):
 * #1f1f1f vs #1a1a2e = 1.035:1. In light both surfaces are ~white so it was
 * invisible.
 *
 * FIX (chordPlugin.render): paint the render container with the SAME resolved
 * theme surface the SVG background uses, so the flank is byte-identical to the
 * canvas (1.00:1) and the seam disappears — resolved from the theme, not a
 * substituted constant.
 *
 * DIRECTION: pre-fix, render never touched container.style.background, so it
 * stayed '' (assertions below fail); post-fix it equals resolveChordBackground
 * for the theme. Asserted in BOTH themes (this is a theme defect), and the dark
 * value is asserted NOT to be the old #1f1f1f seam colour.
 */
import { chordPlugin, resolveChordBackground } from '../chordPlugin';

// A mock d3 that records nothing and never touches a real DOM: every chained
// call returns the same proxy, and calling the module (d3.chord(), d3.arc()…)
// also returns it. The chord render's DOM writes all go through d3.select(...),
// so the real container element stays empty — only the plugin's own
// `container.style.background = bg` line mutates it, which is exactly what we
// assert.
function makeMockD3(): any {
  const target: any = function () {};
  const proxy: any = new Proxy(target, {
    get(_t, prop) {
      if (prop === 'zoomIdentity') return proxy;
      return () => proxy;
    },
    apply() {
      return proxy;
    },
  });
  return proxy;
}

// A minimal small-canvas chord spec (the shape of chord-w2-11: a tiny 100x100
// canvas). The exact geometry is irrelevant to the container background.
function smallChordSpec(width: number, height: number) {
  return {
    type: 'chord',
    width,
    height,
    matrix: [
      [0, 1, 1],
      [1, 0, 1],
      [1, 1, 0],
    ],
    names: ['a', 'b', 'c'],
  };
}

function renderAndReadBg(spec: any, isDark: boolean): string {
  const container = document.createElement('div');
  const cleanup = chordPlugin.render(container, makeMockD3(), spec, isDark);
  if (typeof cleanup === 'function') cleanup();
  return container.style.background;
}

// jsdom's CSSOM serializes `style.background` (e.g. '#1a1a2e' -> 'rgb(26, 26,
// 46)'), so compare colours by round-tripping BOTH sides through the same DOM
// setter rather than string-matching hex.
function normColor(value: string): string {
  const probe = document.createElement('div');
  probe.style.background = value;
  return probe.style.background;
}

const DARK_SURFACE = '#1a1a2e';
const LIGHT_SURFACE = '#ffffff';
const OLD_SEAM = '#1f1f1f';

describe('D-342 chord render paints the container flank with the theme surface', () => {
  it.each([
    ['chord-w2-11', 100, 100],
    ['chord-w2-12', 900, 100],
    ['chord-w2-13', 100, 700],
  ])('DARK (previously broken): %s flank == canvas surface, not the #1f1f1f seam', (_id, w, h) => {
    const bg = renderAndReadBg(smallChordSpec(w, h), /* isDark */ true);
    // The theme surface the SVG itself is painted with.
    expect(bg).toBe(normColor(resolveChordBackground(undefined, true)));
    expect(bg).toBe(normColor(DARK_SURFACE));
    // Direction: pre-fix this was '' (never set); and it must NOT be the old
    // AntD paper seam colour that measured 1.035:1 against the canvas.
    expect(bg).not.toBe('');
    expect(bg).not.toBe(normColor(OLD_SEAM));
  });

  it.each([
    ['chord-w2-11', 100, 100],
    ['chord-w2-12', 900, 100],
    ['chord-w2-13', 100, 700],
  ])('LIGHT (paired, stays invisible): %s flank == white page surface', (_id, w, h) => {
    const bg = renderAndReadBg(smallChordSpec(w, h), /* isDark */ false);
    expect(bg).toBe(normColor(resolveChordBackground(undefined, false)));
    expect(bg).toBe(normColor(LIGHT_SURFACE));
    expect(bg).not.toBe('');
  });

  it('a caller-pinned style.background carries to the flank too (no seam under a custom panel)', () => {
    const spec = { ...smallChordSpec(100, 100), style: { background: '#102030' } };
    const bg = renderAndReadBg(spec, /* isDark */ true);
    // Same resolver the SVG background uses -> flank matches the pinned canvas.
    expect(bg).toBe(normColor(resolveChordBackground('#102030', true)));
    expect(bg).toBe(normColor('#102030'));
  });
});

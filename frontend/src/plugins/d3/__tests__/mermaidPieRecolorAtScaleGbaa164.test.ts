/**
 * @jest-environment jsdom
 *
 * G-baa164 / D-157 (mermaid pie-default-palette-not-canvas-aware),
 * mermaid-w2-15 (60-slice palette-recycling-at-scale probe).
 *
 * mermaid's pie theme exposes only pie1..pie12, so a >12-slice chart drives its
 * d3 ordinal scale past its range and RECYCLES the 12 fills (a 60-slice chart
 * repeats each colour 5x — the "adjacent-far slices indistinguishable" symptom).
 * buildPieThemeVariables makes those 12 canvas-aware but cannot lift the vendor
 * limit. recolorPieSlicesAtScale runs post-render and gives every slice (and its
 * legend swatch) its own colour from an evenly-spaced hue ramp whose lightness
 * is solved per hue to clear the >=3:1 canvas floor on the theme background.
 *
 * DIRECTION: recolorPieSlicesAtScale / pieRampColor do not exist on the pre-fix
 * tree, so this suite fails to compile there. It asserts the NEW guarantee:
 * 60 distinct, contrast-passing fills in BOTH themes, no recycling.
 */
import { recolorPieSlicesAtScale, pieRampColor, PIE_RECYCLE_THRESHOLD } from '../mermaidPlugin';

function srgbToLin(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const LIGHT_BG = '#ffffff';
const DARK_BG = '#1f1f1f';

function buildPie(n: number): SVGElement {
  const host = document.createElement('div');
  let slices = '';
  let legend = '';
  for (let i = 0; i < n; i++) {
    // mermaid recycles the same 12 fills; emulate that starting point.
    const recycled = ['#4e79a7', '#e15759', '#59a14f', '#b07aa1', '#9c6b3f', '#d17d00',
      '#3a8a86', '#8e792b', '#c05299', '#6f6f6f', '#7b4fb0', '#2f7ab8'][i % 12];
    slices += `<path class="pieCircle" fill="${recycled}"></path>`;
    legend += `<g class="legend"><rect width="18" height="18" fill="${recycled}"></rect></g>`;
  }
  host.innerHTML = `<svg>${slices}${legend}</svg>`;
  return host.querySelector('svg') as unknown as SVGElement;
}

function sliceFills(svg: SVGElement): string[] {
  return Array.from(svg.querySelectorAll('path.pieCircle')).map(
    (s) => (s.getAttribute('fill') || '').toLowerCase()
  );
}

describe('G-baa164 D-157: pie recolor eliminates palette recycling at scale', () => {
  it('pieRampColor clears 3:1 for every hue on BOTH backgrounds', () => {
    for (let i = 0; i < 60; i++) {
      const hue = (i * 360) / 60;
      expect(contrast(pieRampColor(hue, false), LIGHT_BG)).toBeGreaterThanOrEqual(3);
      expect(contrast(pieRampColor(hue, true), DARK_BG)).toBeGreaterThanOrEqual(3);
    }
  });

  it('60 slices become 60 DISTINCT fills in LIGHT (recycling removed)', () => {
    const svg = buildPie(60);
    // Precondition: the incoming render only had 12 distinct fills.
    expect(new Set(sliceFills(svg)).size).toBe(12);
    const r = recolorPieSlicesAtScale(svg, false);
    expect(r.recolored).toBe(60);
    const fills = sliceFills(svg);
    expect(new Set(fills).size).toBe(60);
    fills.forEach((f) => expect(contrast(f, LIGHT_BG)).toBeGreaterThanOrEqual(3));
  });

  it('60 slices become 60 DISTINCT fills in DARK (other-theme guard)', () => {
    const svg = buildPie(60);
    const r = recolorPieSlicesAtScale(svg, true);
    expect(r.recolored).toBe(60);
    const fills = sliceFills(svg);
    expect(new Set(fills).size).toBe(60);
    fills.forEach((f) => expect(contrast(f, DARK_BG)).toBeGreaterThanOrEqual(3));
  });

  it('legend swatches are recolored in lockstep with their slices', () => {
    const svg = buildPie(60);
    recolorPieSlicesAtScale(svg, false);
    const slices = sliceFills(svg);
    const swatches = Array.from(svg.querySelectorAll('g.legend rect')).map(
      (r) => (r.getAttribute('fill') || '').toLowerCase()
    );
    expect(swatches).toEqual(slices);
  });

  it('a small pie (<= 12 slices) is left to the curated palette (no-op)', () => {
    const svg = buildPie(PIE_RECYCLE_THRESHOLD);
    const before = sliceFills(svg);
    const r = recolorPieSlicesAtScale(svg, false);
    expect(r.recolored).toBe(0);
    expect(sliceFills(svg)).toEqual(before);
  });

  // D-157 residual: distinctness alone is not legibility. The old monotonic
  // (i*360/n) ramp gave ADJACENT wedges hues only 360/n deg apart (6deg at 60
  // slices) — technically 60 distinct hex values, but visually one smear. The
  // golden-angle distribution must keep consecutive slices far apart on the hue
  // wheel. This assertion FAILS on the monotonic ramp (6deg << 60deg) and passes
  // on the golden-angle one (~137deg). Asserted in BOTH themes.
  function fillHue(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16) / 255;
    const g = parseInt(h.slice(2, 4), 16) / 255;
    const b = parseInt(h.slice(4, 6), 16) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    if (d === 0) return 0;
    let hue: number;
    if (max === r) hue = ((g - b) / d) % 6;
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue *= 60;
    return (hue + 360) % 360;
  }
  function minAdjacentHueGap(fills: string[]): number {
    let m = 360;
    for (let i = 0; i < fills.length - 1; i++) {
      const d = Math.abs(fillHue(fills[i]) - fillHue(fills[i + 1])) % 360;
      m = Math.min(m, Math.min(d, 360 - d));
    }
    return m;
  }

  it.each([['light', false], ['dark', true]] as const)(
    'consecutive slices are widely separated in hue (%s)',
    (_label, isDark) => {
      const svg = buildPie(60);
      recolorPieSlicesAtScale(svg, isDark);
      // Golden-angle spacing keeps neighbours far apart; the monotonic ramp
      // (6deg) would fail this. Require a large minimum circular hue gap.
      expect(minAdjacentHueGap(sliceFills(svg))).toBeGreaterThan(60);
    }
  );
});

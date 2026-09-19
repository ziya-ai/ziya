/**
 * @jest-environment jsdom
 *
 * D-420 (mermaid pie-title-invisible:light / pie-legend-text-low-contrast:dark),
 * specs mermaid-w1-08 / w2-15 / w4-12.
 *
 * mermaid's pie renderer paints the title (`text.pieTitleText`) and legend
 * labels (`g.legend text`) from its base `textColor` / a hard default that
 * OVERRIDES the per-theme pieTitleTextColor / pieLegendTextColor keys pinned in
 * buildPieThemeVariables. The observed symptom is inverted per theme: title +
 * legend rendered near-white on the LIGHT canvas (invisible) and near-black on
 * the DARK canvas (~1.28:1).
 *
 * recolorPieTextForTheme runs post-render and forces those text nodes to the
 * theme-resolved colour (resolved from the theme the renderer was given, not a
 * vendor default). The slice PERCENTAGE labels (`text.slice`) must be left
 * alone — they sit on the wedge fills, not the canvas.
 *
 * DIRECTION: recolorPieTextForTheme does not exist on the pre-fix tree, so this
 * suite fails to compile there. Both themes are asserted.
 */
import { recolorPieTextForTheme } from '../mermaidPlugin';

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
const DARK_BG = '#1e1e2e';

// Build a pie SVG mimicking mermaid's emitted structure, with the WRONG
// (vendor-default) text colour baked in for each theme so the pre-recolor state
// is the failing state.
function buildPie(wrongColor: string): SVGElement {
  const host = document.createElement('div');
  host.innerHTML = `
    <svg>
      <text class="pieTitleText" fill="${wrongColor}" style="fill:${wrongColor};">Storage by tier (TB)</text>
      <g class="legend"><rect fill="#4e79a7"></rect><text fill="${wrongColor}" style="fill:${wrongColor};">SSD</text></g>
      <g class="legend"><rect fill="#e15759"></rect><text fill="${wrongColor}" style="fill:${wrongColor};">HDD</text></g>
      <text class="slice" fill="#000000">42%</text>
    </svg>`;
  return host.querySelector('svg') as unknown as SVGElement;
}

describe('D-420 pie title/legend text resolves from the active theme', () => {
  test('LIGHT: title + legend text become dark and clear 4.5:1 on the white canvas', () => {
    // Vendor default paints near-white on light -> invisible.
    const svg = buildPie('#ffffff');
    const n = recolorPieTextForTheme(svg, false);
    expect(n).toBeGreaterThanOrEqual(3); // title + 2 legend labels

    svg.querySelectorAll('text.pieTitleText, g.legend text').forEach((el) => {
      const fill = (el.getAttribute('fill') || '').toLowerCase();
      expect(fill).toBe('#1a1a1a');
      expect(contrast(fill, LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
    });
  });

  test('DARK: title + legend text become light and clear 4.5:1 on the dark canvas', () => {
    // Vendor default forces near-black on dark -> ~1.28:1.
    const svg = buildPie('#000000');
    const n = recolorPieTextForTheme(svg, true);
    expect(n).toBeGreaterThanOrEqual(3);

    svg.querySelectorAll('text.pieTitleText, g.legend text').forEach((el) => {
      const fill = (el.getAttribute('fill') || '').toLowerCase();
      expect(fill).toBe('#eceff4');
      expect(contrast(fill, DARK_BG)).toBeGreaterThanOrEqual(4.5);
    });
  });

  test('slice percentage labels (text.slice) are NOT recoloured', () => {
    const svg = buildPie('#ffffff');
    recolorPieTextForTheme(svg, false);
    const slice = svg.querySelector('text.slice')!;
    // Untouched: keeps its original wedge-appropriate fill.
    expect((slice.getAttribute('fill') || '').toLowerCase()).toBe('#000000');
  });
});

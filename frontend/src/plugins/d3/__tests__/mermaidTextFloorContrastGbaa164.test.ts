/**
 * @jest-environment jsdom
 *
 * G-baa164 / D-154 (mermaid dark-palette-foreground-not-adapted):
 *   mermaid-w1-11 (mindmap ROOT label) and mermaid-w1-03 (sequence NOTE text).
 *
 * ROOT CAUSE (confirmed in code, not the triage hypothesis):
 *   enhanceSVGVisibility repaints TEXT only when its contrast is below the
 *   generic graphic floor `minContrast` (3.0). A label on a mid-tone fill can
 *   land in the DEAD ZONE — above 3.0 (so the generic pass leaves it) yet below
 *   the WCAG 4.5:1 text floor (so it still fails). The mindmap ROOT circle in
 *   dark (mid-blue #4a72b2) carries a dark label at ~3.29:1: not remediated,
 *   not legible. Separately, getOptimalTextColor's heuristics can pick the
 *   LOWER-contrast of black/white on a mid-tone fill.
 *
 * FIX: enhanceSVGVisibility grew a `textMinContrast` option (mermaidPlugin
 *   passes 4.5; drawio/graphviz keep 3.0), and when the heuristic colour fails
 *   the floor it falls back to the genuinely max-contrast of black/white. The
 *   colour is resolved from the fill measured in the rendered DOM, so the same
 *   logic is correct on BOTH backgrounds.
 *
 * DIRECTION: the first test asserts the OLD behaviour (floor 3.0 leaves the
 *   ~3.29:1 root label untouched) — the bug — and the SECOND asserts that with
 *   textMinContrast:4.5 the label is repainted to clear 4.5. On the pre-fix
 *   tree `textMinContrast` is ignored, so the second test fails.
 */
import { enhanceSVGVisibility } from '../../../utils/colorUtils';

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

// Mid-blue root fill: dark text = 3.29:1 (dead zone), white = 4.84:1.
const ROOT_FILL = '#4a72b2';
// Sequence note dark plate + muted grey note text (2.34:1).
const NOTE_FILL = '#3b4252';

function buildMindmapRoot(): SVGElement {
  const host = document.createElement('div');
  // Label <text> is a SIBLING of the node shape inside the same node group, so
  // findElementBackground resolves the ROOT circle fill as the background (the
  // real mermaid layout — which is why the ~3.29:1 dark label survives the 3.0
  // floor and the verifier still sees it fail).
  host.innerHTML =
    '<svg><g class="mindmap-node section-root">' +
    `<circle r="40" fill="${ROOT_FILL}"></circle>` +
    '<text fill="#222222">Observability</text>' +
    '</g></svg>';
  return host.querySelector('svg') as unknown as SVGElement;
}

function buildSequenceNote(): SVGElement {
  const host = document.createElement('div');
  host.innerHTML =
    '<svg><g class="note">' +
    `<rect class="note" width="180" height="40" fill="${NOTE_FILL}"></rect>` +
    '<text class="noteText" fill="#7a7a7a">idempotency key retained 24h</text>' +
    '</g></svg>';
  return host.querySelector('svg') as unknown as SVGElement;
}

function labelFill(svg: SVGElement): string {
  const t = svg.querySelector('text') as SVGElement;
  return (t.getAttribute('fill') || '').toLowerCase();
}

describe('G-baa164 D-154: mermaid text needs the 4.5:1 floor, resolved from the fill', () => {
  it('BUG: at the generic 3.0 floor the ~3.29:1 mindmap ROOT label is left unremediated', () => {
    const svg = buildMindmapRoot();
    // Pre-condition: the dark label is in the dead zone on the mid-blue fill.
    expect(contrast('#222222', ROOT_FILL)).toBeGreaterThan(3.0);
    expect(contrast('#222222', ROOT_FILL)).toBeLessThan(4.5);
    enhanceSVGVisibility(svg, true); // default floor 3.0 (as drawio/graphviz use)
    // Unchanged: still the failing dark label.
    expect(labelFill(svg)).toBe('#222222');
    expect(contrast(labelFill(svg), ROOT_FILL)).toBeLessThan(4.5);
  });

  it('FIX: with textMinContrast 4.5 the ROOT label is repainted to clear 4.5 in DARK', () => {
    const svg = buildMindmapRoot();
    enhanceSVGVisibility(svg, true, { textMinContrast: 4.5 });
    expect(contrast(labelFill(svg), ROOT_FILL)).toBeGreaterThanOrEqual(4.5);
  });

  it('FIX holds in LIGHT too (theme-symmetric): a mid-blue root label clears 4.5 on the light page', () => {
    // The fill is measured from the DOM, so the same code path runs regardless
    // of theme; assert the applied colour clears the text floor against the fill.
    const svg = buildMindmapRoot();
    enhanceSVGVisibility(svg, false, { textMinContrast: 4.5 });
    expect(contrast(labelFill(svg), ROOT_FILL)).toBeGreaterThanOrEqual(4.5);
  });

  it('sequence NOTE text (w1-03): muted grey on the dark plate is remediated above 4.5', () => {
    const svg = buildSequenceNote();
    expect(contrast('#7a7a7a', NOTE_FILL)).toBeLessThan(4.5); // failing precondition
    enhanceSVGVisibility(svg, true, { textMinContrast: 4.5 });
    expect(contrast(labelFill(svg), NOTE_FILL)).toBeGreaterThanOrEqual(4.5);
  });
});

/**
 * @jest-environment jsdom
 *
 * D-155 (linkstyle-stroke-override-dropped:dark) REGRESSION-SURVIVAL guard.
 *
 * The colour computation for w1-15 is already pinned by mermaidG22.test.ts
 * (#ff8800 honoured verbatim, #aa0000 honour-then-lightened to #cc6666). This
 * guard pins the SEPARATE mechanism whose absence made D-155 oscillate
 * verified<->regression:
 *
 *   The dark visibility pass (enhanceSVGVisibility) runs a DELAYED (500ms)
 *   re-run that repaints edges back to the theme lineColor. That re-run lands
 *   right at the ~500ms headless-capture boundary, while the linkStyle
 *   reapply's safety-net re-run is at 650ms — AFTER capture. So the delayed
 *   visibility re-run erased the honoured author colours in the captured frame.
 *
 * The fix removes the competing writer instead of out-racing a timer:
 *   1. reapplyLinkStyleStrokes TAGS every honoured edge `data-ziya-linkstroke`.
 *   2. mermaidPlugin adds `[data-ziya-linkstroke]` to the visibility pass's
 *      skipSelectors for dark flowchart/graph, so the delayed re-run skips
 *      exactly those edges.
 *
 * Both themes are asserted per the theme-fix contract: DARK tags + survives the
 * re-stomp; LIGHT is a no-op (reapply returns 0, nothing tagged).
 *
 * DIRECTION: without the setAttribute in reapplyLinkStyleStrokes the tag is
 * absent, the skip selector matches nothing, and the simulated delayed
 * visibility re-run (which respects skipSelectors, exactly as the real one
 * does) repaints the author edges back to teal — failing every survival
 * assertion below.
 */

import { reapplyLinkStyleStrokes } from '../mermaidEnhancer';

const SVGNS = 'http://www.w3.org/2000/svg';
const LINE_COLOR = '#88c0d0'; // dark-theme lineColor the visibility pass paints

// The exact selector mermaidPlugin.ts feeds to enhanceSVGVisibility's
// skipSelectors for dark flowchart/graph. If the plugin's selector changes,
// this constant must change with it — that coupling is the point of the guard.
const LINKSTROKE_SKIP_SELECTOR = '[data-ziya-linkstroke]';

const W1_15 = [
  'flowchart LR',
  '  A[Producer] ==> B{Broker}',
  '  B -.->|retry| C[Consumer 1]',
  '  B -->|primary| D[Consumer 2]',
  '  B --x E[Dropped]',
  '  D --o F[Ack store]',
  '  C --> F',
  '  F --> |flush every 5s| G[(Warehouse)]',
  '  linkStyle 1 stroke:#ff8800,stroke-width:2px',
  '  linkStyle 3 stroke:#aa0000,stroke-width:2px',
].join('\n');

function svgEl(tag: string, cls?: string): SVGElement {
  const e = document.createElementNS(SVGNS, tag) as SVGElement;
  if (cls) e.setAttribute('class', cls);
  return e;
}
function strokeOf(el: Element): string {
  return (el as SVGElement).style.getPropertyValue('stroke');
}
function buildW115Svg(): { svg: SVGElement; edges: SVGElement[] } {
  const svg = svgEl('svg');
  const edgePaths = svgEl('g', 'edgePaths');
  svg.appendChild(edgePaths);
  const edges = Array.from({ length: 7 }, () => {
    const p = svgEl('path', 'flowchart-link');
    p.style.setProperty('stroke', LINE_COLOR, 'important'); // visibility-pass repaint
    edgePaths.appendChild(p);
    return p;
  });
  return { svg, edges };
}

/**
 * Faithful stand-in for the visibility pass's DELAYED re-run: repaint EVERY
 * edge to the theme lineColor, but honour skipSelectors exactly as the real
 * enhanceSVGVisibility does (via `el.matches(sel)`). This is the writer that
 * used to erase the honoured colours; the fix is proven by the tagged edges
 * being exempt from it.
 */
function simulateDelayedVisibilityRestomp(svg: Element, skipSelectors: string[]): void {
  svg.querySelectorAll('path').forEach(p => {
    const skipped = skipSelectors.some(sel => {
      try { return p.matches(sel); } catch { return false; }
    });
    if (skipped) return;
    (p as SVGElement).style.setProperty('stroke', LINE_COLOR, 'important');
  });
}

describe('D-155 linkStyle override survives the delayed dark visibility re-run', () => {
  it('DARK: reapply tags only the two colour-coded edges, not the untargeted ones', () => {
    const { svg, edges } = buildW115Svg();
    const n = reapplyLinkStyleStrokes(svg, W1_15, true);
    expect(n).toBe(2);

    // The crux of the fix: honoured edges are tagged so the visibility pass can
    // exempt them. Absent before the fix -> this fails.
    expect(edges[1].getAttribute('data-ziya-linkstroke')).toBe('1');
    expect(edges[3].getAttribute('data-ziya-linkstroke')).toBe('1');

    // Untargeted directional-variant edges are NOT tagged (no over-exemption:
    // they must still be remediated by the visibility pass).
    [0, 2, 4, 5, 6].forEach(i =>
      expect(edges[i].hasAttribute('data-ziya-linkstroke')).toBe(false)
    );
  });

  it('DARK: the plugin skip selector exempts the tagged edges from the re-stomp', () => {
    const { svg, edges } = buildW115Svg();
    reapplyLinkStyleStrokes(svg, W1_15, true);

    // The tagged author colours after reapply.
    expect(strokeOf(edges[1]).toLowerCase()).toBe('#ff8800');
    const redAfterReapply = strokeOf(edges[3]).toLowerCase();
    expect(redAfterReapply).not.toBe('#aa0000'); // honour-then-lightened
    expect(redAfterReapply).not.toBe(LINE_COLOR);

    // The delayed visibility re-run fires — WITH the plugin's skip selector.
    simulateDelayedVisibilityRestomp(svg, [LINKSTROKE_SKIP_SELECTOR]);

    // Colour-coded edges survive (this is the regression that used to fail).
    expect(strokeOf(edges[1]).toLowerCase()).toBe('#ff8800');
    expect(strokeOf(edges[3]).toLowerCase()).toBe(redAfterReapply);

    // Untagged edges are (correctly) repainted to the theme line colour.
    [0, 2, 4, 5, 6].forEach(i => expect(strokeOf(edges[i])).toBe(LINE_COLOR));
  });

  it('DARK: WITHOUT the exemption the same re-stomp erases the author colours (defect reproduction)', () => {
    const { svg, edges } = buildW115Svg();
    reapplyLinkStyleStrokes(svg, W1_15, true);

    // No skip selector -> the delayed re-run repaints everything, exactly the
    // pre-fix behaviour that dropped the linkStyle overrides at capture time.
    simulateDelayedVisibilityRestomp(svg, []);
    expect(strokeOf(edges[1])).toBe(LINE_COLOR);
    expect(strokeOf(edges[3])).toBe(LINE_COLOR);
  });

  it('LIGHT: no-op — reapply returns 0 and tags nothing', () => {
    const { svg, edges } = buildW115Svg();
    edges[1].style.setProperty('stroke', '#ff8800', 'important');
    edges[3].style.setProperty('stroke', '#aa0000', 'important');

    const n = reapplyLinkStyleStrokes(svg, W1_15, false);
    expect(n).toBe(0);
    edges.forEach(e => expect(e.hasAttribute('data-ziya-linkstroke')).toBe(false));
  });
});

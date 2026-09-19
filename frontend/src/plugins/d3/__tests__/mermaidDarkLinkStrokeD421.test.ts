/**
 * @jest-environment jsdom
 *
 * D-421 (dark-link-stroke-dissolves:dark), specs mermaid-w3-04 + w3-05.
 * Both pass LIGHT and fail DARK only.
 *
 *  - w3-04: sankey link ribbons use `mix-blend-mode: multiply`, which reads over
 *    a white page but collapses to near-black over the dark canvas, so the
 *    ribbons vanish. neutralizeSankeyDarkBlend flips the blend to 'normal' in
 *    dark while preserving ribbon width (the flow-magnitude encoding).
 *  - w3-05: a `linkStyle` stroke that clears contrast against the dark CANVAS
 *    (#1e1e1e) is still low-contrast where it crosses a subgraph/cluster fill
 *    (#2e3440, LIGHTER than the canvas). reapplyLinkStyleStrokes now resolves
 *    readability against the cluster fill when clusters are present.
 *
 * DIRECTION: neutralizeSankeyDarkBlend does not exist pre-fix; and pre-fix
 * reapplyLinkStyleStrokes used the canvas as the only reference, leaving a
 * #0000cc link at ~2.70:1 on the cluster fill (below the 3:1 graphic floor).
 */
import { neutralizeSankeyDarkBlend } from '../mermaidPlugin';
import { reapplyLinkStyleStrokes } from '../mermaidEnhancer';

function srgbToLin(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
  const h = hex.replace('#', '');
  return 0.2126 * srgbToLin(parseInt(h.slice(0, 2), 16))
    + 0.7152 * srgbToLin(parseInt(h.slice(2, 4), 16))
    + 0.0722 * srgbToLin(parseInt(h.slice(4, 6), 16));
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const DARK_CANVAS = '#1e1e1e';
const DARK_CLUSTER = '#2e3440';

describe('D-421 w3-04: sankey multiply blend neutralised in dark', () => {
  function buildSankey(): SVGElement {
    const host = document.createElement('div');
    host.innerHTML = `
      <svg>
        <g class="links">
          <path class="link" style="mix-blend-mode:multiply;fill-opacity:0.3;" stroke-width="18"></path>
          <path class="link" style="mix-blend-mode:multiply;fill-opacity:0.3;" stroke-width="4"></path>
        </g>
      </svg>`;
    return host.querySelector('svg') as unknown as SVGElement;
  }

  test('multiply -> normal, and ribbon width (magnitude encoding) preserved', () => {
    const svg = buildSankey();
    const n = neutralizeSankeyDarkBlend(svg);
    expect(n).toBe(2);
    const links = Array.from(svg.querySelectorAll('path.link'));
    links.forEach((el) => {
      expect((el as unknown as SVGElement).style.getPropertyValue('mix-blend-mode')).toBe('normal');
    });
    // Widths untouched: 18 and 4 still distinct.
    expect(links[0].getAttribute('stroke-width')).toBe('18');
    expect(links[1].getAttribute('stroke-width')).toBe('4');
  });
});

describe('D-421 w3-05: linkStyle stroke clears contrast on the cluster fill (dark)', () => {
  function buildClusteredFlow(): SVGElement {
    const host = document.createElement('div');
    host.innerHTML = `
      <svg>
        <g class="clusters"><g class="cluster"><rect></rect></g></g>
        <g class="edgePaths"><path d="M0,0L10,10"></path></g>
      </svg>`;
    return host.querySelector('svg') as unknown as SVGElement;
  }
  // A blue link that clears vs the canvas but NOT vs the lighter cluster fill.
  const DEF = 'flowchart LR\n  subgraph S\n  A-->B\n  end\n  linkStyle 0 stroke:#0000cc';

  test('DARK: applied stroke clears 3:1 against the cluster fill it crosses', () => {
    const svg = buildClusteredFlow();
    const applied = reapplyLinkStyleStrokes(svg, DEF, true);
    expect(applied).toBeGreaterThanOrEqual(1);
    const edge = svg.querySelector('.edgePaths > path')!;
    const stroke = (edge as unknown as SVGElement).style.getPropertyValue('stroke');
    expect(stroke).toMatch(/^#/);
    expect(contrast(stroke, DARK_CLUSTER)).toBeGreaterThanOrEqual(3.0);
    // Still legible against the darker canvas too.
    expect(contrast(stroke, DARK_CANVAS)).toBeGreaterThanOrEqual(3.0);
  });

  test('LIGHT: reapply is a no-op (light already honours linkStyle)', () => {
    const svg = buildClusteredFlow();
    expect(reapplyLinkStyleStrokes(svg, DEF, false)).toBe(0);
  });
});

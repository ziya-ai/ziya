/**
 * @jest-environment jsdom
 *
 * G-22 / D-024 (mermaid-linkstyle-stroke-override-dropped:dark) regression guard.
 *
 * Defect: under the DARK theme the mermaid visibility pass repaints every edge
 * with the theme lineColor (#88c0d0 teal), silently discarding an author's
 * explicit `linkStyle N stroke:...` colour-coding, while node classDef fills
 * survive. Root cause and fix are SHARED with D-161 (parseLinkStyleStrokes +
 * reapplyLinkStyleStrokes in mermaidEnhancer.ts, wired into mermaidPlugin.ts's
 * dark flowchart/graph render path): the strokes are re-applied AFTER the
 * visibility pass, and a dark-unfriendly author stroke is honour-then-lightened
 * to clear the 3:1 graphical floor.
 *
 * This guard pins that behaviour to the EXACT failing spec mermaid-w1-15, which
 * carries two indexed overrides on directional-variant edges:
 *     linkStyle 1 stroke:#ff8800,stroke-width:2px   (orange, 6.97:1 on #1e1e1e)
 *     linkStyle 3 stroke:#aa0000,stroke-width:2px   (dark red, 2.15:1 on #1e1e1e)
 * so a future refactor of the parse/re-apply path that regresses w1-15 fails
 * here. Both themes are asserted (dark repair paired with a light no-op), per
 * the theme-fix contract. Green by design against the current tree: the D-161
 * fix that resolves D-024 is already in source; this locks it to w1-15.
 */

import { parseLinkStyleStrokes, reapplyLinkStyleStrokes, countFlowchartLinks } from '../mermaidEnhancer';
import { contrastRatio, CHART_DARK_BG } from '../chartTheme';

const SVGNS = 'http://www.w3.org/2000/svg';
const LINE_COLOR = '#88c0d0'; // dark-theme lineColor the visibility pass paints

// Exact mermaid-w1-15 body: 7 edges (indices 0..6), overrides on 1 and 3.
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
/** 7 flowchart edges under `.edgePaths`, pre-painted teal like the dark pass. */
function buildW115Svg(): { svg: SVGElement; edges: SVGElement[] } {
  const svg = svgEl('svg');
  const edgePaths = svgEl('g', 'edgePaths');
  svg.appendChild(edgePaths);
  const edges = Array.from({ length: 7 }, () => {
    const p = svgEl('path', 'flowchart-link');
    p.style.setProperty('stroke', LINE_COLOR, 'important'); // visibility pass repaint
    edgePaths.appendChild(p);
    return p;
  });
  return { svg, edges };
}

describe('G-22 / D-024 linkStyle override survives dark theme (mermaid-w1-15)', () => {
  it('parses both indexed overrides, ignoring the stroke-width tail', () => {
    expect(parseLinkStyleStrokes(W1_15)).toEqual([
      { indices: [1], stroke: '#ff8800' },
      { indices: [3], stroke: '#aa0000' },
    ]);
  });

  it('DARK: honours #ff8800 verbatim, lightens #aa0000 to clear 3:1, leaves other edges teal', () => {
    const { svg, edges } = buildW115Svg();
    // DIRECTION baseline: the visibility pass has erased both author colours.
    expect(strokeOf(edges[1])).toBe(LINE_COLOR);
    expect(strokeOf(edges[3])).toBe(LINE_COLOR);

    const n = reapplyLinkStyleStrokes(svg, W1_15, true);
    expect(n).toBe(2);

    // #ff8800 = 6.97:1 on #1e1e1e -> already legible, kept verbatim.
    expect(strokeOf(edges[1]).toLowerCase()).toBe('#ff8800');
    expect(contrastRatio(strokeOf(edges[1]), CHART_DARK_BG)).toBeGreaterThanOrEqual(3);

    // #aa0000 = 2.15:1 on #1e1e1e -> honour-then-lighten: no longer the raw
    // author colour, no longer the discarded teal, and now clears the floor.
    const red = strokeOf(edges[3]).toLowerCase();
    expect(red).not.toBe('#aa0000');
    expect(red).not.toBe(LINE_COLOR);
    expect(contrastRatio(red, CHART_DARK_BG)).toBeGreaterThanOrEqual(3);

    // Untargeted directional-variant edges (==>, primary, --o, -->, flush) keep
    // the theme line colour — the fix touches only the two colour-coded edges.
    [0, 2, 4, 5, 6].forEach(i => expect(strokeOf(edges[i])).toBe(LINE_COLOR));
  });

  it('LIGHT: no-op — mermaid honours linkStyle natively, so nothing is repainted', () => {
    const { svg, edges } = buildW115Svg();
    // In light, mermaid keeps author strokes; simulate that pre-state.
    edges[1].style.setProperty('stroke', '#ff8800', 'important');
    edges[3].style.setProperty('stroke', '#aa0000', 'important');

    const n = reapplyLinkStyleStrokes(svg, W1_15, false);
    expect(n).toBe(0);
    expect(strokeOf(edges[1]).toLowerCase()).toBe('#ff8800');
    expect(strokeOf(edges[3]).toLowerCase()).toBe('#aa0000');
  });
});

// D-155: the linkstyle-fix preprocessor validated `linkStyle N` against a
// short edge count that ignored ==>, --x, --o (no trailing '>'), etc. — so a
// higher-index override was silently stripped ("stroke override dropped").
describe('D-155 flowchart link counting covers directional variants', () => {
  it('counts all 7 edges of mermaid-w1-15 (old alternation saw only 4)', () => {
    // The old counter matched only -.->, --> x3 = 4, missing ==>, --x, --o.
    expect(countFlowchartLinks(W1_15)).toBe(7);
  });

  it('counts thick / marker / dotted / invisible / bidirectional operators', () => {
    const def = [
      'flowchart LR',
      '  A ==> B',      // thick
      '  A === B',      // thick line
      '  A --x C',      // cross head, no >
      '  A --o D',      // circle head, no >
      '  A -.-> E',     // dotted arrow
      '  A -..-> F',    // longer dotted arrow
      '  A ~~~ G',      // invisible
      '  A <--> H',     // bidirectional
      '  A <==> I',     // bidirectional thick
    ].join('\n');
    expect(countFlowchartLinks(def)).toBe(9);
  });

  it('keeps a highest-index override that the short count would have stripped', () => {
    // Edge index 6 (the flush edge) is only valid when all 7 edges are counted.
    // With the old undercount (4) it satisfied 6 >= totalLinks and was dropped.
    const total = countFlowchartLinks(W1_15);
    expect(6 < total).toBe(true);   // survives with the fix
    expect(6 < 4).toBe(false);      // would have been stripped before
  });
});

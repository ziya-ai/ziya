/**
 * The skip-edge rerouter must recognise the edges Mermaid 11/12 emits.
 *
 * It looked only for `.edgePath` groups with LS-/LE- classes (Mermaid 10).
 * Mermaid 11+ emits `path[data-edge="true"]` with `data-id="L_<src>_<tgt>_<n>"`
 * and prefixes node ids with the render id (`m123-flowchart-A-0`), so the
 * rerouter found no edges and never ran.
 *
 * shouldRerouteEdges on the modern DOM fails against the unpatched code (0
 * edges counted).
 */
import {
  shouldRerouteEdges, normalizeNodeId, parseModernEdgeId,
} from '../mermaidEdgeRerouter';

const node = (id: string) => `<g class="node default" id="m1793-flowchart-${id}-0"><rect width="40" height="20"></rect></g>`;
const edge = (s: string, t: string) =>
  `<path data-edge="true" data-id="L_${s}_${t}_0" class="flowchart-link" d="M0,0L10,10"></path>`;

const svgOf = (inner: string): SVGElement => {
  document.body.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;
  return document.querySelector('svg') as unknown as SVGElement;
};

describe('normalizeNodeId', () => {
  it('strips the render-id prefix and the counter', () => {
    expect(normalizeNodeId('m1793174482532-flowchart-my_node-12')).toBe('my_node');
    expect(normalizeNodeId('flowchart-A-0')).toBe('A');
  });
});

describe('parseModernEdgeId', () => {
  const ids = new Set(['my_node', 'B', 'C_D', 'E']);
  it('splits L_<src>_<tgt>_<n> using the known node ids', () => {
    expect(parseModernEdgeId('L_my_node_B_0', ids)).toEqual({ sourceId: 'my_node', targetId: 'B' });
    expect(parseModernEdgeId('L_E_C_D_3', ids)).toEqual({ sourceId: 'E', targetId: 'C_D' });
  });
  it('returns null for unknown ends or another id scheme', () => {
    expect(parseModernEdgeId('L_X_Y_0', ids)).toBeNull();
    expect(parseModernEdgeId('edge0', ids)).toBeNull();
  });
});

describe('shouldRerouteEdges', () => {
  it('counts Mermaid 11/12 edges', () => {
    const svg = svgOf(
      `<g class="edgePaths">${edge('A', 'B')}${edge('B', 'C')}${edge('C', 'D')}${edge('A', 'D')}</g>` +
      `<g class="nodes">${['A', 'B', 'C', 'D'].map(node).join('')}</g>`);
    expect(shouldRerouteEdges(svg)).toBe(true);
  });

  it('still counts legacy .edgePath groups', () => {
    const legacy = (s: string, t: string) => `<g class="edgePath LS-${s} LE-${t}"><path d="M0,0L1,1"></path></g>`;
    const svg = svgOf(`${legacy('A', 'B')}${legacy('B', 'C')}${legacy('C', 'D')}${legacy('A', 'D')}` +
      ['A', 'B', 'C', 'D'].map(node).join(''));
    expect(shouldRerouteEdges(svg)).toBe(true);
  });

  it('declines a sparse graph', () => {
    const svg = svgOf(`${edge('A', 'B')}` + ['A', 'B', 'C', 'D'].map(node).join(''));
    expect(shouldRerouteEdges(svg)).toBe(false);
  });
});

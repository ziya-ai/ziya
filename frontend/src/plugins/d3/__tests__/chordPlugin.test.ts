/**
 * Tests for the chord diagram plugin.
 *
 * Focused on the canHandle type guard and the links-form → matrix
 * normalization, which is where the LLM-authored input lands.  Full
 * SVG render tests would require jsdom with SVG support.
 */

// ── Replicate the type guard from chordPlugin.ts ─────────────────────

function isChordSpec(spec: any): boolean {
  if (typeof spec !== 'object' || spec === null) return false;
  const type = spec.type;
  if (type !== 'chord' && type !== 'chord-directed') return false;
  if (Array.isArray(spec.matrix) && spec.matrix.length > 0
      && Array.isArray(spec.matrix[0])) return true;
  const nodes = spec.nodes || spec.data?.nodes;
  const links = spec.links || spec.data?.links;
  return Array.isArray(nodes) && Array.isArray(links) && nodes.length > 0;
}

// ── Replicate buildMatrix (the only logic worth pinning) ─────────────

interface ChordNode { id: string; label?: string; color?: string; }
interface ChordLink { source: string; target: string; value?: number; }

function buildMatrix(nodes: ChordNode[], links: ChordLink[]): number[][] {
  const n = nodes.length;
  const idx = new Map<string, number>();
  nodes.forEach((node, i) => idx.set(node.id, i));
  const matrix: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  for (const link of links) {
    const s = idx.get(link.source);
    const t = idx.get(link.target);
    if (s === undefined || t === undefined) continue;
    matrix[s][t] += Number(link.value ?? 1);
  }
  return matrix;
}

describe('isChordSpec (canHandle)', () => {
  it('accepts type "chord" with matrix', () => {
    expect(isChordSpec({ type: 'chord', matrix: [[0, 1], [1, 0]] })).toBe(true);
  });

  it('accepts type "chord-directed" with matrix', () => {
    expect(isChordSpec({ type: 'chord-directed', matrix: [[0, 1], [1, 0]] })).toBe(true);
  });

  it('accepts type "chord" with top-level nodes/links', () => {
    expect(isChordSpec({
      type: 'chord',
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'B', value: 5 }],
    })).toBe(true);
  });

  it('accepts type "chord" with data.nodes/data.links', () => {
    expect(isChordSpec({
      type: 'chord',
      data: {
        nodes: [{ id: 'A' }, { id: 'B' }],
        links: [{ source: 'A', target: 'B' }],
      },
    })).toBe(true);
  });

  it('rejects spec with wrong type', () => {
    expect(isChordSpec({ type: 'force-directed', nodes: [{ id: 'A' }], links: [] })).toBe(false);
  });

  it('rejects spec with empty matrix', () => {
    expect(isChordSpec({ type: 'chord', matrix: [] })).toBe(false);
  });

  it('rejects spec with empty nodes', () => {
    expect(isChordSpec({ type: 'chord', nodes: [], links: [] })).toBe(false);
  });

  it('rejects null / undefined / strings', () => {
    expect(isChordSpec(null)).toBe(false);
    expect(isChordSpec(undefined)).toBe(false);
    expect(isChordSpec('chord')).toBe(false);
  });
});

describe('buildMatrix', () => {
  it('builds an N×N matrix preserving node order', () => {
    const nodes = [{ id: 'A' }, { id: 'B' }, { id: 'C' }];
    const links = [
      { source: 'A', target: 'B', value: 5 },
      { source: 'B', target: 'C', value: 3 },
      { source: 'C', target: 'A', value: 1 },
    ];
    expect(buildMatrix(nodes, links)).toEqual([
      [0, 5, 0],
      [0, 0, 3],
      [1, 0, 0],
    ]);
  });

  it('defaults missing value to 1', () => {
    const nodes = [{ id: 'A' }, { id: 'B' }];
    expect(buildMatrix(nodes, [{ source: 'A', target: 'B' }]))
      .toEqual([[0, 1], [0, 0]]);
  });

  it('sums repeated edges between the same pair', () => {
    const nodes = [{ id: 'A' }, { id: 'B' }];
    const links = [
      { source: 'A', target: 'B', value: 2 },
      { source: 'A', target: 'B', value: 3 },
    ];
    expect(buildMatrix(nodes, links)).toEqual([[0, 5], [0, 0]]);
  });

  it('silently skips links with unknown source or target', () => {
    const nodes = [{ id: 'A' }, { id: 'B' }];
    const links = [
      { source: 'A', target: 'B', value: 1 },
      { source: 'A', target: 'GHOST', value: 99 },
      { source: 'GHOST', target: 'B', value: 99 },
    ];
    expect(buildMatrix(nodes, links)).toEqual([[0, 1], [0, 0]]);
  });
});

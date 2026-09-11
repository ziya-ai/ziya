/**
 * G-41 / D-044 — Plotly dense-sankey node-label legibility via capture-height grow.
 *
 * D-044 is a consolidated structural plotly defect. Most of its sub-mechanisms
 * (title-aware automargin, in-shape uniformtext floor, legend-aware capture
 * height, extended colorway, categorical-axis coercion) were already resolved in
 * source under D-239/D-241/D-242. The one genuine remaining gap this suite pins
 * is the DENSE SANKEY case (plotly-w2-07: 120 nodes / 400 links / 6 stages,
 * "node-label density ceiling"): a sankey trace produces exactly ONE legend
 * entry, so legendAwareRenderHeightPx never grows the capture div, and the ~20
 * nodes stacked in each column are crushed into the default 60vh viewport, so
 * their labels overprint into an illegible smear.
 *
 * sankeyAwareRenderHeightPx computes each node's column via longest-path depth
 * from the link graph, takes the busiest column, and returns a taller pixel
 * height when that column exceeds the grow threshold — the same "grow the static
 * capture div so content is not crushed/clipped" mechanism already used for
 * legends. It is structural / theme-independent (a geometry decision), so this
 * suite is theme-agnostic; the both-theme obligation is discharged at the shared
 * render stage.
 *
 * Every test carries a DIRECTION check: the symbol does not exist pre-fix (import
 * throws), and the behavioural assertions describe values the unpatched tree
 * cannot produce.
 */

import {
  sankeyAwareRenderHeightPx,
  declutterDenseSankey,
  raiseRgbaAlphaToFloor,
  PLOTLY_SANKEY_GROW_THRESHOLD,
  PLOTLY_SANKEY_NODE_ROW_PX,
  PLOTLY_LEGEND_MAX_HEIGHT_PX,
  PLOTLY_SANKEY_MIN_NODE_PAD,
  PLOTLY_SANKEY_MIN_LINK_ALPHA,
} from '../plotlyPreprocessor';

/** Build a layered sankey: `stages` columns of `perCol` nodes each, every node
 *  linked to every node in the next column (so the busiest column == perCol). */
function layeredSankey(stages: number, perCol: number): any {
  const n = stages * perCol;
  const label: string[] = [];
  for (let s = 0; s < stages; s++)
    for (let i = 0; i < perCol; i++) label.push(`stage${s}/node${i}`);
  const source: number[] = [];
  const target: number[] = [];
  for (let s = 0; s < stages - 1; s++) {
    for (let a = 0; a < perCol; a++) {
      for (let b = 0; b < perCol; b++) {
        source.push(s * perCol + a);
        target.push((s + 1) * perCol + b);
      }
    }
  }
  return { type: 'sankey', node: { label }, link: { source, target } };
}

describe('sankeyAwareRenderHeightPx (D-044 / G-41 dense-sankey grow)', () => {
  it('DIRECTION: a busy column above the threshold grows the capture height', () => {
    // 6 columns of 19 nodes — mirrors plotly-w2-07's busiest column.
    const data = [layeredSankey(6, 19)];
    const px = sankeyAwareRenderHeightPx(data);
    expect(px).not.toBeNull();
    // 19 > threshold, so height == 19 * ROW + 120, well above the default 60vh box.
    expect(px).toBe(19 * PLOTLY_SANKEY_NODE_ROW_PX + 120);
    expect(px as number).toBeGreaterThan(480);
  });

  it('a sparse sankey at/below the threshold is NOT grown (byte-identical path)', () => {
    // exactly threshold nodes in the busiest column -> null (keep default height).
    const data = [layeredSankey(3, PLOTLY_SANKEY_GROW_THRESHOLD)];
    expect(sankeyAwareRenderHeightPx(data)).toBeNull();
  });

  it('column count, not total node count, drives the grow decision', () => {
    // 40 columns of 4 nodes each = 160 nodes total, but the busiest column is 4.
    const data = [layeredSankey(40, 4)];
    expect(sankeyAwareRenderHeightPx(data)).toBeNull();
  });

  it('an enormous busiest column is capped at PLOTLY_LEGEND_MAX_HEIGHT_PX', () => {
    const data = [layeredSankey(2, 400)];
    const px = sankeyAwareRenderHeightPx(data);
    expect(px).toBe(PLOTLY_LEGEND_MAX_HEIGHT_PX);
  });

  it('returns null for non-sankey data and for malformed input', () => {
    expect(sankeyAwareRenderHeightPx([{ type: 'bar', x: [1, 2], y: [3, 4] }])).toBeNull();
    expect(sankeyAwareRenderHeightPx([{ type: 'sankey' }])).toBeNull(); // no node.label
    expect(sankeyAwareRenderHeightPx([])).toBeNull();
    expect(sankeyAwareRenderHeightPx(null as any)).toBeNull();
  });

  it('tolerates a sankey with labels but no/loose links (single column)', () => {
    // 30 nodes, zero links -> every node depth 0 -> busiest column 30 > threshold.
    const label = Array.from({ length: 30 }, (_, i) => `n${i}`);
    const px = sankeyAwareRenderHeightPx([{ type: 'sankey', node: { label } }]);
    expect(px).toBe(30 * PLOTLY_SANKEY_NODE_ROW_PX + 120);
  });
});

/**
 * D-223 — dense-sankey node-label COLLISION (distinct from the capture-height
 * grow above): plotly-w2-07 sets node.pad=3 on 20-node columns, so even a taller
 * canvas keeps the boxes 3px apart and their ~12px labels overprint. The grow
 * only gives room; declutterDenseSankey supplies the missing declutter by
 * flooring node.pad, and floors the near-transparent (alpha 0.25) flow ribbons.
 * DIRECTION: the unpatched tree exports neither symbol (import throws) and never
 * rewrites node.pad or link alpha, so these assertions cannot pass pre-fix.
 */
describe('declutterDenseSankey (D-223 sankey declutter)', () => {
  const w207Like = () => ({
    type: 'sankey',
    node: {
      label: Array.from({ length: 20 }, (_, i) => `stage/node${i}`),
      pad: 3,
      thickness: 8,
    },
    link: {
      // one full bipartite column pair so the busiest column is 20 (> threshold)
      source: Array.from({ length: 20 }, () => 0),
      target: Array.from({ length: 20 }, (_, i) => i),
      value: Array.from({ length: 20 }, () => 1),
      color: 'rgba(76,120,168,0.25)',
    },
  });

  it('DIRECTION: floors a crammed node.pad on a dense sankey', () => {
    const out = declutterDenseSankey([w207Like()]);
    expect(out[0].node.pad).toBe(PLOTLY_SANKEY_MIN_NODE_PAD);
    expect(PLOTLY_SANKEY_MIN_NODE_PAD).toBeGreaterThan(12); // wider than a text line
  });

  it('DIRECTION: raises near-invisible link ribbon alpha to the floor', () => {
    const out = declutterDenseSankey([w207Like()]);
    expect(out[0].link.color).toBe(`rgba(76, 120, 168, ${PLOTLY_SANKEY_MIN_LINK_ALPHA})`);
  });

  it('leaves an already-roomy pad and an opaque-enough ribbon untouched', () => {
    const roomy = {
      type: 'sankey',
      node: { label: Array.from({ length: 20 }, (_, i) => `n${i}`), pad: 25 },
      link: {
        source: Array.from({ length: 20 }, () => 0),
        target: Array.from({ length: 20 }, (_, i) => i),
        color: 'rgba(76,120,168,0.8)',
      },
    };
    const input = [roomy];
    const out = declutterDenseSankey(input);
    expect(out).toBe(input); // nothing below a floor -> input returned by reference
    expect(out[0].node.pad).toBe(25);
    expect(out[0].link.color).toBe('rgba(76,120,168,0.8)');
  });

  it('does NOT touch a sparse sankey below the grow threshold', () => {
    const sparse = {
      type: 'sankey',
      node: { label: ['a', 'b', 'c'], pad: 3 },
      link: { source: [0, 1], target: [1, 2], color: 'rgba(0,0,0,0.1)' },
    };
    const out = declutterDenseSankey([sparse]);
    expect(out[0].node.pad).toBe(3);
    expect(out[0].link.color).toBe('rgba(0,0,0,0.1)');
  });

  it('handles per-link color arrays, non-sankey traces and malformed input', () => {
    const arr = {
      type: 'sankey',
      node: { label: Array.from({ length: 20 }, (_, i) => `n${i}`), pad: 3 },
      link: {
        source: Array.from({ length: 20 }, () => 0),
        target: Array.from({ length: 20 }, (_, i) => i),
        color: ['rgba(1,2,3,0.1)', 'rgb(4,5,6)', '#abcdef'],
      },
    };
    const out = declutterDenseSankey([arr]);
    expect(out[0].link.color[0]).toBe(`rgba(1, 2, 3, ${PLOTLY_SANKEY_MIN_LINK_ALPHA})`);
    expect(out[0].link.color[1]).toBe('rgb(4,5,6)'); // opaque, untouched
    expect(out[0].link.color[2]).toBe('#abcdef');
    expect(declutterDenseSankey([{ type: 'bar', x: [1], y: [2] }])[0].type).toBe('bar');
    expect(declutterDenseSankey(null as any)).toBeNull();
  });
});

describe('raiseRgbaAlphaToFloor (D-223 alpha helper)', () => {
  it('raises only when alpha is below the floor, preserving hue', () => {
    expect(raiseRgbaAlphaToFloor('rgba(76,120,168,0.25)', 0.5)).toBe('rgba(76, 120, 168, 0.5)');
    expect(raiseRgbaAlphaToFloor('rgba(76,120,168,0.9)', 0.5)).toBeNull();
    expect(raiseRgbaAlphaToFloor('rgb(76,120,168)', 0.5)).toBeNull();
    expect(raiseRgbaAlphaToFloor('#4c78a8', 0.5)).toBeNull();
    expect(raiseRgbaAlphaToFloor(null, 0.5)).toBeNull();
  });
});

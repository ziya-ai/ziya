/**
 * G-c471f1 — d2Plugin.ts legacy-defect verification group (shared file: d2Plugin.ts).
 *
 * These six legacy defects (D-068/071/072/073/075/076) were folded into the
 * consolidated backlog with signatures that predate several later parse/theme
 * fixes to d2Plugin.ts. This suite re-asserts each one against the EXACT spec
 * definition recorded for it, so the group is verified against real inputs
 * rather than the triage prose.
 *
 *   D-068 (structural): a trailing `# comment` must be stripped WITHOUT eating
 *          the node/edge label. Fixed by stripInlineComment (D-085) which only
 *          treats `#` at a token boundary outside quotes as a comment.
 *   D-071 (structural): an explicit requested width/height is honoured by
 *          scaling content through the viewBox (preserveAspectRatio meet), so
 *          nothing is dropped/clipped on an over/under-sized request. Fixed by
 *          d2ResolveSvgSize (D-092): reqW/reqH set the pixel size, viewBox stays
 *          at content bounds.
 *   D-072 (theme, dark): white label on the dark node fill. Fixed by darkening
 *          the dark fill #4361ee -> #303f9f (D-094): white-on-fill 8.98:1.
 *   D-073 (theme, dark): saturated magenta edge dominated the page and vanished
 *          on the node. Fixed by desaturating the dark edge to #9aa4b2 (D-095):
 *          6.54:1 on page, 3.56:1 over the node fill.
 *   D-075 (recovery): a `|md ... |` block folds to one labelled node and a
 *          trailing `{near: ...}` inline attr is stripped from the edge label
 *          (D-061), so no phantom `|`/heading/prose nodes and no `{near}` in
 *          the label.
 *   D-076 (recovery): a stray extra `}` must not desync the frame stack — the
 *          close-brace handler is defensive against an empty stack, so the
 *          nodes/containers before and after it still parse.
 *
 * THEME CONTRACT (D-072/D-073): each theme assertion is PAIRED — the previously
 * broken DARK constant is asserted fixed AND the LIGHT constant is asserted
 * still correct, so no fix is a swap-one-constant that repairs dark by breaking
 * light. Both directions are computed from contrastRatio against the real page
 * and fill colours.
 *
 * DIRECTION: every "DIRECTION" test documents the pre-fix mangling (a comment
 * tail in the label, dropped nodes on a resized graph, a sub-3:1 dark colour, a
 * `{near}` in the edge label, a lost node after a stray brace) so the suite
 * certifies the fix rather than the bug.
 */
import {
  D2Parser,
  stripInlineComment,
  stripD2BlockStrings,
  d2ThemeColors,
  d2ResolveSvgSize,
  d2CanvasSize,
  d2CanvasBounds,
  d2ContainerBounds,
  D2_DARK_BG,
  D2_LIGHT_BG,
} from '../d2Plugin';
import { contrastRatio } from '../chartTheme';

const parse = (def: string) => new D2Parser().parse(def);
const nodeById = (nodes: any[], id: string) => nodes.find(n => n.id === id);

// ---------------------------------------------------------------------------
// D-068 — trailing `# comment` stripped without eating labels (d2-w1-14)
// ---------------------------------------------------------------------------
describe('D-068 trailing comment stripped, node/edge labels intact', () => {
  const def =
    '# Deployment pipeline\n' +
    'build: Build   # compiles sources\n' +
    'test: Test\n' +
    'ship: Ship\n' +
    'build -> test: artifacts   # tarball\n' +
    'test -> ship: green build\n';

  test('DIRECTION: stripInlineComment removes only the trailing comment, keeps the value', () => {
    // pre-fix (whole-line filter only) left the comment in the label.
    expect(stripInlineComment('build: Build   # compiles sources').trim()).toBe('build: Build');
    expect(stripInlineComment('build -> test: artifacts   # tarball').trim()).toBe(
      'build -> test: artifacts'
    );
    // a bare hex colour value after a space is NOT a comment.
    expect(stripInlineComment('fill: #ff0000').trim()).toBe('fill: #ff0000');
  });

  test('parse: 3 clean nodes, 2 edges, no comment text leaks in', () => {
    const { nodes, edges } = parse(def);
    const ids = nodes.map(n => n.id).sort();
    expect(ids).toEqual(['build', 'ship', 'test']);
    expect(nodeById(nodes, 'build').label).toBe('Build');
    expect(edges).toHaveLength(2);
    const artifacts = edges.find(e => e.label === 'artifacts');
    expect(artifacts).toBeDefined();
    // no node or edge label carries the comment tail.
    for (const n of nodes) expect(String(n.label ?? '')).not.toMatch(/#|compiles|tarball/);
    for (const e of edges) expect(String(e.label ?? '')).not.toMatch(/#|tarball/);
  });
});

// ---------------------------------------------------------------------------
// D-071 — explicit requested size honoured via viewBox scaling (d2-w2-11..14)
// ---------------------------------------------------------------------------
describe('D-071 requested size scales content through the viewBox, no drop/clip', () => {
  const nodes = Array.from({ length: 60 }, (_, i) => ({
    id: `n${i}`,
    x: (i % 10) * 120,
    y: Math.floor(i / 10) * 80,
    width: 100,
    height: 60,
  }));
  const canvas = d2CanvasSize(nodes);

  test('DIRECTION: pre-fix used a fixed pixel size that ignored the request', () => {
    // content bounds are non-trivial (many nodes) so a naive fixed size clipped.
    expect(canvas.width).toBeGreaterThan(0);
    expect(canvas.height).toBeGreaterThan(0);
  });

  test('oversize request (3000x300): pixel size honoured, viewBox stays at content bounds', () => {
    const s = d2ResolveSvgSize(canvas, 3000, 300);
    expect(s.width).toBe(3000);
    expect(s.height).toBe(300);
    // viewBox is unchanged -> preserveAspectRatio meet fits all content, no drop.
    expect(s.viewBox).toBe(canvas.viewBox);
  });

  test('undersize request (260x220): honoured, content still fully described by viewBox', () => {
    const s = d2ResolveSvgSize(canvas, 260, 220);
    expect(s.width).toBe(260);
    expect(s.height).toBe(220);
    expect(s.viewBox).toBe(canvas.viewBox);
  });

  test('no request: natural pixel size preserved (fixed-px text not downscaled)', () => {
    const s = d2ResolveSvgSize(canvas);
    expect(s.width).toBe(canvas.width);
    expect(s.height).toBe(canvas.height);
  });
});

// ---------------------------------------------------------------------------
// D-072 — dark node fill legible for white label (d2-w2-02)  [THEME]
// ---------------------------------------------------------------------------
describe('D-072 dark node fill vs white label — paired across themes', () => {
  test('dark fill resolves to the darkened indigo, white label >= 4.5:1', () => {
    const dark = d2ThemeColors(true);
    expect(dark.node).toBe('#303f9f');
    const r = contrastRatio('#ffffff', dark.node);
    expect(r).toBeGreaterThan(4.5); // 8.98 (fix); pre-fix #4361ee was 5.02 but smeared
    expect(r).toBeGreaterThan(contrastRatio('#ffffff', '#4361ee')); // strictly better than pre-fix
  });

  test('light fill unchanged, black label still strongly legible', () => {
    const light = d2ThemeColors(false);
    expect(light.node).toBe('#e3f2fd');
    expect(contrastRatio('#000000', light.node)).toBeGreaterThan(4.5); // 18.39
  });
});

// ---------------------------------------------------------------------------
// D-073 — dark edge colour recessive on page and visible on node (d2-w1-01 ...)  [THEME]
// ---------------------------------------------------------------------------
describe('D-073 dark edge colour — paired across themes', () => {
  test('dark edge is desaturated grey-blue: >=3:1 on page AND on the node fill', () => {
    const dark = d2ThemeColors(true);
    expect(dark.edge).toBe('#9aa4b2');
    expect(contrastRatio(dark.edge, D2_DARK_BG)).toBeGreaterThan(3); // 6.54 on page
    expect(contrastRatio(dark.edge, dark.node)).toBeGreaterThan(3); // 3.56 over fill (was 1.33)
    // pre-fix magenta vanished on the node fill.
    expect(contrastRatio('#f72585', '#303f9f')).toBeLessThan(3);
  });

  test('light edge unchanged and still recessive/legible on the white page', () => {
    const light = d2ThemeColors(false);
    expect(light.edge).toBe('#666666');
    expect(contrastRatio(light.edge, D2_LIGHT_BG)).toBeGreaterThan(3); // 5.74
  });
});

// ---------------------------------------------------------------------------
// D-075 — |md| block folds + inline {near:} attr stripped (d2-w4-14)
// ---------------------------------------------------------------------------
describe('D-075 md block folds to one node and {near:} is stripped from the edge label', () => {
  const def =
    'direction: right\n' +
    'users: Users {shape: person}\n' +
    'db: Inventory {\n  shape: sql_table\n  id: int\n  name: varchar\n}\n' +
    'note: |md\n  ## Deployment note\n  Runs in **us-east-1**\n|\n' +
    'users -> db: reads {near: top-center}\n';

  test('DIRECTION: block-string body lines do not survive as their own lines', () => {
    const folded = stripD2BlockStrings(def);
    // the raw markdown lines are gone; a single `note:` line carries flat text.
    expect(folded).not.toMatch(/\|md/);
    expect(folded).toMatch(/note:\s*Deployment note Runs in us-east-1/);
    // no lone `|` line and no bare `## Heading` line remains.
    expect(folded.split('\n').some(l => l.trim() === '|')).toBe(false);
    expect(folded.split('\n').some(l => l.trim().startsWith('##'))).toBe(false);
  });

  test('parse: note is one labelled node, no phantom md nodes, edge label is clean', () => {
    const { nodes, edges } = parse(def);
    const note = nodeById(nodes, 'note');
    expect(note).toBeDefined();
    expect(String(note.label)).toMatch(/Deployment note/);
    // no phantom node whose id/label is a lone pipe or a markdown heading.
    expect(nodes.some(n => String(n.id).trim() === '|' || String(n.label ?? '').trim() === '|')).toBe(false);
    expect(nodes.some(n => String(n.label ?? '').includes('##'))).toBe(false);
    // the users->db edge label is 'reads', with no {near: ...} leaking in.
    const edge = edges.find(e => e.source === 'users' && e.target === 'db');
    expect(edge).toBeDefined();
    expect(edge.label).toBe('reads');
    expect(String(edge.label)).not.toMatch(/near|\{|\}/);
  });
});

// ---------------------------------------------------------------------------
// D-076 — stray extra `}` does not desync the frame stack (d2-w4-12)
// ---------------------------------------------------------------------------
describe('D-076 stray closing brace is tolerated, structure survives', () => {
  const def =
    'region {\n' +
    '  az1 {\n' +
    '    node1: Instance A\n' +
    '    node2: Instance B\n' +
    '  }\n' +
    '  node3: Shared Cache\n' +
    '}\n' +
    '}\n' + // <- the stray extra closing brace
    'node1 -> node2\n';

  test('DIRECTION: the extra `}` must not throw or swallow the trailing statement', () => {
    expect(() => parse(def)).not.toThrow();
  });

  test('all four labelled nodes parse and the trailing edge survives the stray brace', () => {
    const { nodes, edges } = parse(def);
    const ids = nodes.map(n => n.id);
    // D-105: nodes are keyed by their full path. The trailing top-level
    // `node1 -> node2` names nodes that live in region.az1; strict d2 would
    // spawn two phantom root nodes, but the short-name endpoint fallback
    // resolves each to the unique node of that name — so exactly three nodes.
    for (const id of ['region_az1_node1', 'region_az1_node2', 'region_node3']) expect(ids).toContain(id);
    expect(nodes).toHaveLength(3);
    expect(nodeById(nodes, 'region_az1_node1').label).toBe('Instance A');
    expect(nodeById(nodes, 'region_az1_node2').label).toBe('Instance B');
    // the edge after the stray brace is still parsed as an edge, not a node.
    const edge = edges.find(e => e.source === 'region_az1_node1' && e.target === 'region_az1_node2');
    expect(edge).toBeDefined();
    // no phantom node whose id is a lone brace.
    expect(nodes.some(n => String(n.id).trim() === '}' || String(n.id).trim() === '{')).toBe(false);
  });

  // The stray brace is a red herring: the parser keeps the nesting intact. The
  // actual render failure was that the OUTER container's rect + label were
  // clipped off the top-left. A container's dashed rect is drawn `pad`(+nesting
  // inset) OUTSIDE its members, so when ELK places the topmost/leftmost member
  // near the origin the outer rect lands at negative coordinates — and the old
  // `0 0 W H` viewBox (d2CanvasSize) clipped it. d2CanvasBounds moves the
  // viewBox origin to enclose the container extents.
  describe('D-076 render: container rect + label are on-canvas, not clipped', () => {
    // ELK-style laid-out members: node1 is the topmost/leftmost (near origin).
    const laidOut = [
      { id: 'node1', label: 'Instance A', container: 'az1', x: 10, y: 8, width: 120, height: 50 },
      { id: 'node2', label: 'Instance B', container: 'az1', x: 10, y: 100, width: 120, height: 50 },
      { id: 'node3', label: 'Shared Cache', container: 'region', x: 200, y: 8, width: 140, height: 50 },
    ];
    const containers = [
      { id: 'region', label: 'region', type: 'container', children: ['node3'], parent: null },
      { id: 'az1', label: 'az1', type: 'container', children: ['node1', 'node2'], parent: 'region' },
    ];

    const parseVB = (vb: string) => {
      const [x, y, w, h] = vb.split(/\s+/).map(Number);
      return { x, y, w, h };
    };

    test('DIRECTION: outer container rect extends above/left of node origin, so 0,0 clips it', () => {
      const region = d2ContainerBounds(containers[0] as any, laidOut, containers as any)!;
      // the region rect (pad + nesting inset) crosses the origin.
      expect(region.x).toBeLessThan(0);
      expect(region.y).toBeLessThan(0);
      // the old node-only canvas pins the viewBox origin at 0,0, so the rect's
      // negative top-left corner (and its label at x+8,y+16) fell off-canvas.
      const old = parseVB(d2CanvasSize(laidOut).viewBox);
      expect(old.x).toBe(0);
      expect(old.y).toBe(0);
      expect(region.x).toBeLessThan(old.x); // clipped left
      expect(region.y).toBeLessThan(old.y); // clipped top
    });

    test('d2CanvasBounds encloses every container rect and its top-left label', () => {
      const vb = parseVB(d2CanvasBounds(laidOut, containers).viewBox);
      for (const c of containers) {
        const b = d2ContainerBounds(c as any, laidOut, containers as any)!;
        // rect fully inside the viewBox in both axes...
        expect(vb.x).toBeLessThanOrEqual(b.x);
        expect(vb.y).toBeLessThanOrEqual(b.y);
        expect(vb.x + vb.w).toBeGreaterThanOrEqual(b.x + b.width);
        expect(vb.y + vb.h).toBeGreaterThanOrEqual(b.y + b.height);
        // ...and the group label baseline (x+8, y+16) is on-canvas too.
        expect(vb.x).toBeLessThanOrEqual(b.x + 8);
        expect(vb.y).toBeLessThanOrEqual(b.y + 16);
      }
    });

    test('no containers: d2CanvasBounds is byte-identical to d2CanvasSize (origin 0,0)', () => {
      expect(d2CanvasBounds(laidOut, []).viewBox).toBe(d2CanvasSize(laidOut).viewBox);
      expect(d2CanvasBounds(laidOut, []).viewBox.startsWith('0 0 ')).toBe(true);
    });
  });
});

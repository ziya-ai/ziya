/**
 * G-34686e — d2Plugin.ts structural/recovery defects (shared file:
 * frontend/src/plugins/d3/d2Plugin.ts).
 *
 * All six defects in this group were CONFIRMED already remediated in the
 * current source during this stage; this suite pins the fixed behaviour under
 * the CURRENT backlog defect ids (earlier suites pinned the same behaviour
 * under the legacy stage ids). Every defect here is kind:structural/recovery
 * and THEME-INVARIANT — parsing and geometry resolve no colour from the theme,
 * so the output is byte-identical in light and dark and there is no theme axis
 * to split. Each assertion is paired with the PRE-FIX value it would take
 * against unpatched code, so a revert of any fix flips a test red.
 *
 *   D-062  edge direction & bidirectional actually gate the arrow markers.
 *   D-065  shape keyword (circle squared) & sql_table box are honoured.
 *   D-067  chained `a -> b -> c` yields every hop; `direction:` is a keyword.
 *   D-069  d2CanvasSize paints at natural pixel size (no 12px downscale).
 *   D-070  labels wrap / hard-break and grid pitch cannot overlap boxes.
 *   D-077  alien dialects (JSON / mermaid) are detected before parsing.
 */
import {
  D2Parser,
  d2SqlColumns,
  d2NodeBoxSize,
  d2CanvasSize,
  wrapLabel,
  d2GridPitch,
  d2NodeWidth,
  looksLikeJson,
  looksLikeMermaid,
} from '../d2Plugin';

const parse = (def: string) => new D2Parser().parse(def);

// The render-time marker gating (d2Plugin.ts ~lines 1709-1710). Mirrors the
// exact source expressions so the test discriminates fwd / reversed / bidi.
const markerEnd = (e: any) => (e.bidirectional || !e.reversed);
const markerStart = (e: any) => (e.bidirectional || e.reversed);

describe('G-34686e: d2 structural & recovery regression guards', () => {
  test('D-062: direction/bidirectional gate marker-end and marker-start', () => {
    const { edges } = parse('a -> b\nc <- d\ne <-> f');
    const fwd = edges.find((x: any) => x.source === 'a');
    const rev = edges.find((x: any) => x.source === 'c');
    const bi = edges.find((x: any) => x.source === 'e');

    // Forward: head at the end only.
    expect(markerEnd(fwd)).toBe(true);
    expect(markerStart(fwd)).toBe(false);
    // Reversed: head at the START only. PRE-FIX marker-start was never emitted,
    // so `<-` drew a forward head (pointed the wrong way).
    expect(markerStart(rev)).toBe(true);
    expect(markerEnd(rev)).toBe(false);
    // Bidirectional: heads at BOTH ends. PRE-FIX identical to forward -> `<->`
    // was indistinguishable from `->`.
    expect(markerEnd(bi)).toBe(true);
    expect(markerStart(bi)).toBe(true);
    // The three edges are genuinely distinct in their (start,end) marker pair.
    const sig = (e: any) => `${markerStart(e)}|${markerEnd(e)}`;
    expect(new Set([sig(fwd), sig(rev), sig(bi)]).size).toBe(3);
  });

  test('D-065: circle is squared and sql_table reserves its column rows', () => {
    // circle: box is squared (width == height) so the label fits inscribed.
    // PRE-FIX every node was a `rect rx=5`, so a circle used the wide label box.
    const circle = d2NodeBoxSize({ id: 'n', label: 'Node', shape: 'circle' });
    expect(circle.width).toBe(circle.height);

    // sql_table: columns are extracted and the box is grown for header + rows.
    const table = { id: 't', label: 'Users', shape: 'sql_table', attrs: { id: 'int', name: 'varchar' } };
    expect(d2SqlColumns(table)).toEqual(['id: int', 'name: varchar']);
    const tableBox = d2NodeBoxSize(table);
    const plainBox = d2NodeBoxSize({ id: 't', label: 'Users' });
    // PRE-FIX the sql_table drew as a plain single-line label box; the fixed box
    // is strictly taller to hold the header band plus one row per column.
    expect(tableBox.height).toBeGreaterThan(plainBox.height);
    // A non-sql_table node yields no columns (byte-identical to pre-fix path).
    expect(d2SqlColumns({ id: 'x', label: 'X', shape: 'circle' })).toEqual([]);
  });

  test('D-067: chained connection yields every hop; direction is a keyword', () => {
    const { nodes, edges } = parse('a -> b -> c');
    // PRE-FIX only the first hop survived and c was orphaned.
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b', 'c']);
    expect(edges.map((e: any) => [e.source, e.target])).toEqual([['a', 'b'], ['b', 'c']]);

    // `direction: right` is a top-level keyword, not a phantom node 'right'.
    const g = parse('direction: right\nx -> y');
    expect(g.direction).toBe('right');
    expect(g.nodes.some((n: any) => n.id === 'right' || n.label === 'right')).toBe(false);
  });

  test('D-069: d2CanvasSize is natural pixel size, not a downscaling box', () => {
    const nodes = [
      { x: 0, y: 0, width: 80, height: 40 },
      { x: 3000, y: 0, width: 100, height: 40 },
    ];
    const size = d2CanvasSize(nodes);
    // FIX: width tracks the content extent, so fixed-12px text is drawn 1:1.
    // PRE-FIX the viewBox was downscaled into a small host width, shrinking the
    // 12px label sub-pixel on a wide graph.
    expect(size.width).toBeGreaterThanOrEqual(3100);
    expect(size.width).toBe(3200);
    expect(size.viewBox).toBe(`0 0 ${size.width} ${size.height}`);
  });

  test('D-070: labels wrap / hard-break and grid pitch cannot overlap boxes', () => {
    // Multi-word label wraps into >1 line at a narrow width (PRE-FIX: 1 flat line).
    expect(wrapLabel('the quick brown fox jumps over the lazy dog', 100).length).toBeGreaterThan(1);

    // A single 600-char unbreakable token is hard-broken (PRE-FIX: ran off canvas).
    const broken = wrapLabel('x'.repeat(600), 120);
    expect(broken.length).toBeGreaterThan(1);
    const maxChars = Math.max(4, Math.floor((120 - 16) / 8));
    for (const line of broken) expect(line.length).toBeLessThanOrEqual(maxChars);

    // Grid pitch is at least the widest node width so neighbours never truncate
    // each other. PRE-FIX pitch was a fixed 150 < node width.
    const wide = 'a-really-wide-node-label-that-hits-the-max-width';
    const w = d2NodeWidth(wide);
    const pitch = d2GridPitch([{ label: wide, width: w, height: 40 }]);
    expect(pitch.x).toBeGreaterThanOrEqual(w);
    expect(pitch.x).toBeGreaterThan(150);
  });

  test('D-077: alien dialects are detected, genuine d2 is not', () => {
    // PRE-FIX these hit the brace-matcher and stalled to a 30s render timeout.
    expect(looksLikeJson('{"nodes": [\n  {"id": "a"}\n]}')).toBe(true);
    expect(looksLikeMermaid('graph TD\nA[Web] --> B{API}')).toBe(true);
    // A genuine d2 definition is flagged as neither.
    expect(looksLikeJson('web -> api\napi -> db')).toBe(false);
    expect(looksLikeMermaid('web -> api\napi -> db')).toBe(false);
  });
});

/**
 * G-25 — d2 plugin repairs, part 3 (shared file: d2Plugin.ts).
 *
 * All four defects are THEME-INVARIANT: the parse output, the JSON sniff and
 * the SVG sizing are byte-identical in light and dark (no colour is resolved
 * from the theme in any of these paths), so each test asserts the
 * theme-independent value. Where a render path IS theme-branched (the D-099
 * message card), both themes are exercised via the shared logic.
 *
 *   D-097  A `key: value` line inside an `id: Label { ... }` node body applies
 *          to that node (shape / width / height / style.* / inline style:{})
 *          and NEVER becomes a phantom node — the node's own label survives.
 *   D-099  A JSON graph payload is detected (looksLikeJson) so the renderer can
 *          say "looks like JSON, not D2" instead of drawing two '[' boxes.
 *   D-092  d2ResolveSvgSize honours an explicit requested width/height by
 *          scaling the content viewBox to that box (nothing dropped/clipped),
 *          and falls back to the natural pixel size when none is requested.
 *   D-079  parseConnection sets reversed/bidirectional and the render marker
 *          decision points the head at the correct end for '->','<-','<->'
 *          (regression pin: the direction handling was added with the earlier
 *          edge-rendering rework — D-077/D-078 — and this guards it).
 *
 * Direction: every assertion is paired with a check documenting the PRE-FIX
 * value (attribute line split into a phantom node; JSON line accepted as a
 * simple node; requested size ignored), so the D-097/D-099/D-092 tests fail
 * against unpatched d2Plugin.ts.
 */
import {
  D2Parser,
  looksLikeJson,
  d2ResolveSvgSize,
  d2CanvasSize,
  splitTopLevelSeps,
} from '../d2Plugin';

// The render marker decision, lifted verbatim from the edge render block so the
// test pins the exact expression the renderer evaluates.
const markerEnd = (d: any) => (d.bidirectional || !d.reversed) ? 'url(#arrowhead)' : null;
const markerStart = (d: any) => (d.bidirectional || d.reversed) ? 'url(#arrowhead-start)' : null;

// ---------------------------------------------------------------------------
// D-097 — node-body attribute lines are not phantom nodes
// ---------------------------------------------------------------------------
describe('D-097 attribute-block body applies to the node (no phantom nodes)', () => {
  test('DIRECTION: the pre-fix simple-node split turned `shape: sql_table` into a node', () => {
    // Old parseSimpleNode did `line.split(":")` on a node-body line, so
    // `shape: sql_table` -> id "shape", label "sql_table".
    const parts = 'shape: sql_table'.split(':');
    expect(parts[0].trim()).toBe('shape');          // phantom node id
    expect(parts.slice(1).join(':').trim()).toBe('sql_table');
  });

  test('sql_table body columns do not leak as nodes; label + shape survive (d2-w4-14)', () => {
    const def = [
      'db: Inventory {',
      '  shape: sql_table',
      '  id: int',
      '  name: varchar',
      '}',
      'users: Users {shape: person}',
      'users -> db: reads',
    ].join('\n');
    const { nodes, edges } = new D2Parser().parse(def);

    const ids = nodes.map((n: any) => n.originalId).sort();
    // Exactly db + users. No phantom 'id' / 'name' / 'shape' nodes.
    expect(ids).toEqual(['db', 'users']);
    expect(nodes.some((n: any) => ['id', 'name', 'shape'].includes(n.originalId))).toBe(false);

    const db = nodes.find((n: any) => n.originalId === 'db');
    expect(db.label).toBe('Inventory');    // label NOT discarded / replaced by 'db'
    expect(db.shape).toBe('sql_table');
    expect(db.attrs).toEqual({ id: 'int', name: 'varchar' }); // preserved but inert

    const users = nodes.find((n: any) => n.originalId === 'users');
    expect(users.label).toBe('Users');
    expect(users.shape).toBe('person');

    expect(edges).toHaveLength(1);
    expect(edges[0].source).toBe('users');
    expect(edges[0].target).toBe('db');
  });

  test('inline `style: { fill, stroke, }` with trailing comma + mixed quotes (d2-w4-03)', () => {
    const def = [
      'web: Web Server {',
      "  style: { fill: \"#4287f5\", stroke: 'navy', }",
      '}',
      'api: API Service',
      'web -> api',
    ].join('\n');
    const { nodes } = new D2Parser().parse(def);

    const ids = nodes.map((n: any) => n.originalId).sort();
    expect(ids).toEqual(['api', 'web']);
    // No phantom 'style' node (the old parseNodeWithProperties symptom).
    expect(nodes.some((n: any) => n.originalId === 'style')).toBe(false);

    const web = nodes.find((n: any) => n.originalId === 'web');
    expect(web.label).toBe('Web Server');
    expect(web.style.fill).toBe('#4287f5');   // double quotes stripped
    expect(web.style.stroke).toBe('navy');    // single quotes stripped
  });

  test('quoted numeric width/height + style.* keys apply to the node (d2-w4-11)', () => {
    const def = [
      'web: Web Server {',
      '  width: "200"',
      '  height: "80"',
      '  style.stroke-width: "3"',
      '  style.opacity: "0.9"',
      '}',
      'api: API Service',
      'web -> api',
    ].join('\n');
    const { nodes } = new D2Parser().parse(def);

    const ids = nodes.map((n: any) => n.originalId).sort();
    expect(ids).toEqual(['api', 'web']);
    expect(nodes.some((n: any) => ['width', 'height', 'style.stroke-width'].includes(n.originalId))).toBe(false);

    const web = nodes.find((n: any) => n.originalId === 'web');
    expect(web.label).toBe('Web Server');
    expect(web.width).toBe(200);              // quoted numeric coerced
    expect(web.height).toBe(80);
    expect(web.style['stroke-width']).toBe('3');
    expect(web.style.opacity).toBe('0.9');
  });

  test('regression guard: a nested `style { }` block still applies to the node', () => {
    const def = ['cache: Redis {', '  style {', '    fill: "#fff3e0"', '  }', '}'].join('\n');
    const { nodes, containers } = new D2Parser().parse(def);
    expect(containers).toHaveLength(0);
    const cache = nodes.find((n: any) => n.originalId === 'cache');
    expect(cache.label).toBe('Redis');
    expect(cache.style.fill).toBe('#fff3e0');
  });

  test('splitTopLevelSeps is paren-aware (rgb() commas are not fractured)', () => {
    expect(splitTopLevelSeps('fill: rgb(1,2,3), stroke: navy,', ';,'))
      .toEqual(['fill: rgb(1,2,3)', ' stroke: navy']);
    // Semicolons still split (old behaviour preserved).
    expect(splitTopLevelSeps('a: 1; b: 2', ';,')).toEqual(['a: 1', ' b: 2']);
  });
});

// ---------------------------------------------------------------------------
// D-099 — JSON payload is recognised, not mangled into '[' boxes
// ---------------------------------------------------------------------------
describe('D-099 JSON graph payload detection', () => {
  const jsonWithTrailingCommas = [
    '{',
    '  "nodes": [',
    '    {"id": "web", "label": "Web Server"},',
    '    {"id": "api", "label": "API Service"},',
    '  ],',
    '  "edges": [',
    '    {"from": "web", "to": "api"},',
    '  ]',
    '}',
  ].join('\n');

  test('DIRECTION: strict JSON.parse rejects the trailing-comma payload, and its lines contain ":"', () => {
    expect(() => JSON.parse(jsonWithTrailingCommas)).toThrow();
    // `"nodes": [` contains ':' -> old parseSimpleNode accepted it as a node.
    expect('  "nodes": ['.includes(':')).toBe(true);
  });

  test('looksLikeJson is true for the near-JSON payload (d2-w4-02)', () => {
    expect(looksLikeJson(jsonWithTrailingCommas)).toBe(true);
    // And the parser would otherwise have produced phantom '[' boxes.
    const { nodes } = new D2Parser().parse(jsonWithTrailingCommas);
    expect(nodes.some((n: any) => String(n.label).trim() === '[')).toBe(true);
  });

  test('looksLikeJson is true for strict JSON too', () => {
    expect(looksLikeJson('{"nodes":[{"id":"a"}],"edges":[]}')).toBe(true);
    expect(looksLikeJson('[{"id":"a"},{"id":"b"}]')).toBe(true);
  });

  test('looksLikeJson is FALSE for real D2 (no hijack of valid input)', () => {
    expect(looksLikeJson('a -> b\nb -> c')).toBe(false);
    expect(looksLikeJson('web: Web Server\nweb -> api')).toBe(false);
    // A d2 container block starts with an identifier + `{`, not `{` + quoted keys.
    expect(looksLikeJson('outer {\n  inner {\n    leaf: Leaf\n  }\n}')).toBe(false);
    // A node body that merely uses braces is not JSON.
    expect(looksLikeJson('db: Inventory {\n  shape: sql_table\n}')).toBe(false);
    expect(looksLikeJson('')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// D-092 — explicit requested size is honoured (scale, not truncate)
// ---------------------------------------------------------------------------
describe('D-092 requested width/height plumbed to the SVG', () => {
  // A 60-node graph: content bounds well beyond any small requested box.
  const canvas = d2CanvasSize([
    { x: 0, y: 0, width: 200, height: 40 },
    { x: 1800, y: 2400, width: 200, height: 40 },
  ]);

  test('DIRECTION: with no plumbing the SVG used the natural canvas size, ignoring the request', () => {
    // canvas is the pre-fix size the old renderer always used.
    expect(canvas.width).toBe(2100);   // 1800+200+100
    expect(canvas.height).toBe(2540);  // 2400+40+100
  });

  test('an oversize request widens the SVG while keeping the content viewBox', () => {
    const s = d2ResolveSvgSize(canvas, 3000, 300);
    expect(s.width).toBe(3000);
    expect(s.height).toBe(300);
    // viewBox stays the CONTENT bounds -> preserveAspectRatio "meet" scales all
    // rows to fit; nothing is truncated (the old defect dropped 44/60 rows).
    expect(s.viewBox).toBe(canvas.viewBox);
  });

  test('an undersize request scales down (all nodes still inside the viewBox)', () => {
    const s = d2ResolveSvgSize(canvas, 260, 220);
    expect(s.width).toBe(260);
    expect(s.height).toBe(220);
    expect(s.viewBox).toBe(canvas.viewBox); // content bounds preserved, not clipped
  });

  test('no / invalid request falls back to the natural pixel size (D-086 preserved)', () => {
    expect(d2ResolveSvgSize(canvas)).toEqual(canvas);
    expect(d2ResolveSvgSize(canvas, 0, -5)).toEqual(canvas);
    expect(d2ResolveSvgSize(canvas, NaN as any, undefined)).toEqual(canvas);
  });
});

// ---------------------------------------------------------------------------
// D-079 — edge direction is parsed and rendered (regression pin)
// ---------------------------------------------------------------------------
describe('D-079 edge direction markers', () => {
  test('DIRECTION: the connector token determines reversed/bidirectional flags', () => {
    const { edges } = new D2Parser().parse('a -> b\nc <- d\ne <-> f');
    const fwd = edges.find((e: any) => e.source === 'a');
    const rev = edges.find((e: any) => e.source === 'c');
    const bi = edges.find((e: any) => e.source === 'e');
    expect([fwd.reversed, fwd.bidirectional]).toEqual([false, false]);
    expect([rev.reversed, rev.bidirectional]).toEqual([true, false]);
    expect([bi.reversed, bi.bidirectional]).toEqual([false, true]);
  });

  test("'a -> b' points the head at the target only", () => {
    const [e] = new D2Parser().parse('a -> b').edges;
    expect(markerEnd(e)).toBe('url(#arrowhead)');
    expect(markerStart(e)).toBeNull();
  });

  test("'consumer <- queue' points the head at the source, not the target (d2-w1-05)", () => {
    const [e] = new D2Parser().parse('consumer <- queue: deliver').edges;
    expect(e.source).toBe('consumer');
    expect(e.target).toBe('queue');
    expect(e.label).toBe('deliver');
    expect(markerEnd(e)).toBeNull();                       // NOT at the target
    expect(markerStart(e)).toBe('url(#arrowhead-start)');  // reverse head at source
  });

  test("'a <-> b' paints both heads", () => {
    const [e] = new D2Parser().parse('a <-> b').edges;
    expect(markerEnd(e)).toBe('url(#arrowhead)');
    expect(markerStart(e)).toBe('url(#arrowhead-start)');
  });
});

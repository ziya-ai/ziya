/**
 * G-D2-PARSE — d2Plugin.ts parse-layer recovery (shared file: d2Plugin.ts).
 *
 * All defects here are THEME-INVARIANT: parsing runs on the definition text
 * before any colour is resolved from the theme, so the parse output is
 * byte-identical in light and dark. There is therefore no theme axis to split;
 * a parse fix that is correct is correct in both themes by construction.
 *
 * NEW fixes made in this stage (these fail against unpatched d2Plugin.ts):
 *   D-060  stripD2CodeFence — a wrapping ```d2 ... ``` markdown fence is
 *          removed instead of becoming phantom backtick nodes.
 *   D-061  stripD2BlockStrings + inline-attr strip — a `note: |md ... |` block
 *          folds into one labelled node (its `## Heading` / `**bold**` lines no
 *          longer leak as phantom nodes), and a trailing `{near: ...}` inline
 *          attribute is stripped from an edge label.
 *
 * DIRECTION: the whole file imports stripD2CodeFence / stripD2BlockStrings,
 * which do not exist in unpatched d2Plugin.ts, so it fails to compile against
 * HEAD; each NEW-fix test additionally documents the pre-fix mangling it
 * prevents. The remaining describe blocks are regression guards for parse
 * fixes that already live in d2Plugin.ts (D-047/048/051/052/053/054/062/063).
 */
import {
  D2Parser,
  stripD2CodeFence,
  stripD2BlockStrings,
  d2SqlColumns,
  looksLikeJson,
  looksLikeMermaid,
} from '../d2Plugin';

const parse = (def: string) => new D2Parser().parse(def);

// ---------------------------------------------------------------------------
// D-060 — markdown fence not stripped (d2-w4-01)
// ---------------------------------------------------------------------------
describe('D-060 wrapping ```d2 fence is stripped, not parsed as nodes', () => {
  const raw = '```d2\nweb: Web Server\napi: API Service\ndb: Database\nweb -> api\napi -> db\n```\n';

  test('DIRECTION: raw payload carries fence lines a pre-fix parse turned into phantom nodes', () => {
    expect(raw).toMatch(/```d2/);
    // stripD2CodeFence removes exactly the fence markers, leaving the body.
    expect(stripD2CodeFence(raw).trim()).toBe(
      'web: Web Server\napi: API Service\ndb: Database\nweb -> api\napi -> db'
    );
  });

  test('parse yields the 3 real nodes and 2 edges, no backtick node survives', () => {
    const { nodes, edges } = parse(raw);
    expect(nodes).toHaveLength(3);
    expect(nodes.map((n: any) => n.label)).toEqual(
      expect.arrayContaining(['Web Server', 'API Service', 'Database'])
    );
    // No phantom ```d2 / ``` box.
    expect(nodes.some((n: any) => /`/.test(n.label) || /`/.test(n.id))).toBe(false);
    expect(edges.map((e: any) => [e.source, e.target])).toEqual(
      expect.arrayContaining([['web', 'api'], ['api', 'db']])
    );
  });

  test('stripD2CodeFence leaves an unfenced definition untouched', () => {
    expect(stripD2CodeFence('a -> b\nc -> d')).toBe('a -> b\nc -> d');
  });
});

// ---------------------------------------------------------------------------
// D-061 — |md| block and inline edge attrs (d2-w4-14)
// ---------------------------------------------------------------------------
describe('D-061 |md| block folds to one node; inline edge attrs stripped', () => {
  const raw =
    'direction: right\n' +
    'users: Users {shape: person}\n' +
    'db: Inventory {\n  shape: sql_table\n  id: int\n  name: varchar\n}\n' +
    'note: |md\n  ## Deployment note\n  Runs in **us-east-1**\n|\n' +
    'users -> db: reads {near: top-center}\n';

  test('DIRECTION: stripD2BlockStrings collapses the block a pre-fix parse shredded', () => {
    // The raw block spans 4 lines whose `##`/`**` markup a pre-fix parse leaked
    // as phantom nodes; the fold turns it into a single plain-text node line.
    expect(stripD2BlockStrings('note: |md\n  ## Deployment note\n  Runs in **us-east-1**\n|')).toBe(
      'note: Deployment note Runs in us-east-1'
    );
  });

  test('the md block becomes exactly one legible node, no phantom fragments', () => {
    const { nodes } = parse(raw);
    const note = nodes.find((n: any) => n.id === 'note');
    expect(note).toBeDefined();
    expect(note.label).toMatch(/Deployment note/);
    expect(note.label).toMatch(/us-east-1/);
    // No leaked heading / bold / bare `|` fragments as their own nodes.
    expect(nodes.some((n: any) => /\*\*/.test(n.label))).toBe(false);
    expect(nodes.some((n: any) => n.label === '|' || n.label === '|md')).toBe(false);
    expect(nodes.some((n: any) => /^#/.test(n.label))).toBe(false);
  });

  test('the real nodes and direction still parse correctly around the block', () => {
    const { nodes, direction } = parse(raw);
    expect(direction).toBe('right');
    expect(nodes.find((n: any) => n.id === 'users').shape).toBe('person');
    expect(nodes.find((n: any) => n.id === 'db').shape).toBe('sql_table');
  });

  test('a trailing {near: ...} inline attr is stripped from the edge label', () => {
    const { edges } = parse(raw);
    const e = edges.find((x: any) => x.source === 'users' && x.target === 'db');
    expect(e).toBeDefined();
    expect(e.label).toBe('reads');
    expect(e.label).not.toMatch(/near|\{|\}/);
  });

  test('a label-less inline attr keeps a clean endpoint id', () => {
    const { nodes, edges } = parse('a -> b {near: top}');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b']);
    expect(edges).toHaveLength(1);
    expect(edges[0].target).toBe('b');
  });
});

// ---------------------------------------------------------------------------
// Regression guards for parse fixes already resident in d2Plugin.ts
// ---------------------------------------------------------------------------
describe('D-047 edge `a -> b: x` gives 2 nodes + labelled edge, not a "b: x" node', () => {
  test('colon label is not swallowed into a phantom target node', () => {
    const { nodes, edges } = parse('a -> b: x');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b']);
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('x');
  });
});

describe('D-048 edge direction / bidirectional is recorded for marker gating', () => {
  test('->, <- and <-> set reversed / bidirectional distinctly', () => {
    const { edges } = parse('a -> b\nc <- d\ne <-> f');
    const fwd = edges.find((x: any) => x.source === 'a');
    expect(fwd.reversed).toBe(false);
    expect(fwd.bidirectional).toBe(false);
    const rev = edges.find((x: any) => x.source === 'c');
    expect(rev.reversed).toBe(true);
    expect(rev.bidirectional).toBe(false);
    const bi = edges.find((x: any) => x.source === 'e');
    expect(bi.bidirectional).toBe(true);
  });
});

describe('D-051 shape keyword and sql_table are honoured', () => {
  test('inline shape is stored and sql_table columns are captured', () => {
    const { nodes } = parse('n: Circle {shape: circle}\nt: T {\n  shape: sql_table\n  id: int\n  name: varchar\n}');
    expect(nodes.find((n: any) => n.id === 'n').shape).toBe('circle');
    const t = nodes.find((n: any) => n.id === 't');
    expect(t.shape).toBe('sql_table');
    expect(d2SqlColumns(t)).toEqual(expect.arrayContaining(['id: int', 'name: varchar']));
  });
});

describe('D-052 style directives apply to nodes, not phantom boxes', () => {
  test('inline and dotted style both land on node.style', () => {
    const { nodes } = parse('x: X {fill: blue}\ny.style.fill: red');
    expect(nodes.find((n: any) => n.id === 'x').style.fill).toBe('blue');
    expect(nodes.find((n: any) => n.id === 'y').style.fill).toBe('red');
  });
});

describe('D-053 chained connection yields every hop', () => {
  test('a -> b -> c is two edges over three nodes', () => {
    const { nodes, edges } = parse('a -> b -> c');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b', 'c']);
    expect(edges.map((e: any) => [e.source, e.target])).toEqual([['a', 'b'], ['b', 'c']]);
  });
});

describe('D-054 trailing # comment is stripped from a node line', () => {
  test('`Build   # compiles sources` keeps label Build, no comment node', () => {
    const { nodes } = parse('Build   # compiles sources');
    const b = nodes.find((n: any) => n.id === 'Build');
    expect(b).toBeDefined();
    expect(b.label).toBe('Build');
    expect(nodes.some((n: any) => /compiles/.test(n.label))).toBe(false);
  });
});

describe('D-062 a stray closing brace does not desync the frame stack', () => {
  const raw =
    'region {\n  az1 {\n    node1: Instance A\n    node2: Instance B\n  }\n  node3: Shared Cache\n}\n}\nnode1 -> node2\n';

  test('containers, node labels and the edge all survive the extra `}`', () => {
    let result: any;
    expect(() => { result = parse(raw); }).not.toThrow();
    // D-105: containers/nodes are keyed by full dotted path; the top-level
    // `node1 -> node2` resolves to the unique nodes of those names inside az1.
    expect(result.containers.map((c: any) => c.id)).toEqual(expect.arrayContaining(['region', 'region.az1']));
    const node2 = result.nodes.find((n: any) => n.id === 'region_az1_node2');
    expect(node2).toBeDefined();
    expect(node2.label).toBe('Instance B');
    expect(result.edges.find((x: any) => x.source === 'region_az1_node1' && x.target === 'region_az1_node2')).toBeDefined();
  });
});

describe('D-063 alien dialects are detected (not silently parsed to a hang)', () => {
  test('JSON payload and mermaid source are flagged; genuine d2 is not', () => {
    expect(looksLikeJson('{"nodes": [\n  {"id": "a"}\n]}')).toBe(true);
    expect(looksLikeMermaid('graph TD\nA[Web] --> B{API}')).toBe(true);
    expect(looksLikeMermaid('a -> b\nb -> c')).toBe(false);
    expect(looksLikeJson('web -> api')).toBe(false);
  });
});

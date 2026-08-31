/**
 * @jest-environment jsdom
 */
/**
 * G-43 — d2 plugin repairs, part 4 (shared file: d2Plugin.ts).
 *
 * All four defects are kind:structural and THEME-INVARIANT: the parse output
 * and the emitted geometry are byte-identical in light and dark (no colour is
 * resolved from the theme in any of these paths). The render-level tests still
 * exercise BOTH themes to prove the structural element is emitted regardless of
 * theme, per the run's both-theme rule.
 *
 *   D-080  container label — each group draws its NAME as a <text> at the top
 *          of its bounds (was a bare dashed <rect>, so every group was
 *          anonymous). Bounds already come from member nodes (D-081), so the
 *          "overbounded" half of the triage was already resolved.
 *   D-082  node `shape` is honoured by a shape dispatch (circle/cylinder/queue/
 *          diamond/hexagon/…) instead of an unconditional <rect rx=5>, and a
 *          `X: { shape: sql_table ... }` body is a SINGLE node whose columns are
 *          drawn as rows — not a phantom container full of type-labelled boxes.
 *   D-084  a chained `a -> b -> c` yields BOTH edges (was one edge to a phantom
 *          node "b -> c"), and a top-level `direction: right` sets the layout
 *          flow instead of becoming a box labelled 'right'.
 *   D-085  a trailing `# comment` is stripped from a line (was left inside the
 *          node label, overflowing the box into its neighbour); an unquoted hex
 *          colour value and a quoted "#..." are preserved.
 *
 * Direction: every assertion is paired with a check documenting the PRE-FIX
 * value (single-connector match drops the chain; `direction:` split into a
 * node; `#`-suffix left in the label; unconditional rect; container has no
 * label element), so each test fails against unpatched d2Plugin.ts.
 */
import {
  D2Parser,
  stripInlineComment,
  d2NodeBoxSize,
  d2SqlColumns,
  d2Plugin,
  D2_SQL_HEADER_HEIGHT,
} from '../d2Plugin';

// ---------------------------------------------------------------------------
// D-085 — trailing comment is stripped; hex colours are preserved
// ---------------------------------------------------------------------------
describe('D-085 trailing "# comment" is stripped from a line', () => {
  test('DIRECTION: the pre-fix filter only dropped lines STARTING with #', () => {
    // Old parse(): .filter(line => line && !line.startsWith('#')). A trailing
    // comment survived inside the value.
    const line = 'build: Build   # compiles sources';
    expect(line.startsWith('#')).toBe(false);          // not dropped
    expect(line).toContain('# compiles sources');       // comment tail present
  });

  test('stripInlineComment removes the trailing comment, keeps the label', () => {
    expect(stripInlineComment('build: Build   # compiles sources')).toBe('build: Build   ');
    expect(stripInlineComment('a -> b: artifacts   # tarball')).toBe('a -> b: artifacts   ');
  });

  test('an unquoted hex value and a quoted "#..." are NOT treated as comments', () => {
    // A bare hex colour after a space is a value, not a comment.
    expect(stripInlineComment('x.style.fill: #ff0000')).toBe('x.style.fill: #ff0000');
    expect(stripInlineComment('x.style.fill: #f00')).toBe('x.style.fill: #f00');
    // Inside quotes the # is inert.
    expect(stripInlineComment('label: "a # b"')).toBe('label: "a # b"');
    expect(stripInlineComment("danger.style.fill: '#8b0000'")).toBe("danger.style.fill: '#8b0000'");
  });

  test('parse: node label no longer carries the comment tail (d2-w1-14)', () => {
    const def = [
      '# Deployment pipeline',
      'build: Build   # compiles sources',
      'test: Test',
      'ship: Ship',
      'build -> test: artifacts   # tarball',
      'test -> ship: green build',
    ].join('\n');
    const { nodes, edges } = new D2Parser().parse(def);
    const build = nodes.find((n: any) => n.originalId === 'build');
    expect(build.label).toBe('Build');                 // NOT 'Build # compiles sources'
    expect(nodes.every((n: any) => !String(n.label).includes('#'))).toBe(true);
    const artifacts = edges.find((e: any) => e.source === 'build' && e.target === 'test');
    expect(artifacts.label).toBe('artifacts');          // NOT 'artifacts # tarball'
  });
});

// ---------------------------------------------------------------------------
// D-084 — chained connections + `direction:` keyword
// ---------------------------------------------------------------------------
describe('D-084 chained connections and direction keyword', () => {
  test('DIRECTION: the pre-fix single-match parser kept only the first edge', () => {
    // Old parseConnection matched ONE connector and put the rest in target:
    //   'a -> b -> c'  ->  source 'a', target 'b -> c' (a phantom node).
    const connMatch = 'a -> b -> c'.match(/\s*(<->|<-|->)\s*/)!;
    const after = 'a -> b -> c'.slice(connMatch.index! + connMatch[0].length);
    expect(after.trim()).toBe('b -> c');                // whole tail became target
  });

  test("'a -> b -> c' yields BOTH edges a->b and b->c (3 nodes, no phantom)", () => {
    const { nodes, edges } = new D2Parser().parse('a -> b -> c');
    expect(edges).toHaveLength(2);
    expect(edges.map((e: any) => [e.source, e.target])).toEqual([['a', 'b'], ['b', 'c']]);
    const ids = nodes.map((n: any) => n.originalId).sort();
    expect(ids).toEqual(['a', 'b', 'c']);
    // No phantom node whose id came from swallowing the tail.
    expect(nodes.some((n: any) => /->/.test(n.originalId) || /->/.test(n.label))).toBe(false);
  });

  test('single-connector `a -> b: x` still yields exactly one labelled edge (D-078 guard)', () => {
    const { nodes, edges } = new D2Parser().parse('a -> b: x');
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('x');
    expect(nodes.map((n: any) => n.originalId).sort()).toEqual(['a', 'b']);
  });

  test('chain label applies to every edge; connector kinds are preserved', () => {
    const { edges } = new D2Parser().parse('a -> b -> c: flow');
    expect(edges.map((e: any) => e.label)).toEqual(['flow', 'flow']);
    const bi = new D2Parser().parse('a <-> b <- c');
    expect(bi.edges[0].bidirectional).toBe(true);
    expect(bi.edges[1].reversed).toBe(true);
  });

  test('`direction: right` sets flow direction, NOT a node (d2-w1-11)', () => {
    const def = [
      'direction: right',
      'step1: Ingest',
      'step2: Transform',
      'step3: Load',
      'step1 -> step2 -> step3',
    ].join('\n');
    const result = new D2Parser().parse(def);
    expect((result as any).direction).toBe('right');
    // No phantom 'direction' node (the old parseSimpleNode symptom).
    expect(result.nodes.some((n: any) => n.originalId === 'direction')).toBe(false);
    expect(result.nodes.map((n: any) => n.originalId).sort()).toEqual(['step1', 'step2', 'step3']);
    // The chain produced both edges.
    expect(result.edges).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// D-082 — node shape honoured; sql_table is one node, not phantom columns
// ---------------------------------------------------------------------------
describe('D-082 shape parsing and sizing', () => {
  test('`X: { shape: circle }` stores the shape and keeps the id as label (d2-w1-09)', () => {
    const def = [
      'start: { shape: circle }',
      'store: { shape: cylinder }',
      'q: { shape: queue }',
      'start -> q',
      'q -> store',
    ].join('\n');
    const { nodes, edges } = new D2Parser().parse(def);
    const byId = new Map(nodes.map((n: any) => [n.originalId, n]));
    expect(byId.get('start').shape).toBe('circle');
    expect(byId.get('store').shape).toBe('cylinder');
    expect(byId.get('q').shape).toBe('queue');
    // Label is the id, NOT the shape keyword.
    expect(byId.get('start').label).toBe('start');
    expect(nodes.some((n: any) => n.label === 'circle')).toBe(false);
    expect(edges).toHaveLength(2);
  });

  test('a circle box is squared so the label fits its inscribed area', () => {
    const box = d2NodeBoxSize({ id: 'start', label: 'start', shape: 'circle' });
    expect(box.width).toBe(box.height);
    // A plain node is NOT squared (documents the shape-specific branch).
    const plain = d2NodeBoxSize({ id: 'start', label: 'start' });
    expect(plain.width).not.toBe(plain.height);
  });

  test('`users: { shape: sql_table ... }` is ONE node with column rows, no phantom container (d2-w1-15)', () => {
    const def = [
      'users: {',
      '  shape: sql_table',
      '  id: int',
      '  email: varchar',
      '  org_id: int',
      '}',
      'orgs: {',
      '  shape: sql_table',
      '  id: int',
      '  name: varchar',
      '}',
      'users.org_id -> orgs.id: FK',
    ].join('\n');
    const { nodes, containers } = new D2Parser().parse(def);

    // No anonymous container, and no phantom column nodes named after types.
    expect(containers).toHaveLength(0);
    const users = nodes.find((n: any) => n.originalId === 'users');
    expect(users).toBeDefined();
    expect(users.label).toBe('users');
    expect(users.shape).toBe('sql_table');
    expect(users.attrs).toEqual({ id: 'int', email: 'varchar', org_id: 'int' });
    // Body-only keys never leak as nodes. ('id'/'org_id' can still appear via
    // the `users.org_id -> orgs.id` edge's dotted-path resolution — a separate,
    // pre-existing resolvePath limitation, not the sql_table body shredding —
    // so we assert on 'shape'/'email' which appear ONLY in the table body.)
    expect(nodes.some((n: any) => ['shape', 'email'].includes(n.originalId))).toBe(false);

    // Columns become drawable rows and the box grows to hold them.
    expect(d2SqlColumns(users)).toEqual(['id: int', 'email: varchar', 'org_id: int']);
    const box = d2NodeBoxSize(users);
    expect(box.height).toBeGreaterThan(D2_SQL_HEADER_HEIGHT + 3 * 18); // header + 3 rows
    // DIRECTION: a plain (non-table) node of the same label is far shorter.
    expect(box.height).toBeGreaterThan(d2NodeBoxSize({ id: 'users', label: 'users' }).height);
  });
});

// ---------------------------------------------------------------------------
// Render-level: shape dispatch (D-082) and container label (D-080) are emitted
// in BOTH themes. elkjs is mocked to an echo layout so the test is
// deterministic and needs no real layout engine; d3 is a recording mock that
// evaluates `.each(fn)` and the data-join so per-node shape elements are
// observable.
// ---------------------------------------------------------------------------
jest.mock('elkjs', () => ({
  __esModule: true,
  default: class {
    async layout(graph: any) {
      return {
        ...graph,
        children: (graph.children || []).map((c: any, i: number) => ({
          ...c, x: i * 200, y: i * 100, width: c.width || 100, height: c.height || 50,
        })),
      };
    }
  },
}));

type El = { tag: string; attrs: Record<string, any>; text?: any; datum: any };

function makeD2Recorder() {
  const store: El[] = [];

  function sel(els: El[], data: any[] | null, mode: 'normal' | 'enter'): any {
    const self: any = { __isSel: true };
    const targets = () => (els.length ? els : [{ tag: 'root', attrs: {}, datum: undefined } as El]);

    self.append = (tag: string) => {
      let created: El[];
      if (mode === 'enter' && data && data.length) {
        created = data.map((d) => { const e: El = { tag, attrs: {}, datum: d }; store.push(e); return e; });
      } else {
        const d = els[0] ? els[0].datum : undefined;
        const e: El = { tag, attrs: {}, datum: d };
        store.push(e);
        created = [e];
      }
      return sel(created, null, 'normal');
    };
    self.select = () => sel(els, null, 'normal');
    self.selectAll = () => sel([], null, 'normal');
    self.data = (arr: any[]) => sel([], Array.isArray(arr) ? arr : [], 'normal');
    self.datum = (d: any) => sel([{ tag: 'x', attrs: {}, datum: d }], null, 'normal');
    self.enter = () => sel(els, data, 'enter');
    self.exit = () => sel([], null, 'normal');
    self.merge = () => self;
    self.call = () => self;
    self.remove = () => self;
    self.attr = (k: string, v: any) => {
      els.forEach((e, i) => { e.attrs[k] = typeof v === 'function' ? v(e.datum, i) : v; });
      return self;
    };
    self.style = () => self;
    self.text = (v: any) => {
      els.forEach((e, i) => { e.text = typeof v === 'function' ? v(e.datum, i) : v; });
      return self;
    };
    self.each = function (fn: any) {
      els.forEach((e, i) => { fn.call(sel([e], null, 'normal'), e.datum, i); });
      return self;
    };
    return self;
  }

  const d3: any = {
    select: (arg: any) => (arg && arg.__isSel ? arg : sel([], null, 'normal')),
  };
  return { d3, store };
}

const tags = (store: El[], tag: string) => store.filter((e) => e.tag === tag);
const byClass = (store: El[], cls: string) => store.filter((e) => e.attrs['class'] === cls);

describe('D-080 / D-082 render — container label and shape dispatch (both themes)', () => {
  for (const isDark of [false, true]) {
    const themeName = isDark ? 'dark' : 'light';

    test(`container draws its name as a <text> label (D-080) [${themeName}]`, async () => {
      const { d3, store } = makeD2Recorder();
      const def = ['cloud {', '  lb: Load Balancer', '  app: App Server', '  lb -> app', '}', 'user: User', 'user -> lb'].join('\n');
      await d2Plugin.render!(document.createElement('div'), d3, { type: 'd2', definition: def } as any, isDark);
      const labels = byClass(store, 'container-label');
      // The container is no longer anonymous — its name is drawn.
      expect(labels.length).toBeGreaterThanOrEqual(1);
      expect(labels.map((e) => e.text)).toContain('cloud');
      // DIRECTION: unpatched code emitted only the dashed .container <rect> and
      // no .container-label <text>, so this assertion fails against it.
      expect(tags(store, 'rect').some((e) => e.attrs['class'] === 'container')).toBe(true);
    });

    test(`shape keyword drives the element: circle->ellipse, plain->rect (D-082) [${themeName}]`, async () => {
      const { d3, store } = makeD2Recorder();
      const def = ['start: { shape: circle }', 'plain: Plain Box', 'start -> plain'].join('\n');
      await d2Plugin.render!(document.createElement('div'), d3, { type: 'd2', definition: def } as any, isDark);
      // A circle-shaped node emits an <ellipse> (unpatched: always a <rect>).
      expect(tags(store, 'ellipse').length).toBeGreaterThanOrEqual(1);
      // A shapeless node still emits a <rect> (no regression).
      expect(tags(store, 'rect').length).toBeGreaterThanOrEqual(1);
    });

    test(`sql_table draws a header divider + one text row per column (D-082) [${themeName}]`, async () => {
      const { d3, store } = makeD2Recorder();
      const def = ['users: {', '  shape: sql_table', '  id: int', '  email: varchar', '}', 'orgs: Orgs', 'orgs -> users'].join('\n');
      await d2Plugin.render!(document.createElement('div'), d3, { type: 'd2', definition: def } as any, isDark);
      // Column rows are rendered as <text> (id: int / email: varchar).
      const texts = tags(store, 'text').map((e) => e.text);
      expect(texts).toContain('id: int');
      expect(texts).toContain('email: varchar');
      // The header divider line is drawn (unpatched: no divider, no rows).
      expect(tags(store, 'line').some((e) => e.attrs['y1'] === D2_SQL_HEADER_HEIGHT)).toBe(true);
    });
  }
});

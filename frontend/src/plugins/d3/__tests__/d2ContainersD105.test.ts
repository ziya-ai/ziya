/**
 * D-105 — labelled containers, scoped node paths, hierarchical ELK layout.
 *
 * Standard d2 for a container is `vpc: VPC { web: Web Server; db: Postgres;
 * web -> db }`. The parser treated EVERY `id: Label {` block as a leaf node
 * body (the D-097 attribute-body fix), so the children were swallowed as
 * attributes of `vpc`, the container was never created, and the inner
 * connection leaked to two phantom top-level nodes. Independently, ELK ran
 * flat and containers were drawn as bounding boxes of wherever their members
 * happened to land, so two containers' rects interleaved.
 *
 * Fix: (1) look-ahead classifier d2BodyIsContainer — a block whose body
 * declares a child shape or connection is a container, one whose body holds
 * only reserved attribute keys is a leaf; sql_table/class bodies are rows,
 * never children. (2) nodes and containers are keyed by their full dotted
 * path, resolved against the enclosing scope, so `client -> vpc.web` reaches
 * the child and two containers may each own a `web`. (3) buildElkHierarchy
 * nests member nodes inside compound ELK nodes so containers cluster, and
 * flattenElkResult returns absolute coordinates.
 *
 * DIRECTION: the first describe fails against unpatched d2Plugin.ts — the
 * container map is empty and `web`/`db` are top-level. d2BodyIsContainer /
 * buildElkHierarchy / flattenElkResult do not exist there.
 */
import {
  D2Parser,
  d2BodyIsContainer,
  buildElkHierarchy,
  flattenElkResult,
} from '../d2Plugin';

const parse = (def: string) => new D2Parser().parse(def);
const ids = (xs: Array<{ id: string }>) => xs.map((x) => x.id).sort();

describe('D-105 labelled container `id: Label { children }`', () => {
  const out = parse(`
    client: Client
    vpc: VPC {
      web: Web Server
      db: Postgres
      web -> db: queries
    }
    client -> vpc.web
  `);

  it('creates a container with the label, not a leaf node', () => {
    expect(out.containers).toHaveLength(1);
    expect(out.containers[0]).toMatchObject({ id: 'vpc', label: 'VPC', parent: null });
    // The container is not ALSO drawn as a box.
    expect(out.nodes.find((n) => n.id === 'vpc')).toBeUndefined();
  });

  it('scopes the children into the container with their own labels', () => {
    const web = out.nodes.find((n) => n.label === 'Web Server');
    const db = out.nodes.find((n) => n.label === 'Postgres');
    expect(web).toBeDefined();
    expect(db).toBeDefined();
    expect(web!.container).toBe('vpc');
    expect(db!.container).toBe('vpc');
    expect(out.containers[0].children.sort()).toEqual(['vpc.db', 'vpc.web']);
  });

  it('resolves the inner connection between the scoped children', () => {
    const e = out.edges.find((e) => e.label === 'queries');
    expect(e).toBeDefined();
    expect(e!.source).toBe('vpc_web');
    expect(e!.target).toBe('vpc_db');
  });

  it('resolves a dotted cross-container reference to the same child node', () => {
    const e = out.edges.find((e) => e.source === 'client');
    expect(e).toBeDefined();
    expect(e!.target).toBe('vpc_web');
    // No phantom top-level `web` was created by the reference.
    expect(out.nodes.filter((n) => n.label === 'Web Server')).toHaveLength(1);
    expect(ids(out.nodes)).toEqual(['client', 'vpc_db', 'vpc_web']);
  });
});

describe('D-105 attribute-only bodies stay leaf nodes (D-097 / D-082 preserved)', () => {
  it('`id: Label { shape; style.* }` is a shape, not a container', () => {
    const out = parse(`
      svc: Service {
        shape: hexagon
        style.fill: red
        style: { stroke: navy }
      }
    `);
    expect(out.containers).toHaveLength(0);
    const svc = out.nodes.find((n) => n.id === 'svc');
    expect(svc).toMatchObject({ label: 'Service', shape: 'hexagon' });
    expect(svc!.style).toMatchObject({ fill: 'red', stroke: 'navy' });
  });

  it('a sql_table body is rows, never children, even though `id: int` is not a reserved key', () => {
    const out = parse(`
      users: {
        shape: sql_table
        id: int
        name: varchar
      }
    `);
    expect(out.containers).toHaveLength(0);
    expect(out.nodes).toHaveLength(1);
    expect(out.nodes[0].shape).toBe('sql_table');
  });

  it('a nested `style { }` block inside a container body does not count as a child', () => {
    const out = parse(`
      net: Network {
        style { fill: blue }
        a: A
      }
    `);
    expect(out.containers.map((c) => c.id)).toEqual(['net']);
    expect(ids(out.nodes)).toEqual(['net_a']);
  });
});

describe('D-105 d2BodyIsContainer classifier', () => {
  const classify = (src: string) => {
    const lines = src.trim().split('\n').map((l) => l.trim()).filter(Boolean);
    return d2BodyIsContainer(lines, 0);
  };
  it('child shape line -> container', () => {
    expect(classify(`x: X {\na: A\n}`)).toBe(true);
  });
  it('connection line -> container', () => {
    expect(classify(`x: X {\na -> b\n}`)).toBe(true);
  });
  it('bare child id -> container', () => {
    expect(classify(`x: X {\na\n}`)).toBe(true);
  });
  it('reserved attrs only -> leaf', () => {
    expect(classify(`x: X {\nshape: circle\nwidth: 80\nnear: top-center\n}`)).toBe(false);
  });
  it('child sub-block -> container; style sub-block alone -> leaf', () => {
    expect(classify(`x: X {\ninner: I {\nshape: circle\n}\n}`)).toBe(true);
    expect(classify(`x: X {\nstyle {\nfill: red\n}\n}`)).toBe(false);
  });
  it('sql_table rows -> leaf', () => {
    expect(classify(`t: {\nshape: sql_table\nid: int\n}`)).toBe(false);
  });
  it('only inspects the block starting at openIdx (depth-aware)', () => {
    const lines = ['a: A {', 'shape: circle', '}', 'b: B {', 'c: C', '}'];
    expect(d2BodyIsContainer(lines, 0)).toBe(false);
    expect(d2BodyIsContainer(lines, 3)).toBe(true);
  });
});

describe('D-105 scoped paths', () => {
  it('two containers may each own a child with the same short id', () => {
    const out = parse(`
      east: East { web: Web }
      west: West { web: Web }
      east.web -> west.web
    `);
    expect(ids(out.nodes)).toEqual(['east_web', 'west_web']);
    expect(out.edges[0]).toMatchObject({ source: 'east_web', target: 'west_web' });
  });

  it('nested containers keep a correct parent chain and full-path ids', () => {
    const out = parse(`
      cloud: Cloud {
        vpc: VPC {
          web: Web
        }
      }
    `);
    const byId = Object.fromEntries(out.containers.map((c) => [c.id, c]));
    expect(byId['cloud']).toMatchObject({ label: 'Cloud', parent: null });
    expect(byId['cloud.vpc']).toMatchObject({ label: 'VPC', parent: 'cloud' });
    expect(out.nodes[0]).toMatchObject({ id: 'cloud_vpc_web', label: 'Web', container: 'cloud.vpc' });
  });

  it('`_.x` climbs one scope; a dotted top-level path referenced from inside a container is absolute', () => {
    const out = parse(`
      shared: Shared
      infra: Infra {
        db: DB
        db -> _.shared
      }
      ops: Ops {
        monitor -> infra.db
      }
    `);
    expect(ids(out.nodes)).toEqual(['infra_db', 'ops_monitor', 'shared']);
    expect(out.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: 'infra_db', target: 'shared' }),
        expect.objectContaining({ source: 'ops_monitor', target: 'infra_db' }),
      ]),
    );
  });

  it('a top-level dotted declaration `vpc.web: X` creates the container on demand', () => {
    const out = parse(`vpc.web: Web Server`);
    expect(out.containers.map((c) => c.id)).toEqual(['vpc']);
    expect(out.nodes[0]).toMatchObject({ id: 'vpc_web', label: 'Web Server', container: 'vpc' });
  });

  it('a leaf later opened as a container is promoted, carrying its label, and not drawn twice', () => {
    const out = parse(`
      vpc: My VPC
      vpc: {
        web: Web
      }
    `);
    expect(out.containers[0]).toMatchObject({ id: 'vpc', label: 'My VPC' });
    expect(out.nodes.find((n) => n.id === 'vpc')).toBeUndefined();
  });

  it('a flat diagram with no containers is byte-identical to before', () => {
    const out = parse(`a -> b -> c: chain\nb: Bee`);
    expect(ids(out.nodes)).toEqual(['a', 'b', 'c']);
    expect(out.nodes.find((n) => n.id === 'b')!.label).toBe('Bee');
    expect(out.edges).toHaveLength(2);
    expect(out.containers).toHaveLength(0);
  });
});

describe('D-105 hierarchical ELK graph', () => {
  const out = parse(`
    client: Client
    vpc: VPC {
      web: Web
      db: DB
      web -> db
    }
    client -> vpc.web
  `);

  it('nests member nodes inside a compound node per container and includes children in layout', () => {
    const g = buildElkHierarchy(out.nodes, out.edges, out.containers, { direction: 'DOWN' });
    expect(g.layoutOptions['elk.hierarchyHandling']).toBe('INCLUDE_CHILDREN');
    const rootIds = g.children.map((c: any) => c.id).sort();
    expect(rootIds).toEqual(['client', 'vpc']);
    const vpc = g.children.find((c: any) => c.id === 'vpc');
    expect(vpc.children.map((c: any) => c.id).sort()).toEqual(['vpc_db', 'vpc_web']);
    // Compound nodes reserve headroom for the container label.
    expect(vpc.layoutOptions['elk.padding']).toMatch(/top=\d+/);
    // Edge ids are unique even when the same pair is connected twice.
    const eids = g.edges.map((e: any) => e.id);
    expect(new Set(eids).size).toBe(eids.length);
  });

  it('flattens a laid-out hierarchy to absolute coordinates for every leaf', () => {
    const laid = {
      id: 'root',
      children: [
        { id: 'client', x: 10, y: 20, width: 100, height: 40 },
        {
          id: 'vpc', x: 200, y: 50, width: 300, height: 200,
          children: [
            { id: 'vpc_web', x: 20, y: 40, width: 100, height: 40 },
            { id: 'vpc_db', x: 20, y: 120, width: 100, height: 40 },
          ],
        },
      ],
    };
    const flat = flattenElkResult(laid, out.nodes);
    const byId = Object.fromEntries(flat.map((n: any) => [n.id, n]));
    expect(byId['client']).toMatchObject({ x: 10, y: 20 });
    expect(byId['vpc_web']).toMatchObject({ x: 220, y: 90, label: 'Web', container: 'vpc' });
    expect(byId['vpc_db']).toMatchObject({ x: 220, y: 170 });
    // Compound container nodes are not leaves.
    expect(byId['vpc']).toBeUndefined();
    expect(flat).toHaveLength(3);
  });
});

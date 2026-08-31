/**
 * G-05 — force-directed plugin: caller-colour safety, lenient spec recovery,
 * force-tuning levers, and label legibility (shared file: forceDirectedPlugin.ts).
 *
 * Defects covered:
 *   D-023  caller 'transparent' / unresolvable token no longer erases geometry
 *          (node fill falls back to the palette; link stroke to a readable default)
 *   D-024  fence / trailing-comma / unquoted-key / single-quote / smart-quote /
 *          comment / one-level-deeper wrapper definitions are recovered instead of
 *          leaving the spec node-less (which hangs to a 30s empty-DOM timeout)
 *   D-120  render() reads spec.charge / linkDistance / collideRadius instead of the
 *          hardcoded -200 / 80 / size+4
 *   D-021  node labels are ellipsis-truncated and get a canvas-colour halo
 *
 * Direction: every recovery case first asserts that STRICT JSON.parse THROWS on
 * the raw definition (so the test would fail against the old bare-JSON.parse
 * path), and every render assertion checks a value the pre-fix code did not
 * produce (distance 80/strength -200, no paint-order attr, verbatim colour).
 */
import { truncateLabel } from '../chartTheme';
import {
  forceDirectedPlugin,
  resolveForceDirectedSpec,
  resolveForceColors,
  lenientParseObject,
  stripDefinitionFence,
  normalizeSmartQuotes,
  findGraphContainer,
  FORCE_MAX_LABEL_CHARS,
} from '../forceDirectedPlugin';

// ── a minimal chainable d3 stub that records the interesting calls ───────────
// Every method returns the same callable proxy, so the whole render() chain runs
// without a DOM. The get trap remembers the property name so the immediately
// following apply can attribute the args to it (JS evaluates obj.method(args) as
// get-then-apply). Callbacks passed to .attr()/.text() are NOT invoked by the
// stub — the tests invoke the captured ones explicitly.
function makeMockD3() {
  const record: any = {
    distance: undefined,
    strength: undefined,
    radiusFn: undefined,
    attrs: [] as Array<[string, any]>,
    texts: [] as Array<(d: any) => any>,
  };
  const target: any = function () {};
  // Each property access returns a fresh closure BOUND to its own name, so a
  // nested chain evaluated as an argument (e.g. .force('link', forceLink(..)
  // .distance(10))) records against the right method even though its apply runs
  // between the outer get and the outer apply — a shared "pending" variable
  // would misattribute the outer call.
  const proxy: any = new Proxy(target, {
    get(_t, prop) {
      if (prop === 'zoomIdentity') return proxy;
      const name = String(prop);
      return (...args: any[]) => {
        if (name === 'distance') record.distance = args[0];
        else if (name === 'strength') record.strength = args[0];
        else if (name === 'radius' && typeof args[0] === 'function') record.radiusFn = args[0];
        else if (name === 'attr' && args.length >= 1) record.attrs.push([args[0], args[1]]);
        else if (name === 'text' && typeof args[0] === 'function') record.texts.push(args[0]);
        return proxy;
      };
    },
    apply() {
      return proxy;
    },
  });
  return { d3: proxy, record };
}

function runRender(spec: any, isDark = false) {
  const { d3, record } = makeMockD3();
  const cleanup = forceDirectedPlugin.render({} as any, d3, spec, isDark);
  if (typeof cleanup === 'function') cleanup();
  return record;
}

// ── D-024 lenient recovery ───────────────────────────────────────────────────

describe('D-024 — lenient force-directed spec recovery', () => {
  const structured = (def: string) => ({ type: 'force-directed', definition: def });

  const cases: Array<[string, string]> = [
    ['trailing commas', '{ "nodes": [{"id":"A"},{"id":"B"},], "links": [{"source":"A","target":"B"},], }'],
    ['unquoted keys', '{ nodes: [{id:"A"},{id:"B"}], links: [{source:"A",target:"B"}] }'],
    ['single quotes', "{ 'nodes': [{'id':'A'},{'id':'B'}], 'links': [{'source':'A','target':'B'}] }"],
    ['comments', '{ // graph\n "nodes": [{"id":"A"},{"id":"B"}], "links": [] }'],
    [
      'markdown fence',
      '```json\n{ "nodes": [{"id":"A"},{"id":"B"}], "links": [{"source":"A","target":"B"}] }\n```',
    ],
    [
      'smart quotes',
      '{ \u201Cnodes\u201D: [{\u201Cid\u201D:\u201CA\u201D}], \u201Clinks\u201D: [] }',
    ],
    [
      'one-level-deeper wrapper',
      '{ "graph": { "nodes": [{"id":"A"},{"id":"B"}], "links": [{"source":"A","target":"B"}] } }',
    ],
  ];

  it.each(cases)('recovers a node-less spec: %s', (_name, def) => {
    // Direction: the raw definition is NOT strict JSON (or is fence-wrapped), so
    // the old bare JSON.parse path would throw / bail and leave zero nodes.
    let strictThrew = false;
    try {
      const p = JSON.parse(def);
      strictThrew = !Array.isArray(p.nodes); // parsed but wrapper hid nodes
    } catch {
      strictThrew = true;
    }
    expect(strictThrew).toBe(true);

    const resolved = resolveForceDirectedSpec(structured(def));
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes.length).toBeGreaterThanOrEqual(1);
    expect(forceDirectedPlugin.canHandle(structured(def))).toBe(true);
  });

  it('stripDefinitionFence removes matched and unmatched fences', () => {
    expect(stripDefinitionFence('```json\n{"a":1}\n```')).toBe('{"a":1}');
    expect(stripDefinitionFence('```\n{"a":1}')).toBe('{"a":1}');
    expect(stripDefinitionFence('{"a":1}')).toBe('{"a":1}');
  });

  it('normalizeSmartQuotes maps curly quotes to ASCII', () => {
    expect(normalizeSmartQuotes('\u201Cx\u201D')).toBe('"x"');
    expect(normalizeSmartQuotes('\u2018y\u2019')).toBe("'y'");
  });

  it('lenientParseObject slices leading prose and trailing junk', () => {
    expect(lenientParseObject('here: {"a":1};')).toEqual({ a: 1 });
    expect(lenientParseObject('not json at all')).toBeUndefined();
  });

  it('findGraphContainer locates nodes nested under an arbitrary wrapper key', () => {
    const found = findGraphContainer({ chart: { data: { nodes: [{ id: 'A' }], edges: [] } } });
    expect(found?.nodes.length).toBe(1);
    // `edges` is accepted as the links alias
    expect(Array.isArray(found?.links)).toBe(true);
  });

  it('does NOT hijack a spec that carries no nodes array', () => {
    const spec = { type: 'force-directed', definition: '{ "foo": 1 }' };
    const resolved = resolveForceDirectedSpec(spec);
    expect(resolved.nodes).toBeUndefined();
  });
});

// ── D-120 force-tuning levers ────────────────────────────────────────────────

describe('D-120 — charge / linkDistance / collideRadius are honoured', () => {
  const spec = {
    type: 'force-directed',
    definition: JSON.stringify({
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'B' }],
      charge: -8,
      linkDistance: 10,
      collideRadius: 25,
    }),
  };

  it('render() uses the spec values, not the old hardcoded -200 / 80 / size+4', () => {
    const rec = runRender(spec);
    expect(rec.distance).toBe(10); // was hardcoded 80
    expect(rec.strength).toBe(-8); // was hardcoded -200
    expect(typeof rec.radiusFn).toBe('function');
    expect(rec.radiusFn({ size: 5 })).toBe(25); // was (size||8)+4 = 9
  });

  it('falls back to defaults when the levers are absent', () => {
    const rec = runRender({
      type: 'force-directed',
      definition: JSON.stringify({ nodes: [{ id: 'A' }], links: [] }),
    });
    expect(rec.distance).toBe(80);
    expect(rec.strength).toBe(-200);
    expect(rec.radiusFn({ size: 6 })).toBe(10); // (6)+4
  });
});

// ── D-021 label truncation + halo ────────────────────────────────────────────

describe('D-021 — node labels truncated and haloed', () => {
  it('truncateLabel ellipsises past the cap and leaves short labels intact', () => {
    const long = 'N'.repeat(60);
    const t = truncateLabel(long, FORCE_MAX_LABEL_CHARS);
    expect(t.length).toBeLessThanOrEqual(FORCE_MAX_LABEL_CHARS);
    expect(t.endsWith('\u2026')).toBe(true);
    expect(truncateLabel('short', FORCE_MAX_LABEL_CHARS)).toBe('short');
  });

  it('render() paints a canvas-colour halo under the label (paint-order:stroke)', () => {
    const rec = runRender({
      type: 'force-directed',
      definition: JSON.stringify({ nodes: [{ id: 'A' }], links: [] }),
    }, false);
    const hasPaintOrder = rec.attrs.some(([k, v]) => k === 'paint-order' && v === 'stroke');
    const hasCanvasStroke = rec.attrs.some(([k, v]) => k === 'stroke' && v === '#ffffff');
    // Old code had neither of these on the label.
    expect(hasPaintOrder).toBe(true);
    expect(hasCanvasStroke).toBe(true);
  });

  it('the label text callback truncates a long id', () => {
    const rec = runRender({
      type: 'force-directed',
      definition: JSON.stringify({ nodes: [{ id: 'A' }], links: [] }),
    });
    const longNode = { id: 'X'.repeat(50) };
    const truncated = rec.texts.some((fn) => {
      const out = String(fn(longNode));
      return out.length <= FORCE_MAX_LABEL_CHARS && out.endsWith('\u2026');
    });
    expect(truncated).toBe(true);
  });
});

// ── D-023 caller colour cannot erase geometry ────────────────────────────────

describe('D-023 — transparent / unresolvable caller colour falls back', () => {
  it('node fill: transparent and tokens resolve to the palette, not the raw value', () => {
    const rec = runRender({
      type: 'force-directed',
      definition: JSON.stringify({
        nodes: [{ id: 'A', color: 'transparent', group: 0 }],
        links: [],
      }),
    });
    // find the fill attr whose value is the getNodeColor function
    const fillFn = rec.attrs.find(([k, v]) => k === 'fill' && typeof v === 'function')?.[1] as
      | ((d: any) => string)
      | undefined;
    expect(typeof fillFn).toBe('function');
    expect(fillFn!({ color: 'transparent', group: 0 })).not.toBe('transparent');
    expect(fillFn!({ color: 'var(--brand)', group: 0 })).not.toBe('var(--brand)');
    // a valid hex is preserved (identity, when it clears contrast)
    expect(fillFn!({ color: '#e15759', group: 0 }).toLowerCase()).toBe('#e15759');
  });

  it('link stroke: transparent / token resolve to a readable default, not the raw value', () => {
    for (const dark of [false, true]) {
      const light = resolveForceColors(dark, { linkColor: 'transparent' });
      expect(light.linkStroke.toLowerCase()).not.toBe('transparent');
      const tok = resolveForceColors(dark, { linkColor: 'var(--x)' });
      expect(tok.linkStroke.toLowerCase()).not.toBe('var(--x)');
    }
  });
});

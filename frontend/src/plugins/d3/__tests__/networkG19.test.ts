/**
 * G-19 regression tests for the network engine (networkDiagram.ts).
 *
 * Covers three fixed defects; every assertion is written so it FAILS against the
 * pre-fix source and passes only with the change.
 *
 *  D-208 (recovery): resolveNetworkSpec had a lone strict JSON.parse pre-gated
 *        by `definition.trimStart()[0] !== '{'`, so a ```json fence, trailing
 *        commas, unquoted keys, single/smart quotes and semicolon separators
 *        all left the spec node-less -> canHandle false -> 30s empty-DOM hang.
 *        Now recovered via lenientParseNetworkObject. Direction: each raw body
 *        is asserted to NOT be strict-JSON-parseable.
 *
 *  D-197 (structural): a large un-anchored graph was force-laid-out with
 *        forceManyBody(-200) and no bounding/collision force, ejected off-canvas
 *        and then clamped into a coincident perimeter band. computeGridLayout
 *        now gives every node a distinct, in-viewport cell above the threshold.
 *
 *  D-203 (theme): label default `#ccc` (1.61:1 on light) and link default
 *        `#999`@0.6 (1.78:1 light / 2.96:1 dark) were static literals. Colours
 *        are now resolved per effective canvas; asserted in BOTH themes.
 */
import {
  resolveNetworkSpec,
  lenientParseNetworkObject,
  replaceUnquotedSemicolons,
  stripNetworkFence,
  computeGridLayout,
  resolveNetworkColors,
  readableLinkStroke,
  NETWORK_FORCE_LAYOUT_MAX_NODES,
  NETWORK_LIGHT_BG,
  NETWORK_DARK_BG,
} from '../networkDiagram';
import { contrastRatio, compositeOver } from '../chartTheme';

// ── D-208: tolerant definition recovery ──────────────────────────────────────
describe('D-208 — tolerant network spec recovery', () => {
  const wrap = (definition: string) => ({ type: 'network', definition });

  const expectRecovered = (definition: string, nNodes: number, nLinks: number) => {
    const out = resolveNetworkSpec(wrap(definition));
    expect(Array.isArray(out.nodes)).toBe(true);
    expect(out.nodes).toHaveLength(nNodes);
    expect(Array.isArray(out.links)).toBe(true);
    expect(out.links).toHaveLength(nLinks);
  };

  it('w4-01 trailing commas (strict JSON.parse throws -> was node-less)', () => {
    const def = '{ "nodes": [ {"id":"a"}, {"id":"b"}, ], "links": [ {"source":"a","target":"b"}, ] }';
    expect(() => JSON.parse(def)).toThrow(); // direction: unpatched path fails
    expectRecovered(def, 2, 1);
  });

  it('w4-02 unquoted keys', () => {
    const def = '{ nodes: [ {id:"a"}, {id:"b"} ], links: [ {source:"a", target:"b"} ] }';
    expect(() => JSON.parse(def)).toThrow();
    expectRecovered(def, 2, 1);
  });

  it('w4-03 single quotes', () => {
    const def = "{ 'nodes': [ {'id':'a'}, {'id':'b'} ], 'links': [ {'source':'a','target':'b'} ] }";
    expect(() => JSON.parse(def)).toThrow();
    expectRecovered(def, 2, 1);
  });

  it('w4-04 ```json fence around byte-valid JSON (old first-char guard rejected it)', () => {
    const inner = '{ "nodes": [ {"id":"a"}, {"id":"b"} ], "links": [ {"source":"a","target":"b"} ] }';
    const def = '```json\n' + inner + '\n```';
    // Direction: the OLD guard bailed because trimStart()[0] === '`', not '{'.
    expect(def.trimStart()[0]).not.toBe('{');
    expect(stripNetworkFence(def)).toBe(inner);
    expectRecovered(def, 2, 1);
  });

  it('w4-05 smart/curly quotes', () => {
    const def = '{ \u201Cnodes\u201D: [ {\u201Cid\u201D:\u201Ca\u201D}, {\u201Cid\u201D:\u201Cb\u201D} ], \u201Clinks\u201D: [ {\u201Csource\u201D:\u201Ca\u201D,\u201Ctarget\u201D:\u201Cb\u201D} ] }';
    expect(() => JSON.parse(def)).toThrow();
    expectRecovered(def, 2, 1);
  });

  it('w4-06 semicolon member separators (folded outside strings)', () => {
    const def = '{ "nodes": [ {"id":"a"}; {"id":"b"} ]; "links": [ {"source":"a";"target":"b"} ] }';
    expect(() => JSON.parse(def)).toThrow();
    expectRecovered(def, 2, 1);
  });

  it('accepts `edges` alias inside a recovered definition', () => {
    const def = '{ nodes: [ {id:"a"}, {id:"b"} ], edges: [ {source:"a", target:"b"} ] }';
    const out = resolveNetworkSpec(wrap(def));
    expect(out.links).toHaveLength(1);
  });

  it('GUARD: a definition with no nodes is left unchanged (never hijacks another engine)', () => {
    const def = '{ "mark": "bar", "data": [1,2,3] }';
    const spec = wrap(def);
    const out = resolveNetworkSpec(spec);
    expect(out.nodes).toBeUndefined();
  });

  it('GUARD: replaceUnquotedSemicolons leaves a semicolon inside a string value', () => {
    const s = '{ "label": "a; b"; "x": 1 }';
    const out = replaceUnquotedSemicolons(s);
    expect(out).toBe('{ "label": "a; b", "x": 1 }');
  });

  it('unrecoverable garbage returns undefined (degrades, no throw)', () => {
    expect(lenientParseNetworkObject('not json at all')).toBeUndefined();
    expect(lenientParseNetworkObject('')).toBeUndefined();
  });
});

// ── D-197: large-graph grid layout (no perimeter pileup) ──────────────────────
describe('D-197 — computeGridLayout gives every node a distinct visible cell', () => {
  it('200 nodes: all in-viewport and no two coincident', () => {
    const W = 600, H = 400;
    const nodes = Array.from({ length: 200 }, (_, i) => ({ id: `n${i}` }));
    computeGridLayout(nodes, W, H);
    const seen = new Set<string>();
    for (const n of nodes as any[]) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x).toBeLessThanOrEqual(W);
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.y).toBeLessThanOrEqual(H);
      const key = `${n.x.toFixed(3)},${n.y.toFixed(3)}`;
      expect(seen.has(key)).toBe(false); // no coincidence (the anomaly)
      seen.add(key);
    }
    expect(seen.size).toBe(200);
  });

  it('interior is populated, not just a perimeter band', () => {
    const W = 600, H = 400;
    const nodes = Array.from({ length: 250 }, (_, i) => ({ id: `n${i}` }));
    computeGridLayout(nodes, W, H);
    // At least one node sits well inside the central region (would be empty in
    // the clamp-to-edge pileup failure mode).
    const interior = (nodes as any[]).filter(
      n => n.x > W * 0.3 && n.x < W * 0.7 && n.y > H * 0.3 && n.y < H * 0.7,
    );
    expect(interior.length).toBeGreaterThan(0);
  });

  it('the render threshold is exceeded by the pileup-onset counts', () => {
    // Force layout is abandoned for grids above this many un-anchored nodes;
    // the measured pileup was already severe at 200/250 nodes.
    expect(NETWORK_FORCE_LAYOUT_MAX_NODES).toBeLessThan(200);
  });

  it('degenerate canvas falls back to 600x400 without NaN', () => {
    const nodes = [{ id: 'a' }, { id: 'b' }];
    computeGridLayout(nodes, 0, -3);
    for (const n of nodes as any[]) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(Number.isFinite(n.y)).toBe(true);
    }
  });
});

// ── D-203: theme-resolved label + link colours (BOTH themes) ──────────────────
describe('D-203 — label + link colours resolve per theme', () => {
  it('default label clears 4.5:1 in BOTH themes (old #ccc failed light)', () => {
    // Direction: the old static default was invisible on light.
    expect(contrastRatio('#cccccc', NETWORK_LIGHT_BG)).toBeLessThan(4.5);

    const light = resolveNetworkColors(false, {});
    const dark = resolveNetworkColors(true, {});
    expect(contrastRatio(light.labelColor, NETWORK_LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(dark.labelColor, NETWORK_DARK_BG)).toBeGreaterThanOrEqual(4.5);
  });

  it('default link composite clears 3:1 in BOTH themes (old #999@0.6 failed both)', () => {
    // Direction: old default composited below the graphical floor on both.
    expect(contrastRatio(compositeOver('#999999', NETWORK_LIGHT_BG, 0.6), NETWORK_LIGHT_BG)).toBeLessThan(3);
    expect(contrastRatio(compositeOver('#999999', NETWORK_DARK_BG, 0.6), NETWORK_DARK_BG)).toBeLessThan(3);

    const light = resolveNetworkColors(false, {});
    const dark = resolveNetworkColors(true, {});
    expect(contrastRatio(compositeOver(light.linkColor, NETWORK_LIGHT_BG, light.linkOpacity), NETWORK_LIGHT_BG)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(compositeOver(dark.linkColor, NETWORK_DARK_BG, dark.linkOpacity), NETWORK_DARK_BG)).toBeGreaterThanOrEqual(3);
  });

  it('an authored dark link colour that ghosts on dark is reconciled (w1-06 #4a4a4a@0.85)', () => {
    // #4a4a4a @0.85 over #1f1f1f composites to ~1.69:1 — a ghost hairline.
    expect(contrastRatio(compositeOver('#4a4a4a', NETWORK_DARK_BG, 0.85), NETWORK_DARK_BG)).toBeLessThan(3);
    const stroke = readableLinkStroke('#4a4a4a', NETWORK_DARK_BG, 0.85, '#cfcfcf');
    expect(contrastRatio(compositeOver(stroke, NETWORK_DARK_BG, 0.85), NETWORK_DARK_BG)).toBeGreaterThanOrEqual(3);
  });

  it('an authored label that is fine on one theme is reconciled on the other', () => {
    // #111 is fine on light (near-black) but 1.15:1 on dark; the dark-canvas
    // path must lift it to the floor rather than pass it verbatim.
    const dark = resolveNetworkColors(true, { labelColor: '#111111' });
    expect(contrastRatio(dark.labelColor, NETWORK_DARK_BG)).toBeGreaterThanOrEqual(4.5);
    // And a light-canvas label stays dark & readable.
    const light = resolveNetworkColors(false, { labelColor: '#111111' });
    expect(contrastRatio(light.labelColor, NETWORK_LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
  });

  it('a caller-pinned light background under dark theme drives dark labels', () => {
    // labelColor resolved from the RESOLVED bg, not the raw isDarkMode flag.
    const c = resolveNetworkColors(true, { background: '#f7f7f7' });
    expect(c.darkCanvas).toBe(false);
    expect(contrastRatio(c.labelColor, '#f7f7f7')).toBeGreaterThanOrEqual(4.5);
  });
});

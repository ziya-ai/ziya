/**
 * D-167 / G-80 — flowchart-subgraph-crossedge-render-timeout
 *
 * Root cause (confirmed against source, NOT the triage's dagre hypothesis):
 * the flowchart preprocessor ran an UNANCHORED rewrite `/subgraph(\w+)/g ->
 * 'subgraph $1'` intended to space a glued keyword ("subgraphCore" ->
 * "subgraph Core"). It matched the letters "subgraph" *inside a legitimate node
 * identifier* `subgraph_entry`, rewriting it to `subgraph _entry`. That injected
 * a stray `subgraph` keyword mid-graph (a phantom, never-closed nested cluster)
 * and dropped the real `end`, yielding malformed mermaid that dagre laid out
 * for 30s with zero SVG.
 *
 * These tests exercise the pure preprocessor (no build / no headless render;
 * render verification in both themes is deferred to the shared build+render
 * stage per run convention). They FAIL against the unpatched, unanchored regex
 * (which produced `subgraph _entry` and lost the `end`) and pass with the fix.
 */
import { initMermaidEnhancer, preprocessDefinition } from '../mermaidEnhancer';

// The exact failing spec (mermaid-w1-01): a subgraph with an inbound and an
// outbound cross-edge whose entry node id begins with the letters "subgraph".
const W1_01 = `flowchart TD
  Start([Start]) --> Parse[/Parse request/]
  Parse --> Valid{Valid?}
  Valid -- yes --> subgraph_entry
  Valid -- no --> Err[(Reject)]
  subgraph Core[Core pipeline]
    subgraph_entry[Normalize] --> Enrich[[Enrich]]
    Enrich --> Store[(Datastore)]
  end
  Store --> Done([Done])
  Err --> Done
`;

/** Count real subgraph declaration lines (keyword at line start). */
function countSubgraphDecls(def: string): number {
  return (def.match(/^[ \t]*subgraph[ \t]/gm) || []).length;
}
function countEnds(def: string): number {
  return (def.match(/^[ \t]*end[ \t]*$/gm) || []).length;
}

describe('D-167 subgraph_entry node id is not split by the subgraph-spacing pass', () => {
  beforeAll(() => initMermaidEnhancer());

  it('preserves the `subgraph_entry` identifier and never emits `subgraph _entry`', () => {
    const out = preprocessDefinition(W1_01, 'flowchart');
    // The valid node id must survive intact...
    expect(out).toContain('subgraph_entry');
    // ...and the corrupting split must be absent (fails on unpatched code).
    expect(out).not.toMatch(/subgraph +_entry/);
  });

  it('keeps exactly one subgraph declaration with its matching `end` (no phantom cluster)', () => {
    const out = preprocessDefinition(W1_01, 'flowchart');
    const decls = countSubgraphDecls(out);
    const ends = countEnds(out);
    // Unpatched output injected a phantom `subgraph` opener and dropped the real
    // `end` (decls=2, ends=0). Patched: a single balanced cluster.
    expect(decls).toBe(1);
    expect(ends).toBe(1);
    expect(decls).toBe(ends);
  });

  it('completes quickly (pure string pass, no catastrophic work)', () => {
    const t0 = Date.now();
    preprocessDefinition(W1_01, 'flowchart');
    expect(Date.now() - t0).toBeLessThan(3000);
  });
});

describe('D-167 the intended glued-keyword repair is preserved', () => {
  beforeAll(() => initMermaidEnhancer());

  it('still spaces a genuinely glued subgraph declaration (subgraphCore -> subgraph Core)', () => {
    const glued = `flowchart TD
  A --> B
  subgraphCore[Core pipeline]
    B --> C
  end
`;
    const out = preprocessDefinition(glued, 'flowchart');
    // The keyword must be separated from the glued name.
    expect(out).toMatch(/subgraph +Core/);
    // And it must NOT have re-glued or left `subgraphCore` unspaced.
    expect(out).not.toMatch(/subgraphCore/);
  });

  it('never splits a snake_case node id used as an edge endpoint', () => {
    const def = `flowchart TD
  A --> subgraph_worker
  subgraph_worker --> B
`;
    const out = preprocessDefinition(def, 'flowchart');
    expect(out).toContain('subgraph_worker');
    expect(out).not.toMatch(/subgraph +_worker/);
  });
});

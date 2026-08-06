/**
 * Regression test for Issue 23 (chord renderer, links-array form):
 * definition-as-JSON-string CONTRACT MISMATCH + degenerate flow values.
 *
 * render_diagram (app/mcp/tools/diagram_render.py) always ships the real
 * chord JSON as a STRING under `spec.definition`, with only `type` on the
 * outer wrapper. The shipped `isChordSpec` read `matrix`/`nodes`/`links`
 * directly off the top-level spec, so a links-array chord spec submitted via
 * render_diagram was invisible to the plugin: `findPluginForSpec` returned
 * undefined and the orchestrator retried to a ~30s timeout with zero output
 * (same class as joint#2 / network#11 / music#17).
 *
 * Fix (frontend/src/plugins/d3/chordPlugin.ts):
 *   - resolveChordSpec(spec): recover structured fields from a JSON
 *     `definition` string; guarded so it never hijacks a non-chord spec.
 *   - coerceFlowValue(raw, fallback): map NaN/Infinity/-Infinity/negative
 *     link values -> 0, keep finite non-negative magnitudes, so a single
 *     "not-a-number"/null/negative cell cannot poison d3.chord()'s running
 *     sum into MNaN,NaN paths (Issue 10 value-degeneracy).
 *
 * This test imports the REAL exported helpers (not a local re-implementation),
 * so it detects drift if the source changes.
 *
 * NON-VACUOUS: against the pre-fix code these helpers did NOT exist as exports
 * (the module had no resolveChordSpec/coerceFlowValue), so the import + the
 * `canHandle(wrapped) === true` assertion would both fail. The guard cases pin
 * the REJECTION direction too, so the fix is not a catch-all.
 */
import { chordPlugin, resolveChordSpec, coerceFlowValue } from '../chordPlugin';

describe('Issue 23 — chord definition-string contract recovery', () => {
  const linksBody = {
    type: 'chord',
    directed: true,
    nodes: ['A', 'B', 'C'],
    links: [
      { source: 'A', target: 'B', value: 5 },
      { source: 'B', target: 'C', value: 3 },
    ],
  };

  const matrixBody = {
    type: 'chord',
    matrix: [[0, 5, 2], [3, 0, 1], [4, 2, 0]],
    names: ['A', 'B', 'C'],
  };

  it('recovers a links-form spec wrapped in a JSON definition string', () => {
    const wrapped = { type: 'chord', definition: JSON.stringify(linksBody) };
    const resolved = resolveChordSpec(wrapped);
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(3);
    expect(Array.isArray(resolved.links)).toBe(true);
    expect(resolved.links).toHaveLength(2);
    expect(resolved.directed).toBe(true);
  });

  it('recovers a matrix-form spec wrapped in a JSON definition string', () => {
    const wrapped = { type: 'chord', definition: JSON.stringify(matrixBody) };
    const resolved = resolveChordSpec(wrapped);
    expect(Array.isArray(resolved.matrix)).toBe(true);
    expect(resolved.matrix).toHaveLength(3);
    expect(resolved.names).toEqual(['A', 'B', 'C']);
  });

  it('canHandle accepts the wrapped links-form spec (the outage fix)', () => {
    const wrapped = { type: 'chord', definition: JSON.stringify(linksBody) };
    expect(chordPlugin.canHandle(wrapped)).toBe(true);
  });

  it('canHandle accepts the wrapped matrix-form spec', () => {
    const wrapped = { type: 'chord', definition: JSON.stringify(matrixBody) };
    expect(chordPlugin.canHandle(wrapped)).toBe(true);
  });

  it('canHandle still accepts an already-structured links-form spec (back-compat)', () => {
    expect(chordPlugin.canHandle(linksBody)).toBe(true);
  });

  it('canHandle still accepts an already-structured matrix-form spec (back-compat)', () => {
    expect(chordPlugin.canHandle(matrixBody)).toBe(true);
  });

  // ---- GUARD cases: must still REJECT non-chord / malformed input ----

  it('does NOT claim a non-chord type even if its definition carries chord content', () => {
    // resolveChordSpec (like resolveNetworkSpec) does not gate on type — the
    // type guard lives in canHandle/isChordSpec. So the plugin must still
    // REJECT a mermaid-typed wrapper, which is the behavior that matters.
    const wrapped = { type: 'mermaid', definition: JSON.stringify(linksBody) };
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('rejects a definition that is not JSON', () => {
    const wrapped = { type: 'chord', definition: 'graph TD; A-->B' };
    expect(resolveChordSpec(wrapped)).toBe(wrapped);
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('rejects a JSON definition carrying no chord content', () => {
    const wrapped = { type: 'chord', definition: JSON.stringify({ foo: 'bar' }) };
    expect(resolveChordSpec(wrapped)).toBe(wrapped);
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('rejects an empty / whitespace definition', () => {
    const wrapped = { type: 'chord', definition: '   ' };
    expect(resolveChordSpec(wrapped)).toBe(wrapped);
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('rejects null / non-object spec', () => {
    expect(resolveChordSpec(null)).toBeNull();
    expect(chordPlugin.canHandle(null as any)).toBe(false);
  });
});

describe('Issue 23 — coerceFlowValue guards d3.chord() running sum', () => {
  it('maps non-numeric strings to 0', () => {
    expect(coerceFlowValue('not-a-number', 1)).toBe(0);
  });

  it('maps null/undefined to the fallback then coerces (null -> fallback)', () => {
    // null ?? 1 => 1 (a finite non-negative number, kept)
    expect(coerceFlowValue(null, 1)).toBe(1);
    expect(coerceFlowValue(undefined, 1)).toBe(1);
    // with a 0 fallback, null -> 0
    expect(coerceFlowValue(null, 0)).toBe(0);
  });

  it('maps negative values to 0 (chord flow cannot be negative)', () => {
    expect(coerceFlowValue(-500, 1)).toBe(0);
    expect(coerceFlowValue(-1e308, 1)).toBe(0);
  });

  it('maps Infinity / -Infinity / NaN to 0', () => {
    expect(coerceFlowValue(Infinity, 1)).toBe(0);
    expect(coerceFlowValue(-Infinity, 1)).toBe(0);
    expect(coerceFlowValue(NaN, 1)).toBe(0);
  });

  it('keeps finite non-negative magnitudes including huge and tiny', () => {
    expect(coerceFlowValue(0, 1)).toBe(0);
    expect(coerceFlowValue(3.5, 1)).toBe(3.5);
    expect(coerceFlowValue(1e15, 1)).toBe(1e15);
    expect(coerceFlowValue(1e-300, 1)).toBe(1e-300);
    expect(coerceFlowValue(999999999999, 1)).toBe(999999999999);
  });

  it('coerces numeric strings that ARE valid numbers', () => {
    expect(coerceFlowValue('42', 1)).toBe(42);
  });
});

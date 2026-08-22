/**
 * Regression test for ledger Issue 40 (d3/force-directed):
 * definition-as-JSON-string contract mismatch.
 *
 * `render_diagram` wraps the real spec as a JSON STRING under `spec.definition`
 * with only `type` on the outer wrapper. Before the fix, `isForceDirectedSpec`
 * read `nodes`/`links`/`layout` off the wrapper, found no arrays, returned false
 * -> no plugin matched -> the orchestrator busy-retried to a 30s timeout with
 * zero output. `resolveForceDirectedSpec` recovers the inner spec so the plugin
 * matches and renders.
 *
 * These tests import the REAL module (not a re-implementation) so they detect
 * drift, and they pin BOTH directions: wrapped specs are recovered AND already
 * structured / non-force / non-JSON specs are returned untouched.
 */
import {
  resolveForceDirectedSpec,
  forceDirectedPlugin,
} from '../forceDirectedPlugin';

const canHandle = (spec: any) => forceDirectedPlugin.canHandle(spec);

describe('resolveForceDirectedSpec — Issue 40 definition-as-string recovery', () => {
  const innerSpec = {
    type: 'force-directed',
    layout: 'force-directed',
    width: 800,
    height: 600,
    nodes: [{ id: 'a' }, { id: 'b' }],
    links: [{ source: 'a', target: 'b' }],
  };

  // --- Recovery direction: the wrapped spec must be recognised & recovered ---

  it('recovers nodes/links/layout from a definition-as-JSON-string wrapper', () => {
    const wrapped = {
      type: 'force-directed',
      definition: JSON.stringify(innerSpec),
    };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(2);
    expect(Array.isArray(resolved.links)).toBe(true);
    expect(resolved.links).toHaveLength(1);
    expect(resolved.layout).toBe('force-directed');
    expect(resolved.width).toBe(800);
    expect(resolved.height).toBe(600);
  });

  it('canHandle() returns TRUE for the wrapped spec (was false pre-fix)', () => {
    const wrapped = {
      type: 'force-directed',
      definition: JSON.stringify(innerSpec),
    };
    expect(canHandle(wrapped)).toBe(true);
  });

  it('recovers a spec whose layout discriminator lives INSIDE the definition', () => {
    // Only `type` on wrapper; `layout` (and everything else) inside the string.
    const wrapped = {
      type: 'd3',
      definition: JSON.stringify({
        type: 'd3',
        layout: 'force',
        nodes: [{ id: 'x' }, { id: 'y' }],
        links: [{ source: 'x', target: 'y' }],
      }),
    };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved.layout).toBe('force');
    expect(resolved.nodes).toHaveLength(2);
    expect(canHandle(wrapped)).toBe(true);
  });

  it('recovers nested data.nodes / data.links from the definition', () => {
    const wrapped = {
      type: 'force-directed',
      definition: JSON.stringify({
        type: 'force-directed',
        data: {
          nodes: [{ id: 'n1' }, { id: 'n2' }, { id: 'n3' }],
          links: [{ source: 'n1', target: 'n2' }],
        },
      }),
    };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved.nodes).toHaveLength(3);
    expect(resolved.links).toHaveLength(1);
    expect(canHandle(wrapped)).toBe(true);
  });

  it('defaults links to [] when the definition has nodes but no links', () => {
    const wrapped = {
      type: 'force-directed',
      definition: JSON.stringify({ type: 'force-directed', nodes: [{ id: 'solo' }] }),
    };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved.nodes).toHaveLength(1);
    expect(Array.isArray(resolved.links)).toBe(true);
    expect(resolved.links).toHaveLength(0);
  });

  it('recovers a links `edges` alias from the definition', () => {
    const wrapped = {
      type: 'force-directed',
      definition: JSON.stringify({
        type: 'force-directed',
        nodes: [{ id: 'a' }, { id: 'b' }],
        edges: [{ source: 'a', target: 'b' }],
      }),
    };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved.links).toHaveLength(1);
  });

  // --- Guard direction: must NOT hijack / mangle other specs (not a catch-all) ---

  it('returns an already-structured spec UNCHANGED (by reference)', () => {
    const resolved = resolveForceDirectedSpec(innerSpec);
    expect(resolved).toBe(innerSpec); // same object, no copy
  });

  it('leaves a wrapper whose definition has NO nodes untouched', () => {
    // e.g. a chord/network spec wrapped the same way — must not be claimed.
    const wrapped = {
      type: 'chord',
      definition: JSON.stringify({ type: 'chord', matrix: [[0, 1], [1, 0]] }),
    };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved).toBe(wrapped);
    expect(resolved.nodes).toBeUndefined();
    expect(canHandle(wrapped)).toBe(false);
  });

  it('leaves a non-JSON definition string untouched', () => {
    const wrapped = { type: 'force-directed', definition: 'graph TD; A-->B' };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved).toBe(wrapped);
    expect(canHandle(wrapped)).toBe(false);
  });

  it('leaves a malformed (unparseable) JSON definition untouched', () => {
    const wrapped = { type: 'force-directed', definition: '{ "nodes": [ }' };
    const resolved = resolveForceDirectedSpec(wrapped);
    expect(resolved).toBe(wrapped);
  });

  it('tolerates null / non-object input', () => {
    expect(resolveForceDirectedSpec(null)).toBeNull();
    expect(resolveForceDirectedSpec(undefined as any)).toBeUndefined();
    expect(resolveForceDirectedSpec(42 as any)).toBe(42);
  });

  it('is idempotent: resolving twice yields a canHandle-able spec', () => {
    const wrapped = {
      type: 'force-directed',
      definition: JSON.stringify(innerSpec),
    };
    const once = resolveForceDirectedSpec(wrapped);
    const twice = resolveForceDirectedSpec(once);
    expect(canHandle(twice)).toBe(true);
    expect(twice.nodes).toHaveLength(2);
  });
});

/**
 * Regression test for Issue 17/12 (music renderer): the definition-as-JSON
 * -string CONTRACT MISMATCH, the same class already fixed for joint (Issue 2),
 * chord (Issue 10) and network (Issue 11).
 *
 * The render_diagram tool wrapper always ships `{ type: 'music', definition:
 * '<json string>' }`, and the JSON body it hands over does NOT repeat the
 * `type` field (type lives on the wrapper). The music plugin's `canHandle`
 * did `isMusicSpec(JSON.parse(spec.definition))`, and `isMusicSpec` hard-
 * requires `spec.type === 'music'` -- so the parsed body (no `type`) was
 * rejected, AND the wrapper (has `type` but no top-level notes/measures/
 * staves) was rejected. With `canHandle` false the D3Renderer orchestrator
 * found no plugin for `type: 'music'` and retried to a ~30s timeout with zero
 * output -- total data loss, even for a MINIMAL 4-note spec (proving the old
 * ledger "no registered plugin"/"magnitude" theories both wrong).
 *
 * Fix: `resolveMusicSpec` lifts the parsed definition's fields onto a shallow
 * copy and stamps `type: 'music'` -- but ONLY when the parsed body actually
 * carries music content, so it cannot hijack a non-music spec. `canHandle`,
 * `isDefinitionComplete` and `render` all route through it.
 *
 * The test imports the REAL shipped module (no re-implementation) so it
 * detects drift and would FAIL against the pre-fix source (which had no
 * `resolveMusicSpec` export at all -> import would be undefined -> throws).
 */
import { resolveMusicSpec, isMusicSpec } from '../../../utils/d3Plugins/musicPlugin';
import { musicPlugin } from '../musicPlugin';

// The exact minimal wrapper render_diagram builds: type on the wrapper, a
// JSON string definition with NO `type` field inside it.
const wrap = (body: object) => ({
  type: 'music',
  definition: JSON.stringify(body),
  theme: 'light',
});

const MINIMAL_BODY = {
  tempo: 120,
  timeSignature: '4/4',
  notes: [
    { keys: ['c/4'], duration: 'q' },
    { keys: ['e/4'], duration: 'q' },
    { keys: ['g/4'], duration: 'q' },
    { keys: ['c/5'], duration: 'q' },
  ],
};

describe('Issue 17/12 — resolveMusicSpec (definition-string contract mismatch)', () => {
  it('recovers a music spec from a type-less JSON `definition` string and stamps type', () => {
    const resolved = resolveMusicSpec(wrap(MINIMAL_BODY));
    expect(resolved.type).toBe('music');
    expect(Array.isArray(resolved.notes)).toBe(true);
    expect(resolved.notes).toHaveLength(4);
    // Non-note fields from the body survive.
    expect(resolved.timeSignature).toBe('4/4');
    // And crucially the recovered object passes the gate that backs canHandle.
    expect(isMusicSpec(resolved)).toBe(true);
  });

  it('recovers a measures-only spec (the Issue 17 adversarial shape)', () => {
    const body = {
      title: 'stress',
      tempo: 1000000,
      measures: [
        { id: 'm1', notes: [{ keys: ['c/4'], duration: 'q' }] },
      ],
    };
    // Note: the plugin reads staffSpec.measures[].notes; the isMusicSpec gate
    // accepts a measures list whose measures carry notes.
    const resolved = resolveMusicSpec(wrap(body));
    expect(resolved.type).toBe('music');
    expect(isMusicSpec(resolved)).toBe(true);
  });

  it('recovers a grand-staff (staves[].notes) spec', () => {
    const body = {
      timeSignature: '4/4',
      staves: [
        { clef: 'treble', notes: [{ keys: ['c/5'], duration: 'q' }] },
        { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'q' }] },
      ],
    };
    const resolved = resolveMusicSpec(wrap(body));
    expect(resolved.type).toBe('music');
    expect(isMusicSpec(resolved)).toBe(true);
  });

  it('leaves an already-structured spec untouched (does not double-wrap)', () => {
    const structured = { type: 'music', notes: [{ keys: ['c/5'], duration: 'q' }] };
    // Same object reference back: a correctly-authored spec is never rewritten.
    expect(resolveMusicSpec(structured)).toBe(structured);
  });

  // ---- Guard cases: the widened predicate must still REJECT what it rejected ----

  it('does NOT hijack a non-music spec whose definition happens to parse', () => {
    // A network spec routed through the same wrapper: parses to an object, but
    // carries no music content, so resolveMusicSpec returns it UNCHANGED and
    // isMusicSpec still rejects it.
    const networkWrap = {
      type: 'network',
      definition: JSON.stringify({
        nodes: [{ id: 'a' }, { id: 'b' }],
        links: [{ source: 'a', target: 'b' }],
      }),
    };
    const resolved = resolveMusicSpec(networkWrap);
    expect(resolved).toBe(networkWrap); // untouched
    expect(isMusicSpec(resolved)).toBe(false);
  });

  it('returns the spec unchanged when the definition is not JSON', () => {
    const notJson = { type: 'music', definition: 'flowchart TD; A-->B' };
    expect(resolveMusicSpec(notJson)).toBe(notJson);
    expect(isMusicSpec(resolveMusicSpec(notJson))).toBe(false);
  });

  it('returns the spec unchanged on malformed JSON (no throw)', () => {
    const bad = { type: 'music', definition: '{ "notes": [ ' };
    expect(() => resolveMusicSpec(bad)).not.toThrow();
    expect(resolveMusicSpec(bad)).toBe(bad);
  });

  it('does NOT recover a body that parses but has an empty notes array', () => {
    // Empty content is not renderable; resolveMusicSpec must not claim it.
    const emptyWrap = wrap({ notes: [] });
    const resolved = resolveMusicSpec(emptyWrap);
    expect(resolved).toBe(emptyWrap); // untouched -> falls through, no hijack
    expect(isMusicSpec(resolved)).toBe(false);
  });
});

describe('Issue 17/12 — plugin surface routes through resolveMusicSpec', () => {
  it('canHandle ACCEPTS the type-less-definition wrapper (was rejected pre-fix)', () => {
    expect(musicPlugin.canHandle(wrap(MINIMAL_BODY))).toBe(true);
  });

  it('canHandle still accepts a bare structured spec', () => {
    expect(musicPlugin.canHandle({ type: 'music', notes: [{ keys: ['c/5'], duration: 'q' }] }))
      .toBe(true);
  });

  it('canHandle REJECTS a non-music wrapper (no hijack)', () => {
    expect(musicPlugin.canHandle({
      type: 'network',
      definition: JSON.stringify({ nodes: [{ id: 'a' }], links: [] }),
    })).toBe(false);
  });

  it('isDefinitionComplete is true for a complete type-less music definition', () => {
    expect(musicPlugin.isDefinitionComplete!(JSON.stringify(MINIMAL_BODY))).toBe(true);
  });

  it('isDefinitionComplete is false for an incomplete/non-music definition', () => {
    expect(musicPlugin.isDefinitionComplete!('{ "notes": [')).toBe(false);
    expect(musicPlugin.isDefinitionComplete!(JSON.stringify({ nodes: [] }))).toBe(false);
  });
});

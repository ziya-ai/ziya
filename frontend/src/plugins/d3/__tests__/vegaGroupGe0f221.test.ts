/**
 * Group G-e0f221 — D-277 "vl-body-under-vega-schema-renders-blank" (vega engine,
 * spec vega-w4-07, BOTH themes).
 *
 * DUPLICATE SIGNATURE of D-229/D-273, already remediated in prior iterations:
 * a Vega-Lite BODY (object-form `data.values`, SINGULAR `mark`, `encoding`, NO
 * `marks[]`) delivered under a Vega `$schema`/`type:'vega'` was handed to the
 * Vega runtime, which found no marks/scales and painted a SILENT BLANK canvas
 * (no error → the harness saw a "successful" empty render).
 *
 * The live fix has two co-operating sites that MUST agree on what "a VL body"
 * is, or a body slips through one and blanks:
 *   - isVegaSpec() DECLINES an unwrapped VL body so vega-lite-renderer claims it;
 *   - render() detects the WRAPPED body ({type:'vega', definition:{…}}) and
 *     compiles it in vega-embed's 'vega-lite' mode.
 * This iteration extracts that shared discriminator into the exported PURE
 * `isVegaLiteBody`, so both sites cannot drift. These guards pin it to the exact
 * vega-w4-07 body and assert the resulting compile mode in BOTH themes.
 *
 * DIRECTION: `isVegaLiteBody` does not exist on the pre-fix tree, so the import
 * is undefined and every assertion here fails — the guard fails without the
 * change and passes with it. Structural + theme-independent (no colour emitted;
 * the mode assertion is checked for isDarkMode=false AND true).
 */
import {
  isVegaLiteBody,
  buildVegaEmbedOptions,
  reconcileVlBodySchema,
  normalizeVegaEncodeLifecycle,
  applyVegaMinimalDefaults,
  coalesceVegaSplitGeometry,
  rewriteVegaV2Dialect,
} from '../vegaPlugin';

// The exact vega-w4-07 body (from .ziya/gfx-sweep/specs/vega/vega-w4-07.json):
// a Vega $schema + Vega-typed render carrying a Vega-LITE body.
const W4_07_BODY = () => ({
  $schema: 'https://vega.github.io/schema/vega/v5.json',
  width: 380,
  height: 200,
  padding: 5,
  data: { values: [{ c: 'a', v: 30 }, { c: 'b', v: 52 }, { c: 'd', v: 25 }] },
  mark: 'bar',
  encoding: {
    x: { field: 'c', type: 'nominal' },
    y: { field: 'v', type: 'quantitative' },
    color: { value: '#4c78a8' },
  },
});

describe('D-277 isVegaLiteBody discriminates the vega-w4-07 body', () => {
  it('classifies the VL body under a Vega $schema as a VL body', () => {
    expect(isVegaLiteBody(W4_07_BODY())).toBe(true);
  });

  it('classifies the WRAPPED body identically (definition unwrapped first)', () => {
    // render() unwraps {type,definition} then applies the same predicate.
    const wrapped = { type: 'vega', definition: W4_07_BODY() };
    expect(isVegaLiteBody(wrapped.definition)).toBe(true);
  });

  it('does NOT claim a genuine full-Vega spec (marks[] array present)', () => {
    const fullVega: any = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      data: [{ name: 't', values: [{ c: 'a', v: 30 }] }],
      scales: [{ name: 'x', type: 'band', domain: { data: 't', field: 'c' }, range: 'width' }],
      marks: [{ type: 'rect', from: { data: 't' }, encode: { update: {} } }],
    };
    expect(isVegaLiteBody(fullVega)).toBe(false);
  });

  it('does NOT claim a plural-mark Vega spec or a non-object', () => {
    expect(isVegaLiteBody({ mark: ['bar'], encoding: {} })).toBe(false); // mark is an array
    expect(isVegaLiteBody({ encoding: { x: {} } })).toBe(false); // no mark
    expect(isVegaLiteBody({ mark: 'bar' })).toBe(false); // no encoding
    expect(isVegaLiteBody(null)).toBe(false);
    expect(isVegaLiteBody('string')).toBe(false);
  });
});

describe('D-277 the VL body compiles in vega-lite mode in BOTH themes', () => {
  // End-to-end of render()'s decision: mode = isVegaLiteBody ? 'vega-lite' : 'vega'.
  const modeFor = (isDark: boolean) =>
    buildVegaEmbedOptions(isDark, isVegaLiteBody(W4_07_BODY()) ? 'vega-lite' : 'vega').mode;

  it('light theme picks vega-lite mode (not the blank-canvas vega runtime)', () => {
    expect(modeFor(false)).toBe('vega-lite');
  });

  it('dark theme picks vega-lite mode', () => {
    expect(modeFor(true)).toBe('vega-lite');
  });

  it('a full-Vega spec still compiles in vega mode (no regression)', () => {
    const fullVega: any = { marks: [{ type: 'rect' }] };
    const mode = buildVegaEmbedOptions(false, isVegaLiteBody(fullVega) ? 'vega-lite' : 'vega').mode;
    expect(mode).toBe('vega');
  });
});

/**
 * D-277 recurrence guard. The mode decision is only half the recovery: after
 * render() picks 'vega-lite' for the VL body, it strips the misleading Vega
 * `$schema` (reconcileVlBodySchema) and then runs the Vega-only preprocessors
 * (normalizeVegaEncodeLifecycle / applyVegaMinimalDefaults /
 * coalesceVegaSplitGeometry / rewriteVegaV2Dialect) over the SAME object. If any
 * of those ever mutated the VL body into something the Vega runtime paints blank
 * — injecting a `marks[]`, coercing object `data.values` into a Vega data array,
 * or dropping `encoding` — vega-w4-07 would silently blank again even though the
 * mode was correct. This pins the invariant: those transforms must leave the VL
 * body's mode-critical shape intact (singular `mark`, `encoding`, object `data`,
 * NO `marks[]`) AND must NOT re-introduce a Vega `$schema`. Flip any transform
 * to touch a VL body and this fails.
 */
describe('D-277 the post-mode Vega pipeline leaves the VL body VL-shaped', () => {
  const runPipeline = (body: any) => {
    // Mirrors render(): schema stripped, then the Vega-only rewrites in order.
    reconcileVlBodySchema(body);
    let s = body;
    s = normalizeVegaEncodeLifecycle(s);
    s = applyVegaMinimalDefaults(s);
    s = coalesceVegaSplitGeometry(s);
    s = rewriteVegaV2Dialect(s);
    return s;
  };

  it('reconcileVlBodySchema strips the misleading Vega $schema', () => {
    const body: any = W4_07_BODY();
    expect(reconcileVlBodySchema(body)).toBe(true);
    expect(body.$schema).toBeUndefined();
  });

  it('still classifies as a VL body after the full pipeline (both themes agree)', () => {
    const out = runPipeline(W4_07_BODY());
    // The mode is recomputed from the SAME predicate the mode decision used, so
    // if the pipeline corrupted the shape the mode would silently flip to 'vega'.
    expect(isVegaLiteBody(out)).toBe(true);
    expect(buildVegaEmbedOptions(false, isVegaLiteBody(out) ? 'vega-lite' : 'vega').mode).toBe('vega-lite');
    expect(buildVegaEmbedOptions(true, isVegaLiteBody(out) ? 'vega-lite' : 'vega').mode).toBe('vega-lite');
  });

  it('does not re-introduce a Vega $schema or a marks[] array', () => {
    const out = runPipeline(W4_07_BODY());
    // A leftover /vega/ $schema would let vega-embed guessMode override the mode.
    expect(out.$schema === undefined || !String(out.$schema).includes('/vega/')).toBe(true);
    // A synthesised marks[] would flip the runtime into the blank-canvas path.
    expect(Array.isArray(out.marks)).toBe(false);
    // Mode-critical VL fields survive untouched.
    expect(out.mark).toBe('bar');
    expect(out.encoding && typeof out.encoding).toBe('object');
    expect(out.data && Array.isArray(out.data.values)).toBe(true);
  });
});

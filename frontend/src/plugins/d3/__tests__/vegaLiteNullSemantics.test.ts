/**
 * Regression tests for `sanitizeSpec` null handling in vegaLitePlugin.
 *
 * These import the REAL implementation rather than replicating it (as
 * vegaLitePreprocess.test.ts does for the fix functions), because the bug
 * being guarded here is precisely that the shipped function disagreed with
 * Vega-Lite's semantics — a local copy in the test file would have been
 * written to match the test's own expectation and caught nothing.
 *
 * The semantics under test: in Vega-Lite `null` on certain properties means
 * "explicitly disable this", which is NOT equivalent to omitting the
 * property. Dropping `title: null` makes Vega-Lite fall back to the field
 * name, so a suppressed axis title reappears as the field name ("step") and
 * a suppressed legend title reappears as the field name ("k").
 */

import { sanitizeSpec, NULLABLE_VEGA_KEYS } from '../vegaLitePlugin';

describe('sanitizeSpec — null is meaningful on Vega-Lite disable keys', () => {
  it('preserves title:null on an encoding channel (axis title suppression)', () => {
    const out = sanitizeSpec({
      encoding: { x: { field: 'step', type: 'ordinal', title: null } },
    });
    // 'title' in out — not just falsy — is the assertion. An absent key and
    // a null key produce different charts.
    expect('title' in out.encoding.x).toBe(true);
    expect(out.encoding.x.title).toBeNull();
  });

  it('preserves title:null on a color encoding (legend title suppression)', () => {
    const out = sanitizeSpec({
      encoding: { color: { field: 'k', type: 'nominal', title: null } },
    });
    expect('title' in out.encoding.color).toBe(true);
    expect(out.encoding.color.title).toBeNull();
  });

  it('preserves every key in NULLABLE_VEGA_KEYS', () => {
    const spec: Record<string, any> = {};
    for (const key of NULLABLE_VEGA_KEYS) spec[key] = null;

    const out = sanitizeSpec(spec);

    for (const key of NULLABLE_VEGA_KEYS) {
      expect(key in out).toBe(true);
      expect(out[key]).toBeNull();
    }
  });

  it('still strips null on keys where null is not meaningful', () => {
    const out = sanitizeSpec({
      mark: 'bar',
      encoding: { x: { field: null, type: 'ordinal' } },
      description: null,
    });
    expect('field' in out.encoding.x).toBe(false);
    expect('description' in out).toBe(false);
    // Positive control: the surrounding spec survived the walk.
    expect(out.mark).toBe('bar');
    expect(out.encoding.x.type).toBe('ordinal');
  });

  it('strips undefined everywhere, including under nullable keys', () => {
    const out = sanitizeSpec({ title: undefined, mark: 'line' });
    expect('title' in out).toBe(false);
    expect(out.mark).toBe('line');
  });

  it('drops null array ELEMENTS even under a nullable key', () => {
    // scale.domain is reached under 'domain' (not nullable) but a null entry
    // in any array is noise regardless of the enclosing key.
    const out = sanitizeSpec({ sort: ['a', null, 'b'] });
    expect(out.sort).toEqual(['a', 'b']);
  });

  it('does not mistake an array index for a property name', () => {
    // sanitizeSpec's nullable-key exception is keyed on a string property
    // name. Passing the function straight to Array.map would forward the
    // numeric index as the key; assert that a bare null element is dropped
    // rather than accidentally matching.
    const out = sanitizeSpec({ layer: [null, { mark: 'bar' }] });
    expect(out.layer).toHaveLength(1);
    expect(out.layer[0].mark).toBe('bar');
  });

  it('recurses into layered specs, preserving per-layer title:null', () => {
    const out = sanitizeSpec({
      layer: [
        { mark: 'bar', encoding: { x: { field: 'step', title: null } } },
        { mark: 'text', encoding: { x: { field: 'step' } } },
      ],
    });
    expect(out.layer[0].encoding.x.title).toBeNull();
    expect('title' in out.layer[1].encoding.x).toBe(false);
  });

  it('preserves the disable-nulls the plugin\'s own fixes test for', () => {
    // vegaLitePlugin contains `=== null` guards on scale, legend and stack
    // (respect explicit identity mapping / hidden legend / no stacking).
    // Those guards are unreachable if sanitizeSpec removes the nulls first.
    const out = sanitizeSpec({
      encoding: {
        color: { field: 'k', scale: null, legend: null },
        y: { field: 'v', stack: null },
      },
    });
    expect(out.encoding.color.scale).toBeNull();
    expect(out.encoding.color.legend).toBeNull();
    expect(out.encoding.y.stack).toBeNull();
  });

  it('does not mutate the input spec', () => {
    const input = { encoding: { x: { field: 'step', title: null } } };
    const snapshot = JSON.stringify(input);
    sanitizeSpec(input);
    expect(JSON.stringify(input)).toBe(snapshot);
  });
});

describe('sanitizeSpec — end-to-end on the waterfall spec that surfaced this', () => {
  // Abridged from the real spec: a layered waterfall whose x axis title and
  // color legend title were both suppressed with null, and which rendered
  // with "step" and "k" printed anyway.
  const waterfall = {
    $schema: 'https://vega-lite.github.io/schema/v5.json',
    data: { values: [{ step: 'Cash at hire', s: 0, e: 800000, k: 'Level' }] },
    layer: [
      {
        mark: { type: 'bar', width: 56 },
        encoding: {
          x: { field: 'step', type: 'ordinal', title: null },
          y: { field: 's', type: 'quantitative', title: 'Annual cash (USD)' },
          y2: { field: 'e' },
          color: { field: 'k', type: 'nominal', title: null },
        },
      },
    ],
  };

  it('leaves no field-name fallback for the suppressed x and color titles', () => {
    const out = sanitizeSpec(waterfall);
    const enc = out.layer[0].encoding;

    expect(enc.x.title).toBeNull();
    expect(enc.color.title).toBeNull();
    // The y title was authored, so it must survive unchanged — guards
    // against an over-broad "drop all titles" correction.
    expect(enc.y.title).toBe('Annual cash (USD)');
  });
});

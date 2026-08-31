import {
  isLegacySunburstSpec,
  filterVegaChromeMarks,
  reconcileVegaThemeBackground,
  isLightColor,
  normalizeVegaEncodeLifecycle,
  applyVegaMinimalDefaults,
} from '../vegaPlugin';

/**
 * G-34 — vegaPlugin.ts theme + recovery fixes.
 *
 * Defects covered:
 *   D-278 (structural)  filterVegaChromeMarks stripped static-text/group marks
 *         from ANY spec with a data-bound arc — deleting a donut's centre-total
 *         text (vega-w1-05 "340 total"). The strip is now gated on the LEGACY
 *         sunburst signature (data arc + partition/stratify transform), so a
 *         plain donut keeps its annotations.
 *   D-287 (theme, dark) an authored LIGHT top-level `background` was honoured
 *         verbatim in dark while vega-embed's dark theme whitened the guides →
 *         white-on-white (1.00:1). The light background is now dropped in dark
 *         mode so the dark panel applies; light mode / dark-on-dark untouched.
 *   D-275 (recovery)    channels directly under `encode` (no lifecycle set →
 *         zero marks) are wrapped in {update}; and an unnamed dataset / range-
 *         less scale / from-less data mark are conservatively defaulted.
 *
 * Every assertion below fails against the pre-fix code: the helpers did not
 * exist, and the behaviour they replace produced the broken shape asserted as
 * the "before".
 */

describe('D-278 — chrome strip gated on legacy sunburst signature', () => {
  const donutMarks = [
    { type: 'arc', from: { data: 'table' } },
    { type: 'text' }, // static centre-total, e.g. "340 total"
  ];
  const donutSpec = {
    marks: donutMarks,
    data: [{ name: 'table', transform: [{ type: 'pie', field: 'v' }] }],
  };
  const sunburstSpec = {
    marks: [{ type: 'arc', from: { data: 'tree' } }],
    data: [
      { name: 'tree', transform: [{ type: 'stratify' }, { type: 'partition' }] },
    ],
  };

  it('a plain donut (pie transform) is NOT a legacy sunburst', () => {
    expect(isLegacySunburstSpec(donutSpec)).toBe(false);
  });

  it('the OLD unconditional strip WOULD delete the donut centre text (direction check)', () => {
    // filterVegaChromeMarks still keys on a bare data-arc, so calling it
    // directly on the donut proves the pre-fix path erased the annotation —
    // which is exactly why the render() call site is now gated on
    // isLegacySunburstSpec (false above) instead of calling it unconditionally.
    const stripped = filterVegaChromeMarks(donutMarks);
    expect(stripped.some((m: any) => m.type === 'text')).toBe(false);
  });

  it('a real sunburst (partition/stratify) IS a legacy sunburst → strip fires', () => {
    expect(isLegacySunburstSpec(sunburstSpec)).toBe(true);
  });

  it('a spec with no data-bound arc is not a sunburst', () => {
    expect(
      isLegacySunburstSpec({ marks: [{ type: 'rect', from: { data: 't' } }], data: [] }),
    ).toBe(false);
  });
});

describe('D-287 — light background dropped in dark mode only', () => {
  it('isLightColor resolves hex, short hex, rgb and light names', () => {
    expect(isLightColor('#ffffff')).toBe(true);
    expect(isLightColor('#fff')).toBe(true); // hex-length aware
    expect(isLightColor('white')).toBe(true);
    expect(isLightColor('rgb(255,255,255)')).toBe(true);
    expect(isLightColor('#1a1a2e')).toBe(false);
    expect(isLightColor('black')).toBe(false);
  });

  it('DARK mode: an authored light background is removed (the defect)', () => {
    const dark = reconcileVegaThemeBackground({ background: '#ffffff', marks: [] }, true);
    expect(dark.background).toBeUndefined();
    const darkShort = reconcileVegaThemeBackground({ background: '#fff', marks: [] }, true);
    expect(darkShort.background).toBeUndefined();
  });

  it('DARK mode: an authored dark background is kept (no whiteout to fix)', () => {
    const s = reconcileVegaThemeBackground({ background: '#1a1a2e', marks: [] }, true);
    expect(s.background).toBe('#1a1a2e');
  });

  it('LIGHT mode: the authored background is untouched (both directions)', () => {
    expect(reconcileVegaThemeBackground({ background: '#ffffff' }, false).background).toBe('#ffffff');
    expect(reconcileVegaThemeBackground({ background: '#111111' }, false).background).toBe('#111111');
  });
});

describe('D-275 — encode-lifecycle wrap', () => {
  it('wraps bare channels under encode into {update} (was zero marks)', () => {
    const spec = {
      marks: [{ type: 'rect', encode: { x: { scale: 'x', field: 'a' }, y: { scale: 'y', field: 'b' } } }],
    };
    // direction: the raw encode has NO lifecycle key -> Vega draws nothing.
    expect(Object.keys(spec.marks[0].encode)).toEqual(['x', 'y']);
    const out = normalizeVegaEncodeLifecycle(spec);
    expect(Object.keys(out.marks[0].encode)).toEqual(['update']);
    expect((out.marks[0].encode as any).update.x.field).toBe('a');
  });

  it('leaves a correct encode (already has a lifecycle set) unchanged', () => {
    const spec = { marks: [{ type: 'rect', encode: { update: { x: { field: 'a' } } } }] };
    const out = normalizeVegaEncodeLifecycle(spec);
    expect(Object.keys(out.marks[0].encode)).toEqual(['update']);
  });

  it('leaves an enter-only encode unchanged', () => {
    const spec = { marks: [{ type: 'symbol', encode: { enter: { size: { value: 4 } } } }] };
    const out = normalizeVegaEncodeLifecycle(spec);
    expect(Object.keys(out.marks[0].encode)).toEqual(['enter']);
  });

  it('recurses into group-mark children', () => {
    const spec = {
      marks: [{ type: 'group', marks: [{ type: 'rect', encode: { x: { field: 'a' } } }] }],
    };
    const out = normalizeVegaEncodeLifecycle(spec);
    expect(Object.keys(out.marks[0].marks[0].encode)).toEqual(['update']);
  });
});

describe('D-275 — minimal defaulting (unnamed dataset / scale range / mark from)', () => {
  it('names a single unnamed dataset that a scale references', () => {
    const spec = {
      data: [{ values: [{ a: 1 }] }], // no name
      scales: [{ name: 'x', type: 'band', domain: { data: 't', field: 'a' } }],
      marks: [],
    };
    // direction: pre-fix the dataset stays nameless -> "Undefined data set name: t"
    expect(spec.data[0].name).toBeUndefined();
    const out = applyVegaMinimalDefaults(spec);
    expect(out.data[0].name).toBe('t');
  });

  it('infers a missing scale range from the channel the scale drives', () => {
    const spec = {
      data: [{ name: 't', values: [] }],
      scales: [
        { name: 'xs', type: 'band', domain: { data: 't', field: 'a' } }, // no range
        { name: 'ys', type: 'linear', domain: { data: 't', field: 'b' } }, // no range
      ],
      marks: [
        {
          type: 'rect',
          from: { data: 't' },
          encode: { update: { x: { scale: 'xs', field: 'a' }, y: { scale: 'ys', field: 'b' } } },
        },
      ],
    };
    const out = applyVegaMinimalDefaults(spec);
    expect(out.scales[0].range).toBe('width');
    expect(out.scales[1].range).toBe('height');
  });

  it('binds a from-less data mark that references a scale to the sole dataset', () => {
    const spec = {
      data: [{ name: 't', values: [] }],
      scales: [{ name: 'xs', type: 'band', domain: { data: 't', field: 'a' }, range: 'width' }],
      marks: [{ type: 'rect', encode: { update: { x: { scale: 'xs', field: 'a' } } } }], // no from
    };
    expect((spec.marks[0] as any).from).toBeUndefined();
    const out = applyVegaMinimalDefaults(spec);
    expect((out.marks[0] as any).from).toEqual({ data: 't' });
  });

  it('does NOT bind a static text/annotation mark (no scale reference)', () => {
    const spec = {
      data: [{ name: 't', values: [] }],
      marks: [{ type: 'text', encode: { update: { text: { value: 'hello' } } } }],
    };
    const out = applyVegaMinimalDefaults(spec);
    expect((out.marks[0] as any).from).toBeUndefined();
  });
});

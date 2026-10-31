/**
 * Ranged-bar (x/x2 interval mark) regression tests for applyRangedBarFixes.
 *
 * Reproduces the "no datapoints render" class seen with interval charts:
 *   (a) a timeUnit on the primary channel of a ranged mark drops `x` into
 *       Vega-Lite's 2012 reference year while `x2` stays in the data's year,
 *       and floors sub-unit intervals to zero width;
 *   (b) with x2 set, `y` is a point position, so a quantitative y with no y2
 *       renders as a default-thickness sliver (invisible with cornerRadius).
 *
 * Every assertion fails against the pre-fix code: before the fix the timeUnit
 * stays on x, x2 has none, and no y2 is present.
 */

import { applyRangedBarFixes } from '../vegaLitePlugin';

// The user's spec, minimally trimmed (data and styling are irrelevant to the fix).
const lossEpisodes = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { values: [
    { s: '2026-10-17T06:23:00Z', e: '2026-10-17T06:23:30Z', peak: 50, c: 'blip', lbl: '' },
    { s: '2026-10-17T07:38:00Z', e: '2026-10-17T07:39:30Z', peak: 100, c: 'sat', lbl: 'KS115' },
  ] },
  layer: [
    {
      mark: { type: 'bar', cornerRadius: 2 },
      encoding: {
        x: { field: 's', type: 'temporal', title: 'UTC', timeUnit: 'utchoursminutes',
             axis: { labelAngle: 0, labelLimit: 320 } },
        x2: { field: 'e', type: 'temporal' },
        y: { field: 'peak', type: 'quantitative', title: 'peak 30-s probe loss %' },
        color: { field: 'c', type: 'nominal' },
      },
    },
    {
      mark: { type: 'text', dy: -8 },
      encoding: {
        x: { field: 's', type: 'temporal', timeUnit: 'utchoursminutes' },
        y: { field: 'peak', type: 'quantitative' },
        text: { field: 'lbl' },
      },
    },
  ],
});

describe('applyRangedBarFixes: timeUnit on a ranged temporal channel', () => {
  it('translates the bar layer timeUnit to axis.format on a utc scale and drops it', () => {
    const spec = lossEpisodes();
    applyRangedBarFixes(spec);
    const x = spec.layer[0].encoding.x as any;
    expect(x.timeUnit).toBeUndefined();
    expect(x.axis.format).toBe('%H:%M');
    expect(x.scale.type).toBe('utc');
    // Existing axis settings survive the merge.
    expect(x.axis.labelAngle).toBe(0);
    expect(x.axis.labelLimit).toBe(320);
  });

  it('strips the SAME timeUnit from sibling layers sharing the scale (text labels)', () => {
    // A layer that kept the timeUnit would still be positioned in 2012 on
    // the shared x scale, re-creating the decade-wide domain.
    const spec = lossEpisodes();
    applyRangedBarFixes(spec);
    const textX = spec.layer[1].encoding.x as any;
    expect(textX.timeUnit).toBeUndefined();
    expect(textX.scale.type).toBe('utc');
  });

  it('non-utc timeUnit does not force a utc scale', () => {
    const spec = lossEpisodes();
    (spec.layer[0].encoding.x as any).timeUnit = 'hoursminutes';
    (spec.layer[1].encoding.x as any).timeUnit = 'hoursminutes';
    applyRangedBarFixes(spec);
    const x = spec.layer[0].encoding.x as any;
    expect(x.axis.format).toBe('%H:%M');
    expect(x.scale).toBeUndefined();
  });

  it('respects an author-set axis.format and hidden axis', () => {
    const spec = lossEpisodes();
    (spec.layer[0].encoding.x as any).axis = { format: '%H' };
    (spec.layer[1].encoding.x as any).axis = null;
    applyRangedBarFixes(spec);
    expect((spec.layer[0].encoding.x as any).axis.format).toBe('%H');
    expect((spec.layer[0].encoding.x as any).timeUnit).toBeUndefined();
    expect((spec.layer[1].encoding.x as any).axis).toBeNull();
  });

  it('falls back to copying a non-formattable timeUnit onto the secondary channel', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        x: { field: 's', type: 'temporal', timeUnit: 'yearweek' },
        x2: { field: 'e', type: 'temporal' },
        y: { field: 'n', type: 'nominal' },
      },
    };
    applyRangedBarFixes(spec);
    expect(spec.encoding.x.timeUnit).toBe('yearweek');
    expect(spec.encoding.x2.timeUnit).toBe('yearweek');
  });

  it('handles the object form of timeUnit', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        x: { field: 's', type: 'temporal', timeUnit: { unit: 'hoursminutes', utc: true } },
        x2: { field: 'e' },
        y: { field: 'n', type: 'nominal' },
      },
    };
    applyRangedBarFixes(spec);
    expect(spec.encoding.x.timeUnit).toBeUndefined();
    expect(spec.encoding.x.axis.format).toBe('%H:%M');
    expect(spec.encoding.x.scale.type).toBe('utc');
  });

  it('applies symmetrically to y/y2', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        y: { field: 's', type: 'temporal', timeUnit: 'yearmonth' },
        y2: { field: 'e' },
        x: { field: 'n', type: 'nominal' },
      },
    };
    expect(applyRangedBarFixes(spec)).toBe(1);
    expect(spec.encoding.y.timeUnit).toBeUndefined();
    expect(spec.encoding.y.axis.format).toBe('%b %Y');
  });

  it('leaves a band (non-ranged) timeUnit bar alone', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        x: { field: 'd', type: 'temporal', timeUnit: 'yearmonth' },
        y: { field: 'v', type: 'quantitative' },
      },
    };
    expect(applyRangedBarFixes(spec)).toBe(0);
    expect(spec.encoding.x.timeUnit).toBe('yearmonth');
    expect(spec.encoding.y2).toBeUndefined();
  });
});

describe('applyRangedBarFixes: quantitative height on an x-ranged bar', () => {
  it('anchors y at 0 with y2 so the bar has height, not a sliver', () => {
    const spec = lossEpisodes();
    applyRangedBarFixes(spec);
    expect((spec.layer[0].encoding as any).y2).toEqual({ datum: 0 });
    // The text layer is not a bar and must not get a y2.
    expect((spec.layer[1].encoding as any).y2).toBeUndefined();
  });

  it('does not anchor when y is nominal (true Gantt) or y2 already exists', () => {
    const gantt: any = {
      mark: 'bar',
      encoding: {
        x: { field: 's', type: 'temporal' },
        x2: { field: 'e' },
        y: { field: 'task', type: 'nominal' },
      },
    };
    expect(applyRangedBarFixes(gantt)).toBe(0);
    expect(gantt.encoding.y2).toBeUndefined();

    const floating: any = {
      mark: 'bar',
      encoding: {
        x: { field: 's', type: 'temporal' },
        x2: { field: 'e' },
        y: { field: 'lo', type: 'quantitative' },
        y2: { field: 'hi' },
      },
    };
    expect(applyRangedBarFixes(floating)).toBe(0);
    expect(floating.encoding.y2).toEqual({ field: 'hi' });
  });

  it('does not anchor at 0 on a log y scale, and leaves the y/y2 mirror alone', () => {
    const logY: any = {
      mark: 'bar',
      encoding: {
        x: { field: 's', type: 'quantitative' },
        x2: { field: 'e' },
        y: { field: 'v', type: 'quantitative', scale: { type: 'log' } },
      },
    };
    expect(applyRangedBarFixes(logY)).toBe(0);

    const whisker: any = {
      mark: 'bar',
      encoding: {
        y: { field: 'lo', type: 'quantitative' },
        y2: { field: 'hi' },
        x: { field: 'pos', type: 'quantitative' },
      },
    };
    expect(applyRangedBarFixes(whisker)).toBe(0);
    expect(whisker.encoding.x2).toBeUndefined();
  });

  it('operates on concat children as separate scale groups', () => {
    const spec: any = {
      hconcat: [
        { mark: 'bar', encoding: {
          x: { field: 's', type: 'temporal', timeUnit: 'hours' }, x2: { field: 'e' },
          y: { field: 'v', type: 'quantitative' } } },
        { mark: 'point', encoding: {
          x: { field: 's', type: 'temporal', timeUnit: 'hours' },
          y: { field: 'v', type: 'quantitative' } } },
      ],
    };
    applyRangedBarFixes(spec);
    expect(spec.hconcat[0].encoding.x.timeUnit).toBeUndefined();
    expect(spec.hconcat[0].encoding.y2).toEqual({ datum: 0 });
    // Second child is its own scale group with no ranged mark: untouched.
    expect(spec.hconcat[1].encoding.x.timeUnit).toBe('hours');
  });

  it('is null-safe', () => {
    expect(applyRangedBarFixes(null)).toBe(0);
    expect(applyRangedBarFixes({})).toBe(0);
    expect(applyRangedBarFixes({ mark: 'bar' })).toBe(0);
  });
});

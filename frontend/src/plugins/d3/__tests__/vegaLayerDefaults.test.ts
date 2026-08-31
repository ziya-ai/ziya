/**
 * Vega-Lite layer-default injection.
 *
 * Regression coverage for "the chart does not render its full intent": a
 * layered spec came back with a legend titled "Metrics" containing a single
 * entry "P", an axis config the author never wrote, and text marks that had
 * silently vanished. None of it was in the authored spec — all three were
 * injected by vegaLitePlugin's post-processing, and the injected colour
 * scale won Vega-Lite's scale merge and repainted the real marks in the
 * injected background colour.
 *
 * These tests exercise the real exported helpers, not a re-implementation.
 * The suite at the bottom additionally pins the SEAM: the pure helpers here
 * would all pass while vegaLitePlugin still carried the buggy logic inline,
 * because the defect was never in a helper — there were no helpers.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  MAX_AXIS_LABEL_LIMIT,
  applySharedAxisDefaults,
  deduplicateLegendDomains,
  specOwnsColorChannel,
  synthesizeColorLegend,
} from '../vegaLayerDefaults';

/**
 * Reduced form of the chart that exposed the bug: two layers carry real
 * colour scales with legends deliberately suppressed, and a third layer
 * authors its own x axis.
 */
const ttiSpec = () => ({
  layer: [
    {
      mark: { type: 'rect' },
      encoding: {
        x: { field: 't0', type: 'quantitative' },
        color: {
          field: 'state', type: 'nominal',
          scale: { domain: ['ok', 'over'], range: ['#f2f4f5', '#fcdcd6'] },
          legend: null,
        },
      },
    },
    {
      mark: { type: 'text' },
      encoding: {
        x: { field: 'mid', type: 'quantitative' },
        y: { field: 'p', type: 'nominal' },
        color: {
          field: 'state', type: 'nominal',
          scale: { domain: ['ok', 'over'], range: ['#5d6d7e', '#c0392b'] },
          legend: null,
        },
      },
    },
    {
      mark: { type: 'tick', color: '#1f77b4' },
      encoding: {
        x: { field: 't', type: 'quantitative', axis: { labelAngle: 0, values: [0, 129, 258] } },
        y: { field: 'p', type: 'nominal' },
      },
    },
  ],
});

describe('specOwnsColorChannel', () => {
  it('sees a colour field even when its legend is suppressed with null', () => {
    // The pre-fix guard required legend !== null, so it read deliberate
    // suppression as "no legend present, please add one".
    expect(specOwnsColorChannel(ttiSpec())).toBe(true);
  });

  it('sees a colour field when its legend is suppressed with false', () => {
    const spec = ttiSpec();
    spec.layer[0].encoding.color.legend = false as any;
    expect(specOwnsColorChannel(spec)).toBe(true);
  });

  it('is false when colours are only hardcoded mark values', () => {
    expect(specOwnsColorChannel({
      layer: [{ mark: { type: 'line', color: '#c0392b' }, encoding: { y: { field: 'a' } } }],
    })).toBe(false);
  });

  it('is safe on null / non-layered input', () => {
    expect(specOwnsColorChannel(null)).toBe(false);
    expect(specOwnsColorChannel({ mark: 'bar' })).toBe(false);
  });
});

describe('synthesizeColorLegend', () => {
  it('adds nothing to a spec that already owns the colour channel', () => {
    const spec = ttiSpec();
    const result = synthesizeColorLegend(spec);
    expect(result.added).toBe(false);
    expect(result.skipped).toBe('spec-owns-color-channel');
    // The load-bearing assertion: no fourth layer, so no competing colour
    // scale to win the merge and repaint the text marks.
    expect(spec.layer).toHaveLength(3);
  });

  it('does not manufacture a one-entry legend from a y field name', () => {
    // This produced legend "Metrics" / entry "P" from y.field === 'p'.
    const spec = {
      layer: [
        { mark: { type: 'rule', color: '#c0392b' }, encoding: { x: { field: 't' } } },
        { mark: { type: 'tick', color: '#1f77b4' }, encoding: { x: { field: 't' }, y: { field: 'p' } } },
      ],
    };
    const result = synthesizeColorLegend(spec);
    expect(result.added).toBe(false);
    expect(result.skipped).toBe('not-a-series-set');
    expect(spec.layer).toHaveLength(2);
  });

  it('does not manufacture a legend whose entries all collapse to one label', () => {
    // Every layer plots the same y field, so capitalising it yields N
    // identical labels — a legend that distinguishes nothing.
    const spec = {
      layer: [
        { mark: { type: 'bar', color: '#1a1a2e' }, encoding: { y: { field: 'task' }, x: { field: 'a' } } },
        { mark: { type: 'bar', color: '#2a9d8f' }, encoding: { y: { field: 'task' }, x: { field: 'b' } } },
      ],
    };
    expect(synthesizeColorLegend(spec).added).toBe(false);
    expect(spec.layer).toHaveLength(2);
  });

  // Positive control: without this the absence assertions above could pass
  // simply because the pass had been disabled outright.
  it('still adds a legend for a multi-measure chart with hardcoded colours', () => {
    const spec = {
      layer: [
        { mark: { type: 'line', color: '#1f77b4' }, encoding: { x: { field: 't' }, y: { field: 'queue_depth' } } },
        { mark: { type: 'line', color: '#c0392b' }, encoding: { x: { field: 't' }, y: { field: 'drop_rate' } } },
      ],
    };
    const result = synthesizeColorLegend(spec);
    expect(result.added).toBe(true);
    expect(result.series).toEqual(['Queue Depth', 'Drop Rate']);
    expect(spec.layer).toHaveLength(3);
    const legend: any = spec.layer[2];
    expect(legend.encoding.color.scale.range).toEqual(['#1f77b4', '#c0392b']);
    expect(legend.mark).toEqual({ type: 'point', size: 0, opacity: 0 });
  });

  it('does not run on a single-layer spec', () => {
    const spec = { layer: [{ mark: { type: 'line', color: '#1f77b4' }, encoding: { y: { field: 'a' } } }] };
    expect(synthesizeColorLegend(spec).skipped).toBe('not-layered');
  });
});

describe('applySharedAxisDefaults', () => {
  it('injects nothing for a channel a layer already authored', () => {
    // x is a SHARED channel across layers, so a per-layer default is not a
    // default — it is a conflict Vega-Lite resolves by picking one value.
    const spec = ttiSpec();
    const injected = applySharedAxisDefaults(spec);
    expect(injected.filter(k => k.startsWith('x'))).toEqual([]);
    expect(spec.layer[0].encoding.x).not.toHaveProperty('axis');
    expect((spec.layer[2].encoding.x as any).axis.values).toEqual([0, 129, 258]);
  });

  // Positive control for the assertion above.
  it('injects once, on the first encoding layer, when nobody authored an axis', () => {
    const spec = {
      layer: [
        { mark: 'line', encoding: { x: { field: 't' }, y: { field: 'a' } } },
        { mark: 'line', encoding: { x: { field: 't' }, y: { field: 'b' } } },
      ],
    };
    expect(applySharedAxisDefaults(spec)).toEqual(['x@0', 'y@0']);
    expect((spec.layer[0].encoding.x as any).axis).toEqual({
      labelAngle: 0, labelLimit: MAX_AXIS_LABEL_LIMIT, labelFontSize: 11, labelOverlap: true,
    });
    expect((spec.layer[0].encoding.y as any).axis).toEqual({
      labelLimit: MAX_AXIS_LABEL_LIMIT, labelFontSize: 11,
    });
    expect(spec.layer[1].encoding.x).not.toHaveProperty('axis');
    expect(spec.layer[1].encoding.y).not.toHaveProperty('axis');
  });

  it('injects per layer when the axis is resolved independent', () => {
    // With resolve.axis independent there is no merge, so each layer needs
    // its own defaults and no conflict can arise.
    const spec = {
      resolve: { axis: { y: 'independent' } },
      layer: [
        { mark: 'line', encoding: { y: { field: 'a' } } },
        { mark: 'line', encoding: { y: { field: 'b' } } },
      ],
    };
    expect(applySharedAxisDefaults(spec)).toEqual(['y@0', 'y@1']);
  });

  it('skips a channel no layer encodes', () => {
    const spec = { layer: [{ mark: 'rule', encoding: { x: { field: 't' } } }, { mark: 'rule', encoding: { x: { field: 'u' } } }] };
    expect(applySharedAxisDefaults(spec)).toEqual(['x@0']);
  });

  // D-253 (nominal-axis-labels-forced-90deg): a top-level, non-layered unit
  // spec must ALSO receive the shared axis defaults (labelAngle:0 etc). The
  // layer-only version early-returned here, leaving Vega-Lite to rotate even
  // single-character nominal labels 90°. These assertions FAIL against the
  // pre-fix code, which returned [] and injected no axis.
  it('injects the shared defaults onto a top-level (non-layered) unit spec', () => {
    const spec: any = { mark: 'bar', encoding: { x: { field: 'cat', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } } };
    expect(applySharedAxisDefaults(spec)).toEqual(['x', 'y']);
    expect(spec.encoding.x.axis).toEqual({
      labelAngle: 0, labelLimit: MAX_AXIS_LABEL_LIMIT, labelFontSize: 11, labelOverlap: true,
    });
    expect(spec.encoding.y.axis).toEqual({
      labelLimit: MAX_AXIS_LABEL_LIMIT, labelFontSize: 11,
    });
  });

  it('leaves an authored axis on a non-layered spec untouched', () => {
    const spec: any = { mark: 'bar', encoding: { x: { field: 'cat', axis: { labelAngle: -45, values: ['a'] } } } };
    expect(applySharedAxisDefaults(spec)).toEqual([]);
    expect(spec.encoding.x.axis).toEqual({ labelAngle: -45, values: ['a'] });
  });

  it('reaches the inner spec of a facet spec (encoding lives on spec.spec)', () => {
    // A facet spec has no top-level encoding/layer; the plotting encoding is
    // on the inner unit spec. Pre-fix, layersOf(outer)===[] -> early [], so
    // the faceted x axis never got labelAngle:0.
    const spec: any = {
      facet: { field: 'g', type: 'nominal' },
      spec: { mark: 'bar', encoding: { x: { field: 'cat', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } } },
    };
    expect(applySharedAxisDefaults(spec)).toEqual(['spec.x', 'spec.y']);
    expect(spec.spec.encoding.x.axis).toMatchObject({ labelAngle: 0, labelOverlap: true });
    expect(spec.spec.encoding.y.axis).toMatchObject({ labelLimit: MAX_AXIS_LABEL_LIMIT });
  });

  it('reaches a layered inner spec inside a facet/repeat container', () => {
    const spec: any = {
      spec: { layer: [{ mark: 'line', encoding: { x: { field: 't' }, y: { field: 'a' } } }] },
    };
    expect(applySharedAxisDefaults(spec)).toEqual(['spec.x@0', 'spec.y@0']);
    expect(spec.spec.layer[0].encoding.x.axis).toMatchObject({ labelAngle: 0 });
  });

  // D-270 (dense-nominal-labels-overprint-no-thinning): the x default must
  // carry labelOverlap so a high-cardinality nominal axis thins instead of
  // overprinting. Band/point scales default to labelOverlap:false, so without
  // this the 200-category axis emits every label. FAILS pre-fix (the default
  // had no labelOverlap key).
  it('enables labelOverlap on the injected x axis but not on y', () => {
    const spec: any = { mark: 'bar', encoding: { x: { field: 'cat', type: 'nominal' }, y: { field: 'v' } } };
    applySharedAxisDefaults(spec);
    // x thins dense nominal labels; band/point scales default to
    // labelOverlap:false so this is what fixes the 200-category overprint.
    expect(spec.encoding.x.axis.labelOverlap).toBe(true);
    // y deliberately omits it: quantitative y already defaults to
    // labelOverlap:true in Vega-Lite, and the overprint defect is x-only.
    expect(spec.encoding.y.axis).not.toHaveProperty('labelOverlap');
  });
});

describe('deduplicateLegendDomains', () => {
  it('replaces duplicate domain entries with distinct labels', () => {
    const spec = {
      layer: [
        { mark: { type: 'bar', color: '#1a1a2e' }, encoding: { y: { field: 'task' }, x: { field: 'background' } } },
        { mark: { type: 'bar', color: '#2a9d8f' }, encoding: { y: { field: 'task' }, x: { field: 'actual' } } },
        { mark: { type: 'tick', color: '#ffd700' }, encoding: { y: { field: 'task' }, x: { field: 'target' } } },
        {
          data: { values: [{ series: 'Task' }, { series: 'Task' }, { series: 'Task' }] },
          mark: { type: 'point', size: 0, opacity: 0 },
          encoding: {
            color: {
              field: 'series', type: 'nominal',
              scale: { domain: ['Task', 'Task', 'Task'], range: ['#1a1a2e', '#2a9d8f', '#ffd700'] },
              legend: { title: 'Metrics' },
            },
          },
        },
      ],
    };

    expect(deduplicateLegendDomains(spec)).toBe(1);
    const scale: any = (spec.layer[3] as any).encoding.color.scale;
    expect(new Set(scale.domain).size).toBe(3);
    expect(scale.domain.slice(1)).toEqual(['Actual', 'Target']);
    // Entry 0 is 'Task', not 'Background': the label inference deliberately
    // excludes an x field named 'background' (a backdrop bar) and falls back
    // to the shared y field, which is the category axis and labels nothing
    // useful. Asserted as-is because this pass is untouched here; the label
    // quality is a separate defect, not a regression introduced by this fix.
    expect(scale.domain[0]).toBe('Task');
    expect(scale.range).toEqual(['#1a1a2e', '#2a9d8f', '#ffd700']);
    expect((spec.layer[3] as any).data.values.map((d: any) => d.series)).toEqual(scale.domain);
  });

  it('leaves an already-distinct domain alone', () => {
    const spec = {
      layer: [{
        mark: { type: 'point', size: 0, opacity: 0 },
        encoding: {
          color: {
            field: 'series', type: 'nominal',
            scale: { domain: ['Remaining', 'Actual', 'Target'], range: ['#1', '#2', '#3'] },
          },
        },
      }],
    };
    expect(deduplicateLegendDomains(spec)).toBe(0);
    expect((spec.layer[0] as any).encoding.color.scale.domain).toEqual(['Remaining', 'Actual', 'Target']);
  });
});

/**
 * Seam coverage. Every assertion above would pass with vegaLitePlugin still
 * running its own inline copies, so the wiring is pinned separately.
 * Comments are stripped before matching because the plugin's own comments
 * quote the old predicate to document what changed.
 */
// The spec as reported: a two-row tick strip (y field 'p') with a rule layer
// for TTI boundaries. It authored no color field, so the synthesis pass fired
// and produced a legend titled "Metrics" holding the single entry "P" — the
// capitalised y-field name, labelling nothing and costing plot width. The rule
// layer also received injected axis defaults, which then conflicted with the
// tick layer's authored x axis during Vega-Lite's axis merge.
describe('the reported spec (per-TTI packet arrival strip)', () => {
  const reportedSpec = () => ({
    width: 'container',
    height: 400,
    layer: [
      {
        // TTI boundary rules: x only, no authored axis.
        data: { values: [{ t: 0 }, { t: 258 }, { t: 516 }] },
        mark: { type: 'rule', color: '#c0392b', strokeWidth: 2 },
        encoding: { x: { field: 't', type: 'quantitative' } },
      },
      {
        // Packet arrivals, two nominal rows sharing the y field 'p'.
        data: { values: [{ t: 12.9, p: 'uniform' }, { t: 40, p: 'bursty' }] },
        mark: { type: 'tick', thickness: 2, size: 26, color: '#1f77b4' },
        encoding: {
          x: {
            field: 't',
            type: 'quantitative',
            title: 'µs',
            scale: { domain: [0, 520], nice: false },
            axis: { labelAngle: 0, labelLimit: 320, labelFontSize: 11 },
          },
          y: { field: 'p', type: 'nominal', title: null, axis: { labelLimit: 210 } },
        },
      },
    ],
  });

  it('no longer manufactures a "Metrics" legend holding only "P"', () => {
    const spec = reportedSpec();
    const before = spec.layer.length;
    const result = synthesizeColorLegend(spec);

    expect(result.added).toBe(false);
    expect(result.skipped).toBe('not-a-series-set');
    expect(spec.layer).toHaveLength(before);
    // Specifically: no layer anywhere carries a legend titled Metrics.
    expect(
      spec.layer.some((l: any) => l?.encoding?.color?.legend?.title === 'Metrics')
    ).toBe(false);
  });

  it('leaves the authored x axis alone instead of conflicting with it', () => {
    const spec = reportedSpec();
    applySharedAxisDefaults(spec);

    // The rule layer must NOT gain an axis: it shares the x scale with the
    // tick layer, so an injected default there is a merge conflict, not a
    // default. Vega-Lite reported exactly this as
    // 'Conflicting axis property "labelLimit" (110 and 320)'.
    expect(spec.layer[0].encoding.x).not.toHaveProperty('axis');

    // The authored axis survives untouched, including its tick config.
    expect(spec.layer[1].encoding.x.axis).toEqual({
      labelAngle: 0,
      labelLimit: 320,
      labelFontSize: 11,
    });
    expect(spec.layer[1].encoding.y.axis).toEqual({ labelLimit: 210 });
  });
});

describe('vegaLitePlugin wiring', () => {
  const source = fs.readFileSync(path.join(__dirname, '../vegaLitePlugin.ts'), 'utf8');
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map(line => line.replace(/\/\/.*$/, ''))
    .join('\n');

  it('imports the helpers rather than defining the logic inline', () => {
    expect(code).toMatch(/from\s+'\.\/vegaLayerDefaults'/);
  });

  it.each([
    'deduplicateLegendDomains(',
    'synthesizeColorLegend(',
    'applySharedAxisDefaults(',
  ])('calls %s', (call) => {
    expect(code).toContain(call);
  });

  it('no longer treats legend:null as an absent legend', () => {
    // The pre-fix guard: layer.encoding.color.legend !== null && ... !== false
    expect(code).not.toMatch(/color\.legend\s*!==\s*null/);
    expect(code).not.toMatch(/color\.legend\s*!==\s*false/);
  });

  it('no longer builds legend entries inline from a y field name', () => {
    expect(code).not.toContain("legend: { title: 'Metrics' }");
  });

  it('no longer assigns per-layer axis defaults inline', () => {
    // Scoped to the DEFAULT-INJECTION shape, not to per-layer axis assignment
    // in general. A bare `layer.encoding.y.axis = {}` is also how the
    // dual-axis pass works, and there it is correct: under
    // resolve.scale.y:'independent' each layer draws its own axis, so it must
    // be configured per layer (first left, rest right). The injection this
    // asserts against is recognisable by the label default object it writes.
    expect(code).not.toMatch(
      /encoding\.[xy]\.axis\s*=\s*\{[^}]*labelLimit/s
    );
    expect(code).not.toMatch(
      /encoding\.[xy]\.axis\s*=\s*\{[^}]*labelFontSize/s
    );
  });

  // Positive controls, so the absence assertions above cannot pass by their
  // whole region having been deleted.
  it('keeps the labelLimit clamp pass', () => {
    expect(code).toContain('clampAxisLabelLimits');
    expect(code).toMatch(/MAX_AXIS_LABEL_LIMIT/);
  });

  it('keeps the dual-axis per-layer orient pass', () => {
    // Distinct from default injection and legitimately per-layer; if the
    // assertion above ever widens far enough to forbid this, it has
    // overreached.
    expect(code).toMatch(/encoding\.y\.axis\.orient/);
  });
});

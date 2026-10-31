/**
 * A temporal axis labelled "UTC" must be drawn in UTC (vegaTemporalZone).
 *
 * Reported spec (2026-10-20, "Cell 776194 — median downlink SNR"): data rows
 * in -07:00, a red rule at 2026-10-19T08:19:05Z, x axis `title: "UTC"`,
 * `format: "%H:%M"`, no scale type.  Vega-Lite renders temporal fields in the
 * browser's zone by default, so the axis read 01:10 … 01:25 under a "UTC"
 * title while the caption said ~08:19Z.  Both instants were right; only the
 * labelling lied.
 *
 * Measured with the real vega-lite compiler under node (not assumed):
 *   - scale.type:"utc" switches the AXIS to utcFormat, but a tooltip entry
 *     with `format` still compiles to local timeFormat;
 *   - formatType:"utcFormat" + config.customFormatTypes:true compiles the
 *     tooltip to utcFormat(datum.t, "<same format>");
 *   - with utc on one layer only, the shared scale is utc but the other
 *     layers' own formatting stays local, so every layer must be touched.
 *
 * DIRECTION: vegaTemporalZone does not exist before the fix, so this suite
 * cannot compile against the unpatched tree; the seam block also fails if
 * the module exists but the plugin never calls it.
 */
import * as fs from 'fs';
import * as path from 'path';
import { docTitleClaim, fixTemporalZoneMismatch, utcClaim } from '../vegaTemporalZone';

const ROWS = [
  { t: '2026-10-19T01:10:00-07:00', snr_med: 11.84, snr_p10: 11.36, sat: 'sat57' },
  { t: '2026-10-19T01:19:00-07:00', snr_med: 10.63, snr_p10: 9.76, sat: 'sat236' },
  { t: '2026-10-19T01:25:50-07:00', snr_med: 12.46, snr_p10: 12.03, sat: 'sat266' },
];

const LEGEND = { orient: 'bottom', columns: 2 };

/** The reported chart, trimmed to three rows. */
function reported(): any {
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    title: 'Cell 776194 — median downlink SNR',
    data: { values: ROWS },
    layer: [
      {
        mark: { type: 'line', point: true },
        encoding: {
          x: { field: 't', type: 'temporal', title: 'UTC', axis: { format: '%H:%M' } },
          y: { field: 'snr_med', type: 'quantitative' },
          color: { field: 'sat', type: 'nominal', legend: LEGEND },
          tooltip: [
            { field: 't', type: 'temporal', format: '%H:%M:%S' },
            { field: 'sat' },
            { field: 'snr_med' },
          ],
        },
      },
      {
        mark: { type: 'line', strokeDash: [3, 3] },
        encoding: {
          x: { field: 't', type: 'temporal' },
          y: { field: 'snr_p10', type: 'quantitative' },
          color: { field: 'sat', type: 'nominal', legend: LEGEND },
        },
      },
      {
        data: { values: [{ t: '2026-10-19T08:19:05Z' }] },
        mark: { type: 'rule', color: 'red' },
        encoding: { x: { field: 't', type: 'temporal' } },
      },
    ],
  };
}

describe('reported spec: axis titled UTC, scale rendered local', () => {
  const spec = reported();
  const msgs = fixTemporalZoneMismatch(spec);

  it('derives the UTC claim from the axis title and reports what it did', () => {
    expect(msgs.length).toBeGreaterThan(0);
    expect(msgs.join('\n')).toMatch(/title.*UTC/i);
    expect(msgs.join('\n')).toMatch(/scale\.type.*utc/);
  });

  it('sets scale.type utc on EVERY layer sharing the x scale, including the rule', () => {
    for (const layer of spec.layer) {
      expect(layer.encoding.x.scale).toEqual(expect.objectContaining({ type: 'utc' }));
    }
  });

  it('keeps the axis title and format (the claim was right; the scale was wrong)', () => {
    expect(spec.layer[0].encoding.x.title).toBe('UTC');
    expect(spec.layer[0].encoding.x.axis.format).toBe('%H:%M');
  });

  it('moves the tooltip time to utcFormat with the same format string', () => {
    const t = spec.layer[0].encoding.tooltip[0];
    expect(t.format).toBe('%H:%M:%S');
    expect(t.formatType).toBe('utcFormat');
    expect(spec.config.customFormatTypes).toBe(true);
    // Untouched siblings.
    expect(spec.layer[0].encoding.tooltip[1]).toEqual({ field: 'sat' });
  });

  it('does not rewrite data values', () => {
    expect(spec.data.values).toEqual(ROWS);
    expect(spec.layer[2].data.values).toEqual([{ t: '2026-10-19T08:19:05Z' }]);
  });

  it('is idempotent', () => {
    expect(fixTemporalZoneMismatch(spec)).toEqual([]);
  });
});

describe('claim detection', () => {
  it('reads the title, axis title, a literal Z/UTC in the format, utcFormat in labelExpr, utc timeUnit, utc scale', () => {
    expect(utcClaim({ title: 'Time (UTC)' })).toMatch(/UTC/);
    expect(utcClaim({ axis: { title: 'GMT' } })).toMatch(/GMT/);
    expect(utcClaim({ axis: { title: ['Time', 'Z'] } })).toBeTruthy();
    expect(utcClaim({ axis: { format: '%H:%MZ' } })).toMatch(/format/);
    expect(utcClaim({ axis: { labelExpr: "utcFormat(datum.value, '%H')" } })).toMatch(/labelExpr/);
    expect(utcClaim({ timeUnit: 'utchours' })).toMatch(/timeUnit/);
    expect(utcClaim({ timeUnit: { unit: 'hours', utc: true } })).toMatch(/timeUnit/);
    expect(utcClaim({ scale: { type: 'utc' } })).toMatch(/scale/);
  });
  it('does not see UTC in ordinary words or in % directives', () => {
    expect(utcClaim({ title: 'Frequency (Hz)' })).toBeNull();
    expect(utcClaim({ title: 'Zone' })).toBeNull();
    expect(utcClaim({ axis: { format: '%Z' } })).toBeNull();   // %Z is a directive, not a literal
    expect(utcClaim({ title: 'time' })).toBeNull();
  });
});

describe('leaves specs alone that make no UTC claim', () => {
  it('no claim, offset data: untouched', () => {
    const spec = reported();
    spec.layer[0].encoding.x.title = 'time';
    const before = JSON.stringify(spec);
    expect(fixTemporalZoneMismatch(spec)).toEqual([]);
    expect(JSON.stringify(spec)).toBe(before);
  });
  it('a title that names a local zone wins over a Z-stated datum in another layer', () => {
    const spec = reported();
    spec.layer[0].encoding.x.title = 'Pacific (PDT)';
    const before = JSON.stringify(spec);
    expect(fixTemporalZoneMismatch(spec)).toEqual([]);
    expect(JSON.stringify(spec)).toBe(before);
  });
  it('a non-temporal x is never touched even under a UTC title', () => {
    const spec: any = {
      data: { values: [{ k: 'a', v: 1 }] }, mark: 'bar',
      encoding: { x: { field: 'k', type: 'nominal', title: 'UTC' }, y: { field: 'v', type: 'quantitative' } },
    };
    expect(fixTemporalZoneMismatch(spec)).toEqual([]);
    expect(spec.encoding.x.scale).toBeUndefined();
  });
});

describe('the data itself can carry the claim', () => {
  it('every value states Z -> utc scale (same rule vegaDateExpr applies to utcFormat)', () => {
    const spec: any = {
      data: { values: [{ t: '2026-10-19T08:19:05Z', v: 1 }, { t: '2026-10-19T08:29:05Z', v: 2 }] },
      mark: 'line',
      encoding: { x: { field: 't', type: 'temporal' }, y: { field: 'v', type: 'quantitative' } },
    };
    const msgs = fixTemporalZoneMismatch(spec);
    expect(msgs.join('\n')).toMatch(/values state UTC/);
    expect(spec.encoding.x.scale.type).toBe('utc');
  });
  it('date-only values -> utc scale (Date.parse reads them as UTC midnight; local shows the day before west of UTC)', () => {
    const spec: any = {
      data: { values: [{ d: '2026-10-19', v: 1 }, { d: '2026-10-20', v: 2 }] },
      mark: 'bar',
      encoding: { x: { field: 'd', type: 'temporal' }, y: { field: 'v', type: 'quantitative' } },
    };
    fixTemporalZoneMismatch(spec);
    expect(spec.encoding.x.scale.type).toBe('utc');
  });
  it('offset-stated values with no label claim stay local', () => {
    const spec: any = {
      data: { values: ROWS }, mark: 'line',
      encoding: { x: { field: 't', type: 'temporal' }, y: { field: 'snr_med', type: 'quantitative' } },
    };
    expect(fixTemporalZoneMismatch(spec)).toEqual([]);
    expect(spec.encoding.x.scale).toBeUndefined();
  });
});

describe('other temporal knobs follow the scale', () => {
  it('a local timeUnit under a UTC title becomes its utc variant (string and object forms)', () => {
    const spec: any = {
      data: { values: ROWS }, mark: 'bar',
      encoding: {
        x: { field: 't', type: 'temporal', timeUnit: 'hours', title: 'Hour (UTC)' },
        y: { field: 'snr_med', type: 'quantitative' },
        tooltip: [{ field: 't', timeUnit: { unit: 'hoursminutes' } }],
      },
    };
    fixTemporalZoneMismatch(spec);
    expect(spec.encoding.x.timeUnit).toBe('utchours');
    expect(spec.encoding.x.scale.type).toBe('utc');
    expect(spec.encoding.tooltip[0].timeUnit).toEqual({ unit: 'hoursminutes', utc: true });
    expect(spec.config?.customFormatTypes).toBeUndefined();   // only needed for formatType
  });
  it('axis labelExpr timeFormat -> utcFormat; a bare temporal tooltip gets a utc timeUnit', () => {
    const spec: any = {
      data: { values: ROWS }, mark: 'line',
      encoding: {
        x: { field: 't', type: 'temporal', title: 'UTC', axis: { labelExpr: "timeFormat(datum.value, '%H:%M')" } },
        y: { field: 'snr_med', type: 'quantitative' },
        tooltip: { field: 't', type: 'temporal' },
      },
    };
    fixTemporalZoneMismatch(spec);
    expect(spec.encoding.x.axis.labelExpr).toBe("utcFormat(datum.value, '%H:%M')");
    expect(spec.encoding.tooltip.timeUnit).toEqual({ unit: 'yearmonthdatehoursminutesseconds', utc: true });
  });
  it('a y temporal axis is repaired, and a concat sibling with no zone of its own follows it', () => {
    const spec: any = {
      data: { values: ROWS },
      hconcat: [
        { mark: 'line', encoding: { y: { field: 't', type: 'temporal', title: 'UTC' }, x: { field: 'snr_med', type: 'quantitative' } } },
        { mark: 'line', encoding: { x: { field: 't', type: 'temporal', title: 'time' }, y: { field: 'snr_med', type: 'quantitative' } } },
      ],
    };
    fixTemporalZoneMismatch(spec);
    expect(spec.hconcat[0].encoding.y.scale.type).toBe('utc');
    expect(spec.hconcat[1].encoding.x.scale.type).toBe('utc');
  });
});

/**
 * Reported 2026-10-21 ("Brewster (cell 776194), 4-s bins"): a 5-panel
 * vconcat, each panel with its own -07:00 data, chart title
 * {text: [".. 2026-10-20 18:00Z → 10-21", "06:48Z"], subtitle: [..]}, and
 * only the bottom x axis titled "UTC".  Per-view repair drew the bottom
 * panel 18:30 … 06:30 and the four above it 11:00 … 23:30 (PDT).
 */
const REPORTED_TITLE = {
  text: ['Brewster (cell 776194), 4-s bins, 2026-10-20 18:00Z → 10-21', '06:48Z'],
  subtitle: ['CTs affected per bin by source; red ticks = hard events, orange ticks = soft', 'events'],
};
function stackedPanels(title: any, lastTitle: string | undefined = 'UTC'): any {
  const panel = (field: string, xTitle?: string) => ({
    data: { values: [{ [field]: '2026-10-20T11:03:46-07:00', v: 1 }, { [field]: '2026-10-20T23:48:00-07:00', v: 2 }] },
    mark: 'line',
    encoding: {
      x: { field, type: 'temporal', ...(xTitle !== undefined ? { title: xTitle } : {}) },
      y: { field: 'v', type: 'quantitative', title: 'CTs' },
      tooltip: [{ field, type: 'temporal', format: '%H:%M:%S' }],
    },
  });
  return {
    ...(title !== undefined ? { title } : {}),
    vconcat: [panel('t0'), panel('bin'), panel('t'), panel('t4'), panel('tb', lastTitle)],
  };
}
const xScales = (spec: any) => spec.vconcat.map((p: any) => p.encoding.x.scale?.type);

describe('one chart, one clock: concat panels share the zone claim', () => {
  it('reported layout: chart title states Z times, bottom axis titled UTC -> every panel utc', () => {
    const spec = stackedPanels(REPORTED_TITLE);
    const msgs = fixTemporalZoneMismatch(spec);
    expect(xScales(spec)).toEqual(['utc', 'utc', 'utc', 'utc', 'utc']);
    expect(msgs).toHaveLength(5);
    expect(msgs[0]).toMatch(/^vconcat\[0\]\.x: chart title/);
    expect(msgs[4]).toMatch(/^vconcat\[4\]\.x: axis title "UTC"/);   // own claim wins over the inherited one
    // Tooltips in the inheriting panels follow too.
    expect(spec.vconcat[0].encoding.tooltip[0].formatType).toBe('utcFormat');
    expect(spec.config.customFormatTypes).toBe(true);
  });
  it('no chart title: the bottom panel alone claiming UTC still carries the others', () => {
    const spec = stackedPanels(undefined);
    const msgs = fixTemporalZoneMismatch(spec);
    expect(xScales(spec)).toEqual(['utc', 'utc', 'utc', 'utc', 'utc']);
    expect(msgs[0]).toMatch(/vconcat\[4\]\.x claims UTC \(axis title "UTC"\)/);
  });
  it('the chart title alone is a claim', () => {
    const spec = stackedPanels({ text: 'Loss events', subtitle: '18:00Z → 06:48Z' }, undefined);
    fixTemporalZoneMismatch(spec);
    expect(xScales(spec)).toEqual(['utc', 'utc', 'utc', 'utc', 'utc']);
  });
  it('a panel whose title names a local zone keeps it', () => {
    const spec = stackedPanels(undefined);
    spec.vconcat[1].encoding.x.title = 'PDT';
    fixTemporalZoneMismatch(spec);
    expect(xScales(spec)).toEqual(['utc', undefined, 'utc', 'utc', 'utc']);
  });
  it('a chart title naming a local zone stops inheritance; the claiming panel is still repaired', () => {
    const spec = stackedPanels('Brewster, times in PDT');
    fixTemporalZoneMismatch(spec);
    expect(xScales(spec)).toEqual([undefined, undefined, undefined, undefined, 'utc']);
  });
  it('prose in a chart title does not veto ("west", "est")', () => {
    const spec = stackedPanels('Cells west of the river, est. loss');
    fixTemporalZoneMismatch(spec);
    expect(xScales(spec)).toEqual(['utc', 'utc', 'utc', 'utc', 'utc']);
  });
  it('no claim anywhere: nothing changes', () => {
    const spec = stackedPanels('Brewster loss events', undefined);
    const before = JSON.stringify(spec);
    expect(fixTemporalZoneMismatch(spec)).toEqual([]);
    expect(JSON.stringify(spec)).toBe(before);
  });
  it('docTitleClaim reads UTC words and HH:MMZ times, not bare times or words starting with Z', () => {
    expect(docTitleClaim({ title: REPORTED_TITLE })).toMatch(/chart title/);
    expect(docTitleClaim({ title: ['Loss', 'all times UTC'] })).toBeTruthy();
    expect(docTitleClaim({ title: '18:00 → 06:48' })).toBeNull();
    expect(docTitleClaim({ title: 'Zone A loss' })).toBeNull();
    expect(docTitleClaim({})).toBeNull();
  });
});

describe('plugin seam', () => {
  it('vegaLitePlugin imports the pass and runs it in the preprocess chain', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'vegaLitePlugin.ts'), 'utf-8');
    expect(src).toMatch(/import \{ fixTemporalZoneMismatch \} from '\.\/vegaTemporalZone'/);
    expect(src).toMatch(/fixTemporalZoneMismatch\(spec\)\.forEach\(msg => console\.log\(`🔧 VEGA-PREPROCESS: \$\{msg\}`\)\)/);
    // Runs after the date-expression repair, which classifies the same fields.
    expect(src.indexOf('fixUnparsedDateFormatting(spec)')).toBeLessThan(src.indexOf('fixTemporalZoneMismatch(spec)'));
  });
});

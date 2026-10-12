/**
 * timeFormat / utcFormat over a field that is still a string (vegaDateExpr).
 *
 * Reported spec (2026-10-10, "GGMA receive-chain state"): a heatmap whose x
 * field came from
 *   calculate: "timeFormat(datum.t,'%m-%d')+' '+timeFormat(datum.t,'%y')+'Z'"
 * over rows with t = "2018-10-03T00:00:00".  `t` is never encoded temporal,
 * so Vega-Lite never parses it; d3-time-format coerces the string with
 * `+date` and every row's label became "NaN-NaN NaNZ", collapsing all 30
 * columns into one.  Measured with real vega-lite + vega under node (not
 * assumed): before the repair the three sample rows give
 * ["NaN-NaN NaNZ" x3]; after it, ["10-03 18Z","10-04 00Z","10-04 06Z"], in
 * both TZ=UTC and TZ=America/Los_Angeles.  A date-only field formatted with
 * timeFormat gave "Oct 02" for "2026-10-03" in Los Angeles; utcFormat gives
 * "Oct 03".
 *
 * DIRECTION: vegaDateExpr does not exist before the fix, so this suite cannot
 * compile against the unpatched tree; the seam block also fails if the
 * module exists but the plugin never calls it.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  classifyField, fixUnparsedDateFormatting, repairDateFormatExpr,
} from '../vegaDateExpr';

const REPORTED = "timeFormat(datum.t,'%m-%d')+' '+timeFormat(datum.t,'%y')+'Z'";
const ISO_ROWS = [
  { row: 'a', t: '2018-10-03T00:00:00', state: 'idle' },
  { row: 'a', t: '2000-10-04T00:00:00', state: 'OK' },
];

const reportedSpec = (): any => ({
  data: { values: ISO_ROWS.map(r => ({ ...r })) },
  transform: [{ calculate: REPORTED, as: 'bin' }],
  mark: 'rect',
  encoding: {
    x: { field: 'bin', type: 'nominal', sort: 'ascending' },
    y: { field: 'row', type: 'nominal' },
    color: { field: 'state', type: 'nominal' },
  },
});

describe('fixUnparsedDateFormatting — the reported spec', () => {
  it('wraps both calls in toDate and reports the rewrite', () => {
    const spec = reportedSpec();
    const msgs = fixUnparsedDateFormatting(spec);
    expect(spec.transform[0].calculate).toBe(
      "timeFormat(toDate(datum.t),'%m-%d')+' '+timeFormat(toDate(datum.t),'%y')+'Z'");
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatch(/^transform\[0\]\.calculate: /);
  });

  it('leaves the data and the encoding untouched', () => {
    const spec = reportedSpec();
    const before = reportedSpec();
    fixUnparsedDateFormatting(spec);
    expect(spec.data).toEqual(before.data);
    expect(spec.encoding).toEqual(before.encoding);
  });

  it('is idempotent', () => {
    const spec = reportedSpec();
    fixUnparsedDateFormatting(spec);
    const once = JSON.parse(JSON.stringify(spec));
    expect(fixUnparsedDateFormatting(spec)).toEqual([]);
    expect(spec).toEqual(once);
  });
});

describe('repairDateFormatExpr — variants', () => {
  const iso = () => 'date-string' as const;
  const dateOnly = () => 'date-only' as const;

  it.each([
    ["utcFormat(datum.t, '%H')", "utcFormat(toDate(datum.t), '%H')"],
    ["timeFormat(datum['t'], '%H')", "timeFormat(toDate(datum['t']), '%H')"],
    ['timeFormat(datum["time stamp"], "%H")', 'timeFormat(toDate(datum["time stamp"]), "%H")'],
    ["timeFormat( datum.t , '%H')", "timeFormat(toDate(datum.t), '%H')"],
    ["timeFormat(+datum.t,'%m')", "timeFormat(toDate(datum.t),'%m')"],
    ["timeFormat(toNumber(datum.t),'%m')", "timeFormat(toDate(datum.t),'%m')"],
    ["timeFormat(datetime(datum.t),'%m')", "timeFormat(toDate(datum.t),'%m')"],
    // A comma inside the format string is not an argument boundary.
    ["timeFormat(datum.t, '%b, %d')", "timeFormat(toDate(datum.t), '%b, %d')"],
    ["'w'+timeFormat(datum.t,'%U')+(datum.x>0?'+':'')", "'w'+timeFormat(toDate(datum.t),'%U')+(datum.x>0?'+':'')"],
  ])('%s', (input, expected) => {
    expect(repairDateFormatExpr(input, iso)).toBe(expected);
  });

  it('switches timeFormat to utcFormat for a date-only field', () => {
    expect(repairDateFormatExpr("timeFormat(datum.d,'%b %d')", dateOnly))
      .toBe("utcFormat(toDate(datum.d),'%b %d')");
    expect(repairDateFormatExpr("timeFormat(toDate(datum.d),'%b %d')", dateOnly))
      .toBe("utcFormat(toDate(datum.d),'%b %d')");
  });

  it('switches timeFormat to utcFormat for a field whose values state UTC', () => {
    const utc = () => 'utc-string' as const;
    expect(repairDateFormatExpr("timeFormat(datum.t,'%H')", utc)).toBe("utcFormat(toDate(datum.t),'%H')");
    expect(repairDateFormatExpr("timeFormat(+datum.t,'%H')", utc)).toBe("utcFormat(toDate(datum.t),'%H')");
    // Already UTC: only the toDate wrap is added.
    expect(repairDateFormatExpr("utcFormat(datum.t,'%H')", utc)).toBe("utcFormat(toDate(datum.t),'%H')");
  });

  it('keeps timeFormat for a date-time string without an offset', () => {
    expect(repairDateFormatExpr("timeFormat(datum.t,'%H')", iso)).toBe("timeFormat(toDate(datum.t),'%H')");
  });

  it('leaves calls that already produce a date', () => {
    for (const e of ["timeFormat(toDate(datum.t),'%H')", "timeFormat(now(),'%H')",
      "utcFormat(utc(2026,9,3),'%d')", "timeFormat(1700000000000,'%Y')"]) {
      expect(repairDateFormatExpr(e, iso)).toBe(e);
    }
  });

  it('leaves a numeric (epoch) field as written', () => {
    expect(repairDateFormatExpr("timeFormat(datum.ms,'%Y')", () => 'number')).toBe("timeFormat(datum.ms,'%Y')");
  });

  it('does not match names that only end in timeFormat', () => {
    for (const e of ["myTimeFormat(datum.t,'%H')", "obj.timeFormat(datum.t,'%H')"]) {
      expect(repairDateFormatExpr(e, iso)).toBe(e);
    }
  });

  it('leaves a call whose first argument never closes', () => {
    expect(repairDateFormatExpr('timeFormat(datum.t', iso)).toBe('timeFormat(datum.t');
  });

  it('repairs a call nested in another call', () => {
    expect(repairDateFormatExpr("upper(timeFormat(datum.t,'%b'))", iso)).toBe("upper(timeFormat(toDate(datum.t),'%b'))");
  });
});

describe('classifyField', () => {
  it('classifies from inline values', () => {
    expect(classifyField(['2026-10-03', '2026-10-04'])).toBe('date-only');
    // Mixed: one value states UTC, one does not -> not treated as UTC.
    expect(classifyField(['2018-10-03T00:00:00', '2026-10-03T18:00:00Z'])).toBe('date-string');
    expect(classifyField(['2026-10-03T18:00:00Z', '2026-10-04T00:00:00.000z'])).toBe('utc-string');
    expect(classifyField(['2026-10-03T18:00:00+00:00', '2026-10-04T00:00:00-0000'])).toBe('utc-string');
    expect(classifyField(['2026-10-03T18:00:00+02:00'])).toBe('date-string');
    expect(classifyField([1700000000000, 1])).toBe('number');
    expect(classifyField(['1700000000000'])).toBe('other');
    expect(classifyField(['RF-silent'])).toBe('other');
    expect(classifyField(undefined)).toBe('other');
  });
});

describe('fixUnparsedDateFormatting — where it looks', () => {
  it('reaches calculate, filter, labelExpr and condition tests in nested views', () => {
    const spec: any = {
      data: { values: [{ d: '2026-10-03', t: '2026-10-03T18:00:00' }] },
      hconcat: [
        { transform: [{ calculate: "timeFormat(datum.d,'%b %d')", as: 'day' }], mark: 'bar' },
        {
          layer: [{
            transform: [{ filter: { and: ["timeFormat(datum.t,'%H')=='18'"] } }],
            mark: 'point',
            encoding: {
              x: { field: 't', type: 'ordinal', axis: { labelExpr: "timeFormat(datum.value,'%H')" } },
              color: { condition: { test: "timeFormat(datum.t,'%H')=='18'", value: 'red' }, value: 'grey' },
            },
          }],
        },
      ],
    };
    const msgs = fixUnparsedDateFormatting(spec);
    expect(msgs).toHaveLength(4);
    expect(spec.hconcat[0].transform[0].calculate).toBe("utcFormat(toDate(datum.d),'%b %d')");
    const layer = spec.hconcat[1].layer[0];
    expect(layer.transform[0].filter.and[0]).toBe("timeFormat(toDate(datum.t),'%H')=='18'");
    expect(layer.encoding.x.axis.labelExpr).toBe("timeFormat(toDate(datum.value),'%H')");
    expect(layer.encoding.color.condition.test).toBe("timeFormat(toDate(datum.t),'%H')=='18'");
  });

  it('rewrites a calculate over explicit-Z rows to utcFormat, and leaves offset-less rows local', () => {
    const z: any = {
      data: { values: [{ t: '2026-10-03T18:00:00Z' }, { t: '2026-10-04T00:00:00Z' }] },
      transform: [{ calculate: "timeFormat(datum.t,'%m-%d %H')+'Z'", as: 'bin' }],
      mark: 'rect',
    };
    fixUnparsedDateFormatting(z);
    expect(z.transform[0].calculate).toBe("utcFormat(toDate(datum.t),'%m-%d %H')+'Z'");
    const local = reportedSpec();
    fixUnparsedDateFormatting(local);
    expect(local.transform[0].calculate.startsWith('timeFormat(')).toBe(true);
  });

  it('reads samples from top-level datasets', () => {
    const spec: any = {
      datasets: { rows: [{ d: '2026-10-03' }] },
      data: { name: 'rows' },
      transform: [{ calculate: "timeFormat(datum.d,'%d')", as: 'x' }],
      mark: 'bar',
    };
    fixUnparsedDateFormatting(spec);
    expect(spec.transform[0].calculate).toBe("utcFormat(toDate(datum.d),'%d')");
  });

  it('does not touch strings outside expression keys', () => {
    const spec: any = {
      title: "timeFormat(datum.t,'%H')",
      data: { values: [{ t: "timeFormat(datum.t,'%H')" }] },
      mark: 'bar',
      encoding: { x: { field: 't', type: 'nominal', title: "timeFormat(datum.t,'%H')" } },
    };
    const before = JSON.parse(JSON.stringify(spec));
    expect(fixUnparsedDateFormatting(spec)).toEqual([]);
    expect(spec).toEqual(before);
  });

  it('a spec with no date formatting is unchanged', () => {
    const spec: any = { data: { values: [{ a: 1 }] }, transform: [{ calculate: 'datum.a*2', as: 'b' }], mark: 'bar' };
    const before = JSON.parse(JSON.stringify(spec));
    expect(fixUnparsedDateFormatting(spec)).toEqual([]);
    expect(spec).toEqual(before);
  });
});

describe('seam — vegaLitePlugin applies the repair', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'vegaLitePlugin.ts'), 'utf8');

  it('imports the helper', () => {
    expect(src).toMatch(/import\s*\{[^}]*fixUnparsedDateFormatting[^}]*\}\s*from\s*'\.\/vegaDateExpr'/);
  });

  it('calls it on the spec in the preprocessing pass, beside the log-scale fix', () => {
    const call = src.indexOf('fixUnparsedDateFormatting(spec)');
    const logScales = src.indexOf('fixLogScales(spec).forEach');
    expect(logScales).toBeGreaterThan(-1);
    expect(call).toBeGreaterThan(-1);
    expect(Math.abs(call - logScales)).toBeLessThan(800);
  });
});

/**
 * Plotly traces bound to ziya://dataset/<id> (plotlyDatasets.ts).  Before
 * this, a map from an `analyze` result had its numbers hand-copied into the
 * spec.  The fetcher is stubbed; the seam into plotlyPlugin is asserted on
 * the source so a refactor that drops the call fails here.
 */
import * as fs from 'fs';
import * as path from 'path';

import { expandDatasetTrace, hasPlotlyDatasetRefs, resolvePlotlyDatasets } from '../plotlyDatasets';

const ID = 'ds_218aa62dc98d87d6';
const URL = `ziya://dataset/${ID}`;
const LINKS = [
  { a_lat: 47.61, a_lon: -122.33, b_lat: 39.04, b_lon: -77.49, band: '< 50 ms', label: 'SEA–IAD ~48 ms' },
  { a_lat: 39.04, a_lon: -77.49, b_lat: 53.35, b_lon: -6.26, band: '50–100 ms', label: 'IAD–DUB ~71 ms' },
  { a_lat: 53.35, a_lon: -6.26, b_lat: 50.11, b_lon: 8.68, band: '< 50 ms', label: 'DUB–FRA ~14 ms' },
  { a_lat: 50.11, a_lon: 8.68, b_lat: 35.68, b_lon: 139.69, band: '> 100 ms', label: 'FRA–NRT ~122 ms' },
];
const fetcher = (rows: any[]) => async (id: string) => ({ id, row_count: rows.length, returned: rows.length, truncated: false, rows });

describe('expandDatasetTrace', () => {
  it('fills attributes from columns, including dotted paths, and drops the binding', () => {
    const rows = [{ lat: 1, lon: 2, n: 'A', t: 5 }, { lat: 3, lon: 4, n: 'B', t: 9 }];
    const [t] = expandDatasetTrace({ type: 'scattergeo', mode: 'markers', marker: { color: 'red' },
      dataset: { url: URL, columns: { lat: 'lat', lon: 'lon', text: 'n', 'marker.size': 't', customdata: ['n', 't'] } } }, rows);
    expect(t).toEqual({ type: 'scattergeo', mode: 'markers', lat: [1, 3], lon: [2, 4], text: ['A', 'B'],
      marker: { color: 'red', size: [5, 9] }, customdata: [['A', 5], ['B', 9]] });
  });

  it('draws one null-separated line per row, grouped and styled per value', () => {
    const traces = expandDatasetTrace({ type: 'scattergeo', mode: 'lines', line: { width: 2 },
      dataset: { url: URL, columns: { text: 'label' }, segments: { from: ['a_lat', 'a_lon'], to: ['b_lat', 'b_lon'] },
        groupBy: 'band', groupStyle: { '> 100 ms': { line: { color: '#d62728' } } } } }, LINKS);
    expect(traces.map((t) => t.name)).toEqual(['< 50 ms', '50–100 ms', '> 100 ms']);
    expect(traces[0].lat).toEqual([47.61, 39.04, null, 53.35, 50.11, null]);
    expect(traces[0].lon).toEqual([-122.33, -77.49, null, -6.26, 8.68, null]);
    expect(traces[0].text).toEqual(['SEA–IAD ~48 ms', 'SEA–IAD ~48 ms', null, 'DUB–FRA ~14 ms', 'DUB–FRA ~14 ms', null]);
    expect(traces[2].line).toEqual({ width: 2, color: '#d62728' });
    expect(traces[0].line).toEqual({ width: 2 });
  });

  it('names a misspelt column and lists the real ones', () => {
    expect(() => expandDatasetTrace({ type: 'scattergeo', dataset: { url: URL, columns: { lat: 'latitude' } } }, LINKS, ID))
      .toThrow(/no column "latitude"; columns are a_lat, a_lon, b_lat, b_lon, band, label/);
  });

  it('rejects a malformed segments block', () => {
    expect(() => expandDatasetTrace({ type: 'scattergeo', dataset: { url: URL, segments: { from: ['a_lat'] } } }, LINKS))
      .toThrow(/segments/);
  });
});

describe('resolvePlotlyDatasets', () => {
  const spec = () => ({ data: [
    { type: 'scattergeo', mode: 'markers', lat: [0], lon: [0], name: 'inline' },
    { type: 'scattergeo', mode: 'lines', dataset: { url: URL, segments: { from: ['a_lat', 'a_lon'], to: ['b_lat', 'b_lon'] }, groupBy: 'band' } },
  ], layout: {} });

  it('expands bound traces in place and keeps inline ones in order', async () => {
    const res = await resolvePlotlyDatasets(spec(), fetcher(LINKS));
    expect(res.resolved).toEqual([ID]);
    expect(res.spec.data.map((t: any) => t.name)).toEqual(['inline', '< 50 ms', '50–100 ms', '> 100 ms']);
    expect(hasPlotlyDatasetRefs(res.spec)).toBe(false);
  });

  it('reports a failed fetch and leaves the spec alone', async () => {
    const s = spec();
    const res = await resolvePlotlyDatasets(s, async () => { throw new Error('404'); });
    expect(res.failed).toEqual([ID]);
    expect(res.spec).toBe(s);
  });

  it('refuses any url that is not a ziya dataset handle', async () => {
    const s = { data: [{ type: 'scattergeo', dataset: { url: 'https://evil.example/rows.json' } }] };
    await expect(resolvePlotlyDatasets(s, fetcher(LINKS))).rejects.toThrow(/must be ziya:\/\/dataset\//);
  });

  it('is a no-op for a spec with no bindings', async () => {
    const s = { data: [{ type: 'bar', x: [1], y: [2] }] };
    const res = await resolvePlotlyDatasets(s, fetcher(LINKS));
    expect(res.spec).toBe(s);
    expect(res.resolved).toEqual([]);
  });
});

describe('the plotly plugin resolves bindings before preprocessing', () => {
  it('calls resolvePlotlyDatasets ahead of preprocessPlotlySpec', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../plotlyPlugin.ts'), 'utf8');
    expect(src).toContain("import { hasPlotlyDatasetRefs, resolvePlotlyDatasets } from './plotlyDatasets';");
    const call = src.indexOf('await resolvePlotlyDatasets(plotlySpec)');
    const pre = src.indexOf('plotlySpec = preprocessPlotlySpec(plotlySpec)');
    expect(call).toBeGreaterThan(-1);
    expect(pre).toBeGreaterThan(call);
  });
});

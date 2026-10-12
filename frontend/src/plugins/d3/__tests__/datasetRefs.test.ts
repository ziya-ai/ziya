/**
 * @jest-environment jsdom
 *
 * Datasets by reference (design/dataset-store.md): a Vega-Lite spec may
 * carry `data: { url: "ziya://dataset/<id>" }` at any nesting level; the
 * resolver swaps it for inline rows fetched from /api/datasets/<id>/rows.
 *
 * Two layers are tested here:
 *   1. the pure resolver module with an injected fetcher, and
 *   2. the seam: vegaLitePlugin must invoke the resolver AFTER parsing and
 *      BEFORE sanitizeSpec / stripExternalDataUrls, otherwise the SSRF
 *      stripper removes the reference before it can be resolved (the chart
 *      renders empty and the feature is silently dead).
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  DATASET_URL_RE,
  parseDatasetUrl,
  findDatasetRefs,
  hasDatasetRefs,
  resolveDatasetRefs,
  defaultDatasetFetcher,
  DATASET_FETCH_LIMIT,
  DatasetRowsResponse,
} from '../datasetRefs';

const ID_A = 'ds_0123456789abcdef';
const ID_B = 'ds_fedcba9876543210';
const url = (id: string) => `ziya://dataset/${id}`;

const okResponse = (id: string, rows: Record<string, unknown>[], truncated = false): DatasetRowsResponse => ({
  id, rows, returned: rows.length, row_count: truncated ? rows.length + 1 : rows.length, truncated,
});

describe('parseDatasetUrl / DATASET_URL_RE', () => {
  it('accepts exactly the ds_ + 16 hex scheme', () => {
    expect(parseDatasetUrl(url(ID_A))).toBe(ID_A);
    expect(DATASET_URL_RE.test(url(ID_B))).toBe(true);
  });

  it('rejects everything else, including loopback URLs and traversal', () => {
    for (const bad of [
      'http://localhost:6969/api/datasets/' + ID_A + '/rows',
      'ziya://dataset/../etc/passwd',
      'ziya://dataset/ds_0123456789abcde',      // 15 hex
      'ziya://dataset/ds_0123456789abcdefg',    // 17 chars
      'ziya://dataset/ds_ZZZZZZZZZZZZZZZZ',
      'ziya://dataset/' + ID_A + '?x=1',
      'ZIYA://dataset/' + ID_A,
      'data/cars.json',
      42, null, undefined, { url: url(ID_A) },
    ]) {
      expect(parseDatasetUrl(bad)).toBeNull();
    }
  });
});

describe('findDatasetRefs', () => {
  it('finds refs at every nesting level and dedupes', () => {
    const spec = {
      data: { url: url(ID_A) },
      layer: [
        { data: { url: url(ID_A) }, mark: 'bar' },
        { data: { url: url(ID_B) }, mark: 'line' },
      ],
      hconcat: [{ facet: { row: { field: 'r' } }, spec: { data: { url: url(ID_B) }, mark: 'point' } }],
      // decoys: not a data object, or not our scheme
      encoding: { x: { field: 'url' } },
      other: { data: { url: 'http://evil/x' } },
      arr: { data: [{ url: url(ID_A) }] },
    };
    expect(findDatasetRefs(spec).sort()).toEqual([ID_A, ID_B].sort());
    expect(hasDatasetRefs(spec)).toBe(true);
  });

  it('is empty for inline-data specs', () => {
    expect(findDatasetRefs({ data: { values: [{ a: 1 }] }, mark: 'bar' })).toEqual([]);
    expect(hasDatasetRefs({ data: { values: [] } })).toBe(false);
    expect(findDatasetRefs(null)).toEqual([]);
    expect(findDatasetRefs('ziya://dataset/' + ID_A)).toEqual([]);
  });
});

describe('resolveDatasetRefs', () => {
  it('inlines rows for each reference, fetching each id once', async () => {
    const calls: string[] = [];
    const fetcher = async (id: string) => {
      calls.push(id);
      return okResponse(id, [{ id, v: 1 }]);
    };
    const spec: any = {
      data: { url: url(ID_A), format: { type: 'json' } },
      layer: [
        { data: { url: url(ID_A) }, mark: 'bar' },
        { data: { name: 'src', url: url(ID_B) }, mark: 'line' },
      ],
    };
    const res = await resolveDatasetRefs(spec, fetcher);
    expect(calls.sort()).toEqual([ID_A, ID_B].sort());       // dedup
    expect(res.resolved.sort()).toEqual([ID_A, ID_B].sort());
    expect(res.failed).toEqual([]);
    expect(res.spec).toBe(spec);                               // mutates in place
    expect(spec.data).toEqual({ values: [{ id: ID_A, v: 1 }] }); // url + format gone
    expect(spec.layer[0].data).toEqual({ values: [{ id: ID_A, v: 1 }] });
    expect(spec.layer[1].data).toEqual({ name: 'src', values: [{ id: ID_B, v: 1 }] }); // name kept
    expect(hasDatasetRefs(spec)).toBe(false);
  });

  it('leaves a failed reference in place (for the SSRF stripper) and never throws', async () => {
    const fetcher = async (id: string) => {
      if (id === ID_B) throw new Error('HTTP 404');
      return okResponse(id, [{ x: 1 }]);
    };
    const spec: any = { layer: [{ data: { url: url(ID_A) } }, { data: { url: url(ID_B) } }] };
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const res = await resolveDatasetRefs(spec, fetcher);
    warn.mockRestore();
    expect(res.resolved).toEqual([ID_A]);
    expect(res.failed).toEqual([ID_B]);
    expect(spec.layer[0].data).toEqual({ values: [{ x: 1 }] });
    expect(spec.layer[1].data).toEqual({ url: url(ID_B) });
  });

  it('reports truncated datasets', async () => {
    const fetcher = async (id: string) => okResponse(id, [{ x: 1 }], true);
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const res = await resolveDatasetRefs({ data: { url: url(ID_A) } }, fetcher);
    warn.mockRestore();
    expect(res.truncated).toEqual([ID_A]);
    expect(res.resolved).toEqual([ID_A]);
  });

  it('does not call the fetcher when there is nothing to resolve', async () => {
    const fetcher = jest.fn();
    const spec = { data: { values: [{ a: 1 }] } };
    const res = await resolveDatasetRefs(spec, fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    expect(res.resolved).toEqual([]);
  });
});

describe('defaultDatasetFetcher', () => {
  const origFetch = (global as any).fetch;
  afterEach(() => { (global as any).fetch = origFetch; });

  it('hits only the same-origin /api/datasets/<id>/rows endpoint', async () => {
    const mock = jest.fn(async () => ({
      ok: true,
      json: async () => okResponse(ID_A, [{ a: 1 }]),
    }));
    (global as any).fetch = mock;
    const resp = await defaultDatasetFetcher(ID_A);
    expect(resp.rows).toEqual([{ a: 1 }]);
    expect(mock).toHaveBeenCalledTimes(1);
    const calledUrl = (mock.mock.calls[0] as any[])[0] as string;
    expect(calledUrl).toBe(`/api/datasets/${ID_A}/rows?limit=${DATASET_FETCH_LIMIT}`);
    expect(calledUrl.startsWith('/')).toBe(true); // relative → same-origin
  });

  it('throws on non-OK and on malformed bodies', async () => {
    (global as any).fetch = jest.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    await expect(defaultDatasetFetcher(ID_A)).rejects.toThrow(/404/);
    (global as any).fetch = jest.fn(async () => ({ ok: true, json: async () => ({ nope: true }) }));
    await expect(defaultDatasetFetcher(ID_A)).rejects.toThrow(/malformed/);
  });
});

// ---------------------------------------------------------------------------
// Seam: the plugin must resolve BEFORE the SSRF stripper, or the feature is
// silently dead. Source-inspection per this repo's convention for the large
// D3 plugins (see vegaLiteSsrf.test.ts).
// ---------------------------------------------------------------------------
describe('vegaLitePlugin dataset resolution seam', () => {
  const PLUGIN_SRC = fs.readFileSync(path.join(__dirname, '..', 'vegaLitePlugin.ts'), 'utf-8');

  it('imports and awaits the resolver', () => {
    expect(PLUGIN_SRC).toMatch(/from '\.\/datasetRefs'/);
    expect(PLUGIN_SRC).toMatch(/await resolveDatasetRefs\(parsedSpec\)/);
  });

  it('resolves after parse and before sanitizeSpec and the url stripper', () => {
    const resolveAt = PLUGIN_SRC.indexOf('await resolveDatasetRefs(parsedSpec)');
    const sanitizeAt = PLUGIN_SRC.indexOf('let fixedSpec = sanitizeSpec(parsedSpec)');
    const stripAt = PLUGIN_SRC.indexOf('stripExternalDataUrls(finalSpec)');
    expect(resolveAt).toBeGreaterThan(0);
    expect(sanitizeAt).toBeGreaterThan(resolveAt);
    expect(stripAt).toBeGreaterThan(resolveAt);
  });

  it('keeps the SSRF hardening intact (restricted loader + stripper still present)', () => {
    expect(PLUGIN_SRC).toMatch(/loader:\s*await getRestrictedVegaLoader\(\)/);
    expect(PLUGIN_SRC).toMatch(/stripExternalDataUrls\(finalSpec\)/);
  });
});

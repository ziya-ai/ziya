/**
 * ziya://dataset/<id> bindings for Plotly traces.
 *
 * Vega-Lite reads a registered dataset through `data: {url}` (datasetRefs.ts);
 * Plotly traces have no data field, so before this a map drawn from an
 * `analyze` result had its numbers copied into the spec by hand.  A trace may
 * now carry
 *
 *   "dataset": {
 *     "url": "ziya://dataset/ds_<16 hex>",
 *     "columns": {"lat": "lat", "marker.size": "traffic", "customdata": ["a", "b"]},
 *     "segments": {"from": ["a_lat", "a_lon"], "to": ["b_lat", "b_lon"]},
 *     "groupBy": "band",
 *     "groupStyle": {"> 100 ms": {"line": {"color": "#d62728"}}}
 *   }
 *
 * and is expanded into ordinary traces with inline arrays before Plotly sees
 * it.  Fetching goes through datasetRefs' same-origin fetcher, which only
 * ever requests /api/datasets/<validated id>/rows.
 */
import { DatasetFetcher, defaultDatasetFetcher, parseDatasetUrl } from './datasetRefs';

export interface PlotlyDatasetBinding {
  url: string;
  /** Trace attribute (dotted for nested) -> column; an array of columns builds per-point arrays. */
  columns?: Record<string, string | string[]>;
  /** One line per row between two [lat column, lon column] pairs, null-separated. */
  segments?: { from: [string, string]; to: [string, string] };
  /** One trace per distinct value of this column, in first-seen order. */
  groupBy?: string;
  /** Attributes merged into the trace of one group value. */
  groupStyle?: Record<string, Record<string, unknown>>;
}

export interface PlotlyDatasetResult {
  spec: any;
  resolved: string[];
  failed: string[];
  truncated: string[];
}

type Row = Record<string, unknown>;

const isPlainObject = (v: unknown): v is Record<string, any> =>
  !!v && typeof v === 'object' && !Array.isArray(v);

function deepMerge(base: any, extra: any): any {
  if (!isPlainObject(base) || !isPlainObject(extra)) return extra;
  const out: any = { ...base };
  for (const [k, v] of Object.entries(extra)) {
    out[k] = isPlainObject(v) && isPlainObject(out[k]) ? deepMerge(out[k], v) : v;
  }
  return out;
}

function setPath(obj: any, path: string, value: unknown): void {
  const keys = path.split('.');
  let node = obj;
  for (const k of keys.slice(0, -1)) {
    if (!isPlainObject(node[k])) node[k] = {};
    node = node[k];
  }
  node[keys[keys.length - 1]] = value;
}

const num = (v: unknown): number =>
  typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;

export function hasPlotlyDatasetRefs(spec: any): boolean {
  return Array.isArray(spec?.data) && spec.data.some((t: any) => isPlainObject(t) && isPlainObject(t.dataset));
}

function requireColumn(name: unknown, available: Set<string>, where: string): void {
  if (typeof name !== 'string' || !available.has(name)) {
    throw new Error(
      `dataset ${where}: no column ${JSON.stringify(name)}; columns are ${Array.from(available).join(', ')}`,
    );
  }
}

/** Expand one dataset-bound trace into plain traces (one per group). */
export function expandDatasetTrace(trace: any, rows: Row[], id = 'rows'): any[] {
  const { dataset: binding, ...base } = trace;
  const cols = Object.entries(isPlainObject(binding.columns) ? binding.columns : {}) as [string, string | string[]][];
  const segs = binding.segments;
  if (segs !== undefined && !(Array.isArray(segs?.from) && Array.isArray(segs?.to)
      && segs.from.length === 2 && segs.to.length === 2)) {
    throw new Error(`dataset ${id}: "segments" needs from: [latColumn, lonColumn] and to: [latColumn, lonColumn]`);
  }
  const available = new Set<string>();
  rows.forEach((r) => Object.keys(r).forEach((k) => available.add(k)));
  if (rows.length > 0) {
    for (const [attr, c] of cols) (Array.isArray(c) ? c : [c]).forEach((n) => requireColumn(n, available, `${id} (${attr})`));
    if (binding.groupBy !== undefined) requireColumn(binding.groupBy, available, `${id} (groupBy)`);
    if (segs) [...segs.from, ...segs.to].forEach((n: string) => requireColumn(n, available, `${id} (segments)`));
  }

  const groups = new Map<string, Row[]>();
  if (binding.groupBy !== undefined) {
    for (const r of rows) {
      const key = String(r[binding.groupBy] ?? '');
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(r);
    }
  } else {
    groups.set('', rows);
  }

  const value = (r: Row, c: string | string[]) => (Array.isArray(c) ? c.map((n) => r[n]) : r[c]);
  const out: any[] = [];
  groups.forEach((groupRows, key) => {
    let t: any = JSON.parse(JSON.stringify(base));
    if (binding.groupBy !== undefined) {
      t.name = typeof base.name === 'string' && base.name.includes('{group}')
        ? base.name.split('{group}').join(key) : key;
      const style = isPlainObject(binding.groupStyle) ? binding.groupStyle[key] : undefined;
      if (isPlainObject(style)) t = deepMerge(t, style);
    }
    if (segs) {
      const lat: (number | null)[] = [];
      const lon: (number | null)[] = [];
      const series: unknown[][] = cols.map(() => []);
      for (const r of groupRows) {
        const p = [num(r[segs.from[0]]), num(r[segs.from[1]]), num(r[segs.to[0]]), num(r[segs.to[1]])];
        if (!p.every(Number.isFinite)) continue;
        lat.push(p[0], p[2], null);
        lon.push(p[1], p[3], null);
        cols.forEach(([, c], i) => { const v = value(r, c); series[i].push(v, v, null); });
      }
      t.lat = lat;
      t.lon = lon;
      cols.forEach(([attr], i) => setPath(t, attr, series[i]));
    } else {
      cols.forEach(([attr, c]) => setPath(t, attr, groupRows.map((r) => value(r, c))));
    }
    out.push(t);
  });
  return out;
}

/**
 * Fetch every dataset a trace binds to and expand those traces in place.
 * A malformed url throws (the error card names it); a failed fetch is
 * reported in `failed` and leaves the spec unchanged.
 */
export async function resolvePlotlyDatasets(
  spec: any,
  fetcher: DatasetFetcher = defaultDatasetFetcher,
): Promise<PlotlyDatasetResult> {
  const result: PlotlyDatasetResult = { spec, resolved: [], failed: [], truncated: [] };
  if (!hasPlotlyDatasetRefs(spec)) return result;
  const ids = new Set<string>();
  for (const t of spec.data) {
    if (!(isPlainObject(t) && isPlainObject(t.dataset))) continue;
    const id = parseDatasetUrl(t.dataset.url);
    if (!id) {
      throw new Error(`trace "${t.name ?? t.type}": dataset.url must be ziya://dataset/ds_<16 hex digits>, got ${JSON.stringify(t.dataset.url)}`);
    }
    ids.add(id);
  }
  const rowsById = new Map<string, Row[]>();
  await Promise.all(Array.from(ids).map(async (id) => {
    try {
      const resp = await fetcher(id);
      rowsById.set(id, resp.rows);
      result.resolved.push(id);
      if (resp.truncated) result.truncated.push(id);
    } catch (e) {
      result.failed.push(id);
      console.warn(`Plotly: could not resolve ${id}:`, e);
    }
  }));
  if (result.failed.length > 0) return result;
  const data = spec.data.flatMap((t: any) => {
    if (!(isPlainObject(t) && isPlainObject(t.dataset))) return [t];
    const id = parseDatasetUrl(t.dataset.url)!;
    return expandDatasetTrace(t, rowsById.get(id) || [], id);
  });
  result.spec = { ...spec, data };
  return result;
}

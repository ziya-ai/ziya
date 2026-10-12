/**
 * Dataset-by-reference resolution for Vega-Lite specs.
 *
 * The `dataset_register` builtin tool hands the model a handle of the form
 * `ziya://dataset/<id>`. A spec may carry it as `data: { url: "ziya://…" }`
 * at any nesting level (top-level, layer[], hconcat/vconcat/concat[],
 * facet/repeat `spec`). This module swaps each such reference for inline
 * `data: { values: [...] }` fetched from `/api/datasets/<id>/rows`.
 *
 * Security posture (PenPal #83, CWE-918): the Vega-Lite plugin still runs
 * a restricted loader that refuses every URL and strips any remaining
 * `data.url` before embedding. This resolver runs BEFORE those steps and
 * only ever fetches the strict `ziya://dataset/<id>` scheme, only from the
 * same-origin `/api/datasets/` endpoint, which serves only registered
 * datasets by validated id. Anything that does not match the scheme, or
 * fails to resolve, is left in place for the existing stripper to remove.
 */

export const DATASET_URL_RE = /^ziya:\/\/dataset\/(ds_[0-9a-f]{16})$/;

/** Rows requested per reference; server hard-caps at 100k. */
export const DATASET_FETCH_LIMIT = 50000;

export interface DatasetRowsResponse {
  id: string;
  row_count: number;
  returned: number;
  truncated: boolean;
  rows: Record<string, unknown>[];
}

export type DatasetFetcher = (id: string) => Promise<DatasetRowsResponse>;

export interface ResolveResult<T> {
  spec: T;
  /** ids successfully inlined */
  resolved: string[];
  /** ids whose fetch failed (url left in place for the stripper) */
  failed: string[];
  /** ids whose row set was cut at the server limit */
  truncated: string[];
}

export function parseDatasetUrl(url: unknown): string | null {
  if (typeof url !== 'string') return null;
  const m = DATASET_URL_RE.exec(url);
  return m ? m[1] : null;
}

/** Collect every dataset id referenced by a `data.url` anywhere in the spec. */
export function findDatasetRefs(spec: unknown): string[] {
  const ids = new Set<string>();
  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.data && typeof node.data === 'object' && !Array.isArray(node.data)) {
      const id = parseDatasetUrl(node.data.url);
      if (id) ids.add(id);
    }
    for (const key of Object.keys(node)) walk(node[key]);
  };
  walk(spec);
  return Array.from(ids);
}

export const defaultDatasetFetcher: DatasetFetcher = async (id: string) => {
  // Same-origin only; the id has already been validated by DATASET_URL_RE.
  const url = `/api/datasets/${encodeURIComponent(id)}/rows?limit=${DATASET_FETCH_LIMIT}`;
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (!resp.ok) {
    throw new Error(`dataset ${id}: HTTP ${resp.status}`);
  }
  const body = await resp.json();
  if (!body || !Array.isArray(body.rows)) {
    throw new Error(`dataset ${id}: malformed rows response`);
  }
  return body as DatasetRowsResponse;
};

/**
 * Replace every `data: { url: "ziya://dataset/<id>" }` with inline values.
 * Mutates and returns `spec`. Fetches are deduplicated per id. Never throws:
 * a failed fetch leaves that reference untouched and records it in `failed`.
 */
export async function resolveDatasetRefs<T>(
  spec: T,
  fetcher: DatasetFetcher = defaultDatasetFetcher,
): Promise<ResolveResult<T>> {
  const ids = findDatasetRefs(spec);
  const result: ResolveResult<T> = { spec, resolved: [], failed: [], truncated: [] };
  if (ids.length === 0) return result;

  const rowsById = new Map<string, Record<string, unknown>[]>();
  await Promise.all(ids.map(async (id) => {
    try {
      const resp = await fetcher(id);
      rowsById.set(id, resp.rows);
      result.resolved.push(id);
      if (resp.truncated) {
        result.truncated.push(id);
        console.warn(
          `Vega-Lite: dataset ${id} truncated to ${resp.returned} of ${resp.row_count} rows; ` +
          'pre-aggregate or filter on the server for full fidelity.',
        );
      }
    } catch (e) {
      result.failed.push(id);
      console.warn(`Vega-Lite: could not resolve ${id}:`, e);
    }
  }));

  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.data && typeof node.data === 'object' && !Array.isArray(node.data)) {
      const id = parseDatasetUrl(node.data.url);
      if (id && rowsById.has(id)) {
        // Preserve a `name` (used by named-data params); drop `url`/`format`,
        // which only make sense for a remote load.
        const { name } = node.data;
        node.data = { ...(name ? { name } : {}), values: rowsById.get(id) };
      }
    }
    for (const key of Object.keys(node)) walk(node[key]);
  };
  walk(spec);
  return result;
}

/** True when the spec references at least one dataset handle. */
export function hasDatasetRefs(spec: unknown): boolean {
  return findDatasetRefs(spec).length > 0;
}

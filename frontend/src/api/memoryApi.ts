/**
 * Memory API client — typed wrappers around /api/v1/memory endpoints.
 */

export interface MemoryItem {
  id: string;
  content: string;
  layer: string;
  tags: string[];
  learned_from: string;
  created: string;
  last_accessed: string;
  status: string;
  importance: number;
  scope?: { domain_node?: string | null; project_paths?: string[] };
  relations?: Record<string, string[]>;
  retrieval_loaded_count?: number;
  retrieval_used_count?: number;
}

export interface MemoryProposal {
  id: string;
  content: string;
  layer: string;
  tags: string[];
  learned_from: string;
  proposed_at: number;
  // Probation progress.  Optional so a stale cached response still parses.
  corroborations?: number;
  signals?: string[];
  // Age and expiry are in activity ticks, not wall-clock: a vacation must
  // not silently expire anything.
  age?: number;
  expires_in?: number;
  would_promote?: string | null;
}

export interface MindMapNode {
  id: string;
  handle: string;
  parent: string | null;
  children: string[];
  cross_links: string[];
  memory_refs: string[];
  tags: string[];
  access_count: number;
  last_accessed: string;
}

export interface MemoryStatus {
  total: number;
  by_layer: Record<string, number>;
  by_status: Record<string, number>;
  pending_proposals: number;
}

export interface ReviewSummary {
  stale: MemoryItem[];
  oversized_nodes: Array<{ node_id: string; memory_count: number }>;
  orphans: MemoryItem[];
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  if (path) h['X-Project-Root'] = path;
  return h;
}

export async function getMemoryStatus(): Promise<MemoryStatus> {
  const res = await fetch('/api/v1/memory', { headers: headers() });
  if (!res.ok) throw new Error(`Memory status failed: ${res.status}`);
  return res.json();
}

export async function getAllMemories(): Promise<MemoryItem[]> {
  const res = await fetch('/api/v1/memory/all', { headers: headers() });
  if (!res.ok) throw new Error(`List memories failed: ${res.status}`);
  return res.json();
}

export async function searchMemories(query: string, limit = 20): Promise<MemoryItem[]> {
  const res = await fetch(`/api/v1/memory/search?q=${encodeURIComponent(query)}&limit=${limit}`, { headers: headers() });
  if (!res.ok) throw new Error(`Search failed: ${res.status}`);
  return res.json();
}

export async function saveMemory(content: string, layer: string, tags: string[]): Promise<MemoryItem> {
  const res = await fetch('/api/v1/memory', {
    method: 'POST', headers: headers(),
    body: JSON.stringify({ content, layer, tags }),
  });
  if (!res.ok) throw new Error(`Save failed: ${res.status}`);
  return res.json();
}

export async function updateMemoryScope(id: string, projectPaths: string[]): Promise<MemoryItem> {
  const res = await fetch(`/api/v1/memory/${id}`, {
    method: 'PUT', headers: headers(),
    body: JSON.stringify({ scope: { project_paths: projectPaths } }),
  });
  if (!res.ok) throw new Error(`Scope update failed: ${res.status}`);
  return res.json();
}

export async function updateMemory(id: string, updates: Partial<Pick<MemoryItem, 'content' | 'layer' | 'tags' | 'status'>>): Promise<MemoryItem> {
  const res = await fetch(`/api/v1/memory/${id}`, {
    method: 'PUT', headers: headers(),
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Update failed: ${res.status}`);
  return res.json();
}

export async function deleteMemory(id: string): Promise<void> {
  const res = await fetch(`/api/v1/memory/${id}`, { method: 'DELETE', headers: headers() });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

export async function getProposals(): Promise<MemoryProposal[]> {
  const res = await fetch('/api/v1/memory/proposals', { headers: headers() });
  if (!res.ok) throw new Error(`Proposals failed: ${res.status}`);
  return res.json();
}

export async function approveProposal(id: string): Promise<MemoryItem> {
  const res = await fetch(`/api/v1/memory/proposals/${id}/approve`, { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Approve failed: ${res.status}`);
  return res.json();
}

export async function approveAllProposals(): Promise<{ approved: number }> {
  const res = await fetch('/api/v1/memory/proposals/approve-all', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Approve all failed: ${res.status}`);
  return res.json();
}

export async function dismissProposal(id: string): Promise<void> {
  const res = await fetch(`/api/v1/memory/proposals/${id}`, { method: 'DELETE', headers: headers() });
  if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`);
}

export async function getMindMap(): Promise<MindMapNode[]> {
  const res = await fetch('/api/v1/memory/mindmap', { headers: headers() });
  if (!res.ok) throw new Error(`Mind map failed: ${res.status}`);
  return res.json();
}

export async function expandMindMapNode(nodeId: string): Promise<{ node: MindMapNode; memories: MemoryItem[]; count: number }> {
  const res = await fetch(`/api/v1/memory/mindmap/${nodeId}/expand`, { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Expand failed: ${res.status}`);
  return res.json();
}

export async function getReview(): Promise<ReviewSummary> {
  const res = await fetch('/api/v1/memory/review', { headers: headers() });
  if (!res.ok) throw new Error(`Review failed: ${res.status}`);
  return normalizeReview(await res.json());
}

export async function runMaintenance(): Promise<{ divided: string[]; cross_linked: string[] }> {
  const res = await fetch('/api/v1/memory/maintenance', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Maintenance failed: ${res.status}`);
  return res.json();
}

export async function startOrganize(): Promise<{ status: string }> {
  const res = await fetch('/api/v1/memory/organize', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Organize failed: ${res.status}`);
  return res.json();
}

export async function getOrganizeStatus(): Promise<{
  running: boolean;
  result: OrganizeResult | null;
  error: string | null;
  started_at: number | null;
}> {
  const res = await fetch('/api/v1/memory/organize/status', { headers: headers() });
  if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
  return res.json();
}

export interface OrganizeResult {
  cleanup: { status: string; removed?: number; merged?: number; reviewed?: number };
  bootstrap: { status: string; domains_created?: number; domains_updated?: number; memories_placed?: number };
  relations: { status: string; relations_found?: number };
  cross_links: string[];
  divisions: string[];
  rem?: {
    nodes_mature?: number;
    syntheses_created?: number;
    memories_contested?: number;
    syntheses?: Array<{ node_id: string; proposal_id: string }>;
    contested?: Array<{ node_id: string; memory_id: string }>;
  };
}

export interface OrganizeHistoryRecord {
  timestamp: number;
  cleanup: { removed: number; merged: number; reviewed: number };
  bootstrap: { domains_created: number; domains_updated: number; memories_placed: number };
  relations_found: number;
  cross_links_added: number;
  divisions: number;
  rem: {
    nodes_mature: number;
    syntheses_created: number;
    memories_contested: number;
    syntheses: Array<{ node_id: string; proposal_id: string }>;
    contested: Array<{ node_id: string; memory_id: string }>;
  };
}

export async function getOrganizeHistory(): Promise<OrganizeHistoryRecord[]> {
  const res = await fetch('/api/v1/memory/organize/history', { headers: headers() });
  if (!res.ok) throw new Error(`History fetch failed: ${res.status}`);
  return res.json();
}

export async function runOrganize(): Promise<OrganizeResult> {
  await startOrganize();
  // Poll for completion
  const POLL_INTERVAL = 2000;
  const MAX_WAIT = 120000; // 2 minutes
  const start = Date.now();
  while (Date.now() - start < MAX_WAIT) {
    await new Promise(r => setTimeout(r, POLL_INTERVAL));
    const status = await getOrganizeStatus();
    if (!status.running) {
      if (status.error) throw new Error(status.error);
      if (status.result) return status.result;
      throw new Error('Organize completed with no result');
    }
  }
  throw new Error('Organize timed out after 2 minutes');
}

export const LAYER_COLORS: Record<string, string> = {
  domain_context: '#3b82f6',
  architecture: '#8b5cf6',
  lexicon: '#06b6d4',
  decision: '#f59e0b',
  active_thread: '#10b981',
  process: '#6366f1',
  preference: '#ec4899',
  negative_constraint: '#ef4444',
};

export const LAYER_LABELS: Record<string, string> = {
  domain_context: 'Domain',
  architecture: 'Architecture',
  lexicon: 'Vocabulary',
  decision: 'Decisions',
  active_thread: 'Active Work',
  process: 'Process',
  preference: 'Preferences',
  negative_constraint: 'Lessons (avoid)',
};

export const LAYER_ICONS: Record<string, string> = {
  domain_context: '🌐',
  architecture: '🏗️',
  lexicon: '📖',
  decision: '⚖️',
  active_thread: '🔄',
  process: '📋',
  preference: '💜',
  negative_constraint: '🚫',
};

// ── Pure helpers for the Health-tab organize status line ──────────────
// Extracted from MemoryBrowser's inline IIFE so they are unit-testable
// without dragging the component (and its render chain) into jest.

/** Threshold at which orphan accumulation auto-triggers organize.
 *  Mirrors AUTO_ORGANIZE_ORPHAN_THRESHOLD in app/memory/organizer.py. */
export const AUTO_ORGANIZE_ORPHAN_THRESHOLD = 15;

/** Relative-time label for a past epoch-ms timestamp ("5m ago", "3h ago",
 *  "2d ago"), or "never" when no timestamp is given.  now is injectable
 *  for deterministic tests. */
export function formatLastRunLabel(timestampMs: number | null | undefined, now: number = Date.now()): string {
  if (!timestampMs) return 'never';
  const mins = Math.floor((now - timestampMs) / 60000);
  if (mins < 0) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)}d ago`;
}

export interface OrphanStatus {
  count: number;
  atThreshold: boolean;   // count >= threshold → auto-organize will fire
  label: string;
}

/** Build the "Currently N orphans — auto-organize triggers at T" status. */
export function orphanStatus(
  orphanCount: number | null | undefined,
  threshold: number = AUTO_ORGANIZE_ORPHAN_THRESHOLD,
): OrphanStatus {
  const count = Math.max(0, orphanCount || 0);
  return {
    count,
    atThreshold: count >= threshold,
    label: `Currently ${count} orphan${count === 1 ? '' : 's'} — auto-organize triggers at ${threshold}.`,
  };
}

/** Raw embedding-status payload from GET /api/v1/memory/embeddings/status. */
export interface EmbeddingStatusPayload {
  enabled?: boolean;
  provider?: string;
  total?: number;
  embedded?: number;
  missing?: number;
}

/** Derived embedding-coverage view backing the Health-tab coverage line. */
export interface EmbeddingCoverage {
  enabled: boolean;    // embedding provider active (not Noop)
  degraded: boolean;   // active + some memories lack vectors → cross-linking partial
  pct: number;         // 0-100, round-half-up; 100 when total is 0 (vacuously complete)
  total: number;
  embedded: number;
  missing: number;
  label: string;
}

/**
 * Derive the embedding-coverage view for the Memory Browser Health tab.
 *
 * Embedding-centroid cross-linking can only consider memories that actually
 * carry vectors, so a partially-backfilled embeddings.npz silently yields
 * fewer cross-links than expected.  This surfaces that as a degraded state
 * with a backfill hint.  Pure + exported for unit testing without rendering
 * MemoryBrowser (and its marked/ESM render chain).
 *
 * Cases:
 *   - null/undefined or enabled:false → disabled (Noop provider); cross-linking
 *     falls back to shared-tag overlap, never degraded.
 *   - missing omitted → derived as total - embedded.
 *   - total === 0 → 100% (vacuously complete), not degraded.
 *   - pct is round-half-up (Math.round on positives).
 */
export function embeddingCoverage(
  status: EmbeddingStatusPayload | null | undefined,
): EmbeddingCoverage {
  // Disabled: no payload at all, or provider explicitly off (Noop).
  if (!status || status.enabled === false) {
    return {
      enabled: false,
      degraded: false,
      pct: 0,
      total: 0,
      embedded: 0,
      missing: 0,
      // Single label satisfies both disabled assertions (/disabled/i and
      // /shared-tag overlap/i) and states the real fallback behavior.
      label: 'Embeddings disabled — cross-linking uses shared-tag overlap.',
    };
  }

  const total = Math.max(0, status.total || 0);
  const embedded = Math.max(0, status.embedded || 0);
  const missing = status.missing != null ? Math.max(0, status.missing) : Math.max(0, total - embedded);
  const pct = total === 0 ? 100 : Math.round((embedded / total) * 100);
  const degraded = total > 0 && embedded < total;

  return {
    enabled: true,
    degraded,
    pct,
    total,
    embedded,
    missing,
    label: degraded
      ? `Embeddings: partial coverage (${embedded}/${total}, ${pct}%) — Backfill to enable full cross-linking.`
      : `Embeddings: full coverage (${embedded}/${total}).`,
  };
}

/**
 * Raw payload of GET /api/v1/memory/review (app/memory/maintenance.py
 * get_review_summary).  The backend names the lists ``stale_memories`` /
 * ``orphan_memories`` / ``large_nodes``; the Health tab reads ``stale`` /
 * ``orphans`` / ``oversized_nodes``.  Nothing renamed them, so the tab always
 * showed "all healthy" and the orphan-threshold line always read 0.  The two
 * shapes meet here, at the one fetch site; the component keeps its names.
 */
export interface ReviewPayload {
  stale_memories?: MemoryItem[];
  orphan_memories?: MemoryItem[];
  large_nodes?: Array<{ id: string; handle?: string; memory_count: number }>;
  // Already-normalised shape is accepted too.
  stale?: MemoryItem[];
  orphans?: MemoryItem[];
  oversized_nodes?: Array<{ node_id: string; memory_count: number }>;
}

export function normalizeReview(raw: ReviewPayload | null | undefined): ReviewSummary {
  const r = raw || {};
  return {
    stale: r.stale ?? r.stale_memories ?? [],
    orphans: r.orphans ?? r.orphan_memories ?? [],
    oversized_nodes: r.oversized_nodes
      ?? (r.large_nodes || []).map(n => ({ node_id: n.id, memory_count: n.memory_count })),
  };
}

/**
 * The review endpoint returns slices (id, truncated content, tags — orphans
 * carry no layer/importance/last_accessed) but MemoryCard renders those
 * fields.  Swap in the full record when the browser already holds it.
 */
export function hydrateReview(review: ReviewSummary, known: MemoryItem[]): ReviewSummary {
  const byId = new Map(known.map(m => [m.id, m]));
  const full = (m: MemoryItem) => byId.get(m.id) ?? m;
  return { ...review, stale: review.stale.map(full), orphans: review.orphans.map(full) };
}

/** Record written by app/utils/memory_pause.py when a memory pass hit an outage. */
export interface MemoryPauseRecord {
  paused_at: number | null;
  last_seen_at?: number;
  repeat_count?: number;
  kind?: string;
  error?: string;
  where?: string[];
}

export async function getMemoryPause(): Promise<{ paused: boolean; record: MemoryPauseRecord | null }> {
  const res = await fetch('/api/v1/memory/pause', { headers: headers() });
  if (!res.ok) throw new Error(`Pause status failed: ${res.status}`);
  return res.json();
}

/** Human-in-the-loop continuation: clear the pause so the next turn extracts. */
export async function resumeMemory(): Promise<{ resumed: boolean; cleared: MemoryPauseRecord | null }> {
  const res = await fetch('/api/v1/memory/resume', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Resume failed: ${res.status}`);
  return res.json();
}

/** One-line banner text for a pause record; null when not paused. */
export function memoryPauseLabel(
  rec: MemoryPauseRecord | null | undefined,
  now: number = Date.now(),
): string | null {
  if (!rec) return null;
  const since = rec.paused_at ? formatLastRunLabel(rec.paused_at, now) : 'at an unknown time';
  const where = (rec.where || []).filter(Boolean).join(', ') || 'a memory pass';
  const n = rec.repeat_count || 0;
  const repeats = n > 0 ? ` (${n} further failure${n === 1 ? '' : 's'} since)` : '';
  return (
    `Memory paused ${since} — ${rec.kind || 'infrastructure failure'} in ${where}${repeats}. ` +
    'It retries on its own with backoff; Resume skips the wait.'
  );
}

// ── Export ────────────────────────────────────────────────────────────
// Mirrors app/utils/memory_export.py.  The export is the one sanctioned
// path for memory to leave the machine, so the client echoes the preview
// fingerprint and the server refuses (409) if the store has moved on.

export interface ExportSelection {
  domains: string[];
  include_projects: string[];
  exclude_projects: string[];
  include_unscoped: boolean;
  layers: string[];
  statuses: string[];
  strip_provenance: boolean;
  anonymize_paths: boolean;
  include_mindmap: boolean;
}

export const DEFAULT_EXPORT_SELECTION: ExportSelection = {
  domains: [],
  include_projects: [],
  exclude_projects: [],
  include_unscoped: true,
  layers: [],
  statuses: ['active'],
  strip_provenance: true,
  anonymize_paths: true,
  include_mindmap: true,
};

export interface ExportDomainOption {
  id: string;
  handle: string;
  count: number;
  children: ExportDomainOption[];
}

export interface ExportOptions {
  total: number;
  domains: ExportDomainOption[];
  unplaced: number;
  projects: Array<{ path: string; label: string; count: number }>;
  unscoped: number;
  layers: Array<{ layer: string; count: number }>;
  statuses: Array<{ status: string; count: number }>;
  /** Folder the save dialog starts in (~/Downloads, else home). */
  default_save_dir?: string;
}

export interface ExportWarning {
  memory_id: string | null;
  kind: string;
  match: string;
  snippet: string;
}

export interface ExportPreview {
  memory_ids: string[];
  fingerprint: string;
  total: number;
  held_back: Record<string, number>;
  counts: {
    by_layer: Record<string, number>;
    by_domain: Array<{ id: string; handle: string; count: number }>;
    by_project: Array<{ path: string; label: string; count: number }>;
    nodes: number;
  };
  warnings: ExportWarning[];
  node_ids: string[];
  sample: MemoryItem[];
  /** Server-chosen filename for this selection, used to prefill the save path. */
  suggested_filename?: string;
}

export async function getExportOptions(): Promise<ExportOptions> {
  const res = await fetch('/api/v1/memory/export/options', { headers: headers() });
  if (!res.ok) throw new Error(`Export options failed: ${res.status}`);
  return res.json();
}

export async function previewExport(selection: ExportSelection): Promise<ExportPreview> {
  const res = await fetch('/api/v1/memory/export/preview', {
    method: 'POST', headers: headers(), body: JSON.stringify(selection),
  });
  if (!res.ok) throw new Error(`Export preview failed: ${res.status}`);
  return res.json();
}

/** Run the export.  Resolves to the bundle blob and the server's filename;
 *  throws with the server's detail on 409 (store changed) / 412 (warnings). */
export async function runExport(
  selection: ExportSelection, fingerprint: string, acknowledgeWarnings: boolean,
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch('/api/v1/memory/export', {
    method: 'POST', headers: headers(),
    body: JSON.stringify({ selection, fingerprint, acknowledge_warnings: acknowledgeWarnings }),
  });
  if (!res.ok) {
    let detail = `Export failed: ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* keep status text */ }
    throw new Error(detail);
  }
  const cd = res.headers.get('content-disposition') || '';
  const m = /filename="([^"]+)"/.exec(cd);
  return { blob: await res.blob(), filename: m ? m[1] : 'ziya-memory-export.json' };
}

/** Error from saveExport.  code === 'file_exists' means the target is
 *  taken and the caller may retry with overwrite; any other 409 means the
 *  store moved since the preview. */
export class ExportSaveError extends Error {
  constructor(message: string, public status: number,
              public code: string | null = null, public path: string | null = null) {
    super(message);
  }
}

/** Have the server write the bundle to savePath (a folder or a file
 *  path).  Resolves to the absolute path it was written to: unlike a
 *  browser download, this can tell the user where the file is. */
export async function saveExport(
  selection: ExportSelection, fingerprint: string, acknowledgeWarnings: boolean,
  savePath: string, overwrite: boolean = false,
): Promise<{ path: string; filename: string; total: number; fingerprint: string }> {
  const res = await fetch('/api/v1/memory/export', {
    method: 'POST', headers: headers(),
    body: JSON.stringify({
      selection, fingerprint, acknowledge_warnings: acknowledgeWarnings,
      save_path: savePath, overwrite,
    }),
  });
  if (!res.ok) {
    let detail: any = null;
    try { detail = (await res.json()).detail; } catch { /* keep status text */ }
    if (detail && typeof detail === 'object') {
      throw new ExportSaveError(detail.message || `Export failed: ${res.status}`,
        res.status, detail.code || null, detail.path || null);
    }
    throw new ExportSaveError(detail || `Export failed: ${res.status}`, res.status);
  }
  return res.json();
}

/** Prefill for the save dialog: folder + filename joined with one
 *  separator (a backslash when the folder is Windows-style). */
export function defaultSaveTarget(dir?: string | null, filename?: string | null): string {
  if (!dir) return filename || '';
  if (!filename) return dir;
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/';
  return `${dir.replace(/[\\/]+$/, '')}${sep}${filename}`;
}

export interface ExportReadiness {
  ready: boolean;
  reason: string | null;
}

/**
 * Whether the Export button may be enabled.  Pure, so the gate is testable:
 * no preview, an empty selection, or unacknowledged warnings all block.
 * The server enforces the same rules (409/412); this just explains them
 * before the click.
 */
export function exportReadiness(
  preview: ExportPreview | null | undefined,
  acknowledged: boolean,
  stale: boolean = false,
): ExportReadiness {
  if (!preview) return { ready: false, reason: 'Run the preview first.' };
  if (stale) return { ready: false, reason: 'Selection changed — preview is refreshing.' };
  if (preview.total === 0) return { ready: false, reason: 'Nothing selected.' };
  if (preview.warnings.length > 0 && !acknowledged) {
    const n = preview.warnings.length;
    return { ready: false, reason: `Acknowledge ${n} warning${n === 1 ? '' : 's'} to export.` };
  }
  return { ready: true, reason: null };
}

// ── Import ────────────────────────────────────────────────────────────
// Mirrors app/utils/memory_import.py.  Preview classifies every bundle
// memory against this store; import echoes both fingerprints and the
// server refuses (409) if either moved.

export interface ImportOptions {
  on_collision: 'rename' | 'skip' | 'overwrite';
  skip_duplicate_content: boolean;
  include_mindmap: boolean;
  source_label: string | null;
}

export const DEFAULT_IMPORT_OPTIONS: ImportOptions = {
  on_collision: 'rename',
  skip_duplicate_content: true,
  include_mindmap: true,
  source_label: null,
};

export type ImportDisposition =
  | 'new' | 'identical' | 'duplicate_content'
  | 'collision_renamed' | 'collision_skipped' | 'collision_overwritten';

export interface ImportPreview {
  bundle_fingerprint: string;
  store_fingerprint: string;
  total: number;
  exported_by: string | null;
  exported_at: string | null;
  counts: Record<string, number>;   // new, identical, duplicate_content, collision_*, nodes_new, nodes_merged, to_write
  nodes_new: string[];
  nodes_merged: string[];
  warnings: string[];
  sample: Array<{ id: string; final_id: string | null; disposition: ImportDisposition; layer: string | null; content: string }>;
}

export interface ImportResult {
  imported: number;
  counts: Record<string, number>;
  nodes_new: string[];
  nodes_merged: string[];
  embeddings_pending: number;
  bundle_fingerprint: string;
}

/** Parse an uploaded file as a bundle, with a readable error. */
export async function readBundleFile(file: File): Promise<unknown> {
  const text = await file.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${file.name} is not valid JSON`);
  }
}

async function detailOr(res: Response, fallback: string): Promise<string> {
  try { return (await res.json()).detail || fallback; } catch { return fallback; }
}

export async function previewImport(bundle: unknown, options: ImportOptions): Promise<ImportPreview> {
  const res = await fetch('/api/v1/memory/import/preview', {
    method: 'POST', headers: headers(), body: JSON.stringify({ bundle, options }),
  });
  if (!res.ok) throw new Error(await detailOr(res, `Import preview failed: ${res.status}`));
  return res.json();
}

export async function runImport(
  bundle: unknown, options: ImportOptions, preview: ImportPreview, source: string | null,
): Promise<ImportResult> {
  const res = await fetch('/api/v1/memory/import', {
    method: 'POST', headers: headers(),
    body: JSON.stringify({
      bundle, options, source,
      bundle_fingerprint: preview.bundle_fingerprint,
      store_fingerprint: preview.store_fingerprint,
    }),
  });
  if (!res.ok) throw new Error(await detailOr(res, `Import failed: ${res.status}`));
  return res.json();
}

export async function backfillEmbeddings(): Promise<{ status: string; embedded?: number; missing?: number }> {
  const res = await fetch('/api/v1/memory/embeddings/backfill', { method: 'POST', headers: headers() });
  if (!res.ok) throw new Error(`Backfill failed: ${res.status}`);
  return res.json();
}

/**
 * Whether the Import button may be enabled.  Pure, like exportReadiness.
 * Nothing to write is a block, not an error: the user should see that the
 * bundle is already present rather than get a no-op click.
 */
export function importReadiness(
  preview: ImportPreview | null | undefined,
  stale: boolean = false,
): ExportReadiness {
  if (!preview) return { ready: false, reason: 'Choose a bundle file.' };
  if (stale) return { ready: false, reason: 'Options changed — preview is refreshing.' };
  const n = preview.counts.to_write || 0;
  if (n === 0) {
    const ident = preview.counts.identical || 0;
    const dup = preview.counts.duplicate_content || 0;
    if (ident + dup === preview.total && preview.total > 0) {
      return { ready: false, reason: 'Everything in this bundle is already in your memory.' };
    }
    return { ready: false, reason: 'Nothing to import with these options.' };
  }
  return { ready: true, reason: null };
}

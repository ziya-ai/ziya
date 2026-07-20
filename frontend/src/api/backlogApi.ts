/**
 * Backlog API client
 */
import type { BeadItem } from './beadApi';
export interface SeamSnippet { role: string; text: string; }
export interface BacklogOrigin { conversation_id: string; bead_id: string; }
export interface BacklogItem {
  bead: BeadItem;
  conversation_id: string;
  conversation_title: string;
  folder_id: string | null;
  breadcrumb: string[];
  descendant_parked_count: number;
  seam_snippet: SeamSnippet | null;
  age_ms: number;
  can_branch: boolean;
  origin: BacklogOrigin | null;
}
export interface BacklogResponse {
  items: BacklogItem[];
  counts: { parked: number; abandoned: number };
  scanned_chats: number;
}
export type BacklogStatus = 'parked' | 'abandoned';
function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  if (path) h['X-Project-Root'] = path;
  return h;
}
function getProjectId(): string {
  return (window as any).__ZIYA_CURRENT_PROJECT_ID__ || 'default';
}
const EMPTY: BacklogResponse = { items: [], counts: { parked: 0, abandoned: 0 }, scanned_chats: 0 };
export async function getBacklog(projectId: string, opts?: { status?: string }): Promise<BacklogResponse> {
  const pid = projectId || getProjectId();
  const status = opts?.status ?? 'parked';
  const res = await fetch(`/api/v1/projects/${pid}/backlog?status=${encodeURIComponent(status)}`, { headers: headers() });
  if (!res.ok) {
    if (res.status === 404) return { ...EMPTY };
    throw new Error(`Get backlog failed: ${res.status}`);
  }
  return res.json();
}
export async function setBeadStatus(projectId: string, chatId: string, beadId: string, status: BacklogStatus): Promise<{ ok: boolean; bead: BeadItem }> {
  const pid = projectId || getProjectId();
  const res = await fetch(`/api/v1/projects/${pid}/chats/${chatId}/beads/${beadId}/status`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(`Set bead status failed: ${res.status}`);
  return res.json();
}

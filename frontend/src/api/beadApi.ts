/**
 * Bead API client — typed wrappers around /api/v1/.../beads endpoints.
 */

export interface BeadItem {
  id: string;
  parent_id: string | null;
  content: string;
  status: 'active' | 'parked' | 'completed' | 'abandoned';
  created_at: number;
  message_index: number | null;
  context_hint: string | null;
  // Branch lineage — present on beads inherited into a fork (see
  // design/bead-branching.md); null on natively-created beads.
  origin_conversation_id?: string | null;
  origin_bead_id?: string | null;
}

export interface BeadTreeResponse {
  beads: BeadItem[];
  active_id: string | null;
  parked_count: number;
  completed_count: number;
}

export interface ResumeBeadResponse {
  ok: boolean;
  resumed_bead: BeadItem;
  breadcrumb: string;
  suggested_message: string;
}

export interface ForkBeadResponse {
  ok: boolean;
  new_chat_id: string;
  branchedFrom: string;
  branchedAtMessageIndex: number;
  branchedFromLabel: string;
  message_count: number;
  inherited_bead_count: number;
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  if (path) h['X-Project-Root'] = path;
  return h;
}

function getProjectId(): string {
  return (window as any).__ZIYA_CURRENT_PROJECT_ID__ || 'default';
}

export async function getBeadTree(chatId: string): Promise<BeadTreeResponse> {
  const pid = getProjectId();
  const res = await fetch(
    `/api/v1/projects/${pid}/chats/${chatId}/beads`,
    { headers: headers() }
  );
  if (!res.ok) {
    if (res.status === 404) return { beads: [], active_id: null, parked_count: 0, completed_count: 0 };
    throw new Error(`Get beads failed: ${res.status}`);
  }
  return res.json();
}

export async function resumeBead(chatId: string, beadId: string): Promise<ResumeBeadResponse> {
  const pid = getProjectId();
  const res = await fetch(
    `/api/v1/projects/${pid}/chats/${chatId}/beads/resume`,
    {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ bead_id: beadId }),
    }
  );
  if (!res.ok) throw new Error(`Resume bead failed: ${res.status}`);
  return res.json();
}

/**
 * Split a conversation at a parked bead's seam into a new branched
 * conversation (Mode-1 non-destructive fork; see design/bead-branching.md).
 * The source is left intact; the new chat holds messages up to the bead's
 * message_index, carries the inherited beads, and is stamped with lineage
 * metadata.  Returns the new chat id so the caller can navigate to it.
 */
export async function forkFromBead(chatId: string, beadId: string): Promise<ForkBeadResponse> {
  const pid = getProjectId();
  const res = await fetch(
    `/api/v1/projects/${pid}/chats/${chatId}/beads/fork`,
    {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ bead_id: beadId }),
    }
  );
  if (!res.ok) throw new Error(`Fork from bead failed: ${res.status}`);
  return res.json();
}

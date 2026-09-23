/**
 * Handoff API client — design/conversation-handoff.md.
 *
 * Mirrors app/api/handoff.py.  Same project/header resolution as beadApi.
 */

export interface HandoffDocument {
  document: string;
  generatedAt: number;
  updatedAt?: number;
  editedAt?: number | null;
  sourceMessageCount?: number;
  tokenEstimate?: number;
}

export interface HandoffState {
  conversationId: string;
  title: string;
  messageCount: number;
  draft: HandoffDocument | null;
  handoff: HandoffDocument | null;
  lineageKind: 'fork' | 'branch' | 'handoff' | null;
  branchedFrom: string | null;
  handedOffTo: string | null;
  predecessors: { id: string; title: string | null; messageCount: number | null }[];
  openBeads: { id: string; content: string; status: string }[];
  additionalFiles: string[];
  contextPressure: number | null;
}

export interface HandoffCommitResponse {
  ok: boolean;
  child: Record<string, any> & { id: string; title: string };
  sourceId: string;
  handedOffTo: string;
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

function base(chatId: string): string {
  return `/api/v1/projects/${getProjectId()}/chats/${chatId}/handoff`;
}

async function check<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail || ''; } catch { /* ignore */ }
    throw new Error(`${what} failed: ${res.status}${detail ? ` — ${detail}` : ''}`);
  }
  return res.json();
}

export async function getHandoffState(chatId: string): Promise<HandoffState> {
  return check(await fetch(base(chatId), { headers: headers() }), 'Load handoff state');
}

/** Edit the living draft on a SOURCE.  Empty document clears it. */
export async function editHandoffDraft(chatId: string, document: string) {
  return check<{ ok: boolean; draft: HandoffDocument | null; cleared?: boolean }>(
    await fetch(`${base(chatId)}/draft`, { method: 'PATCH', headers: headers(), body: JSON.stringify({ document }) }),
    'Save handoff draft');
}

/** Edit the inherited document on a CONTINUATION. */
export async function editInheritedHandoff(chatId: string, document: string) {
  return check<{ ok: boolean; handoff: HandoffDocument }>(
    await fetch(base(chatId), { method: 'PATCH', headers: headers(), body: JSON.stringify({ document }) }),
    'Save handoff document');
}

export async function commitHandoff(
  chatId: string, body: { document?: string; workingSet?: string[]; title?: string },
): Promise<HandoffCommitResponse> {
  return check(await fetch(`${base(chatId)}/commit`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }),
    'Create continuation');
}

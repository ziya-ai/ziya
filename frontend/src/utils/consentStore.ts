/**
 * consentStore — the pure half of the chat-side consent runtime
 * (design/consent-runtime.md step 3).
 *
 * Two inputs, one view:
 *   - stream events ``consent_opened`` / ``consent_answered`` (live turn,
 *     forwarded by chatApi as DOM CustomEvents), and
 *   - ``GET /api/consent/open?conversation_id=`` (the reconnect handshake —
 *     a consent whose ``consent_opened`` frame rolled out of the relay
 *     buffer must still be answerable).
 *
 * Kept free of React so the reducer can be unit-tested and so both the
 * chat StreamedContent panel and any future tile can share it.
 */

export type ConsentScope = 'once' | 'conversation' | 'session' | 'always';
export type ConsentDecision = 'approve' | 'reject';

export interface ToolCallPayload {
  kind: 'tool_call';
  tool: string;
  op: string;
  args: Record<string, unknown>;
  preflight: Record<string, unknown> | null;
  tool_id?: string;
}

export interface QuestionPayload {
  kind: 'question';
  text: string;
  choices: string[];
}

export type ConsentPayload = ToolCallPayload | QuestionPayload;

/** Wire shape of a ``consent_opened`` frame and of /api/consent records. */
export interface ConsentRecord {
  request_id: string;
  conversation_id?: string;
  owner_id?: string;
  tool_id?: string;
  payload: ConsentPayload;
  scopes?: ConsentScope[];
  opened_at?: number;
  ttl_ms?: number | null;
  answer?: { decision: ConsentDecision; scope?: ConsentScope; answer?: string } | null;
  closed?: boolean;
}

export interface ConsentAnsweredEvent {
  request_id: string;
  decision: ConsentDecision;
  scope: ConsentScope;
  always_pending_signature?: boolean;
}

export const CONSENT_OPENED_EVENT = 'ziyaConsentOpened';
export const CONSENT_ANSWERED_EVENT = 'ziyaConsentAnswered';

/** Open requests, oldest first, keyed by request_id. */
export type OpenConsents = ConsentRecord[];

export function conversationOf(rec: ConsentRecord): string | undefined {
  return rec.conversation_id ?? rec.owner_id;
}

/** Add or replace a record; a settled/closed record is dropped. */
export function applyOpened(list: OpenConsents, rec: ConsentRecord): OpenConsents {
  const rest = list.filter(r => r.request_id !== rec.request_id);
  if (rec.closed || rec.answer) return rest;
  return [...rest, rec].sort((a, b) => (a.opened_at ?? 0) - (b.opened_at ?? 0));
}

export function applyAnswered(list: OpenConsents, ev: { request_id: string }): OpenConsents {
  return list.filter(r => r.request_id !== ev.request_id);
}

/** Replace the set for ONE conversation with the server's authoritative list,
 *  leaving other conversations' entries untouched. */
export function applyServerList(
  list: OpenConsents, conversationId: string, fromServer: ConsentRecord[],
): OpenConsents {
  const others = list.filter(r => conversationOf(r) !== conversationId);
  const mine = fromServer
    .filter(r => !r.closed && !r.answer)
    .map(r => ({ ...r, conversation_id: r.conversation_id ?? conversationId }));
  return [...others, ...mine].sort((a, b) => (a.opened_at ?? 0) - (b.opened_at ?? 0));
}

export function openFor(list: OpenConsents, conversationId: string): OpenConsents {
  return list.filter(r => conversationOf(r) === conversationId);
}

/** Human-readable one-liner for the panel header. */
export function describe(rec: ConsentRecord): string {
  const p = rec.payload;
  if (p.kind === 'question') return p.text || 'This turn is waiting for your input.';
  const op = p.op && p.op !== '*' ? ` ${p.op}` : '';
  return `Allow ${p.tool}${op}?`;
}

export const SCOPE_LABELS: Record<ConsentScope, string> = {
  once: 'Once',
  conversation: 'This conversation',
  session: 'This session',
  // ``always`` is honoured as a session grant until the signed widening
  // (design/consent-runtime.md step 4) makes it durable.
  always: 'Always (session now; stages for signing)',
};

export async function fetchOpenConsents(conversationId: string): Promise<ConsentRecord[]> {
  try {
    const res = await fetch(`/api/consent/open?conversation_id=${encodeURIComponent(conversationId)}`);
    if (!res.ok) return [];
    const body = await res.json();
    return Array.isArray(body) ? (body as ConsentRecord[]) : [];
  } catch {
    return [];
  }
}

export async function answerConsent(
  requestId: string,
  decision: ConsentDecision,
  scope: ConsentScope = 'once',
  answer = '',
): Promise<{ ok: boolean; status: number; detail?: string }> {
  try {
    const res = await fetch(`/api/consent/${encodeURIComponent(requestId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, scope: decision === 'reject' ? 'once' : scope, answer }),
    });
    if (res.ok) return { ok: true, status: res.status };
    let detail: string | undefined;
    try { detail = (await res.json())?.detail; } catch { /* body optional */ }
    return { ok: false, status: res.status, detail };
  } catch (e) {
    return { ok: false, status: 0, detail: String(e) };
  }
}

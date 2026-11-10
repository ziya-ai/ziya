/**
 * Conversation references API client (design/conversation-references.md,
 * slice 2).  Routes in app/api/chats.py over app/utils/conversation_references.py.
 * Components never call this directly — useConversationReferences() does.
 */

export type ReferenceKind = 'url' | 'file' | 'pdf' | 'chat';
export type ReferenceProvenance = 'pinned' | 'picked_up';

export interface ReferenceUse { kind: 'read' | 'cited' | 'opened'; turn: number; tool?: string; }

export interface ConversationReference {
    id: string;
    kind: ReferenceKind;
    locator: string;
    title: string | null;
    provenance: ReferenceProvenance;
    added_turn: number;
    pinned_turn?: number;
    last_used_turn?: number;
    uses: ReferenceUse[];
    /** Computed by the server: cited, or read in two or more turns. */
    useful: boolean;
    /** Picked up and unused for FADE_TURNS turns: counted, not listed. */
    faded: boolean;
}

export interface ReferencesSummary { pinned: number; picked_up: number; useful: number; faded: number; }

export interface ReferencesView {
    turn: number;
    summary: ReferencesSummary;
    references: ConversationReference[];
}

/** chatApi dispatches this when a turn's references_updated frame arrives;
 *  the hook's own writes dispatch it too so every mounted line re-fetches. */
export const REFERENCES_CHANGED_EVENT = 'ziyaReferencesChanged';

export function announceReferencesChanged(conversationId: string): void {
    if (typeof document === 'undefined' || !conversationId) return;
    document.dispatchEvent(new CustomEvent(REFERENCES_CHANGED_EVENT, { detail: { conversationId } }));
}

const base = (projectId: string, conversationId: string) =>
    `/api/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(conversationId)}/references`;

async function ok<T>(r: Response, what: string): Promise<T> {
    if (!r.ok) throw new Error(`${what} failed: ${r.status}`);
    return r.json();
}

export function fetchReferences(projectId: string, conversationId: string): Promise<ReferencesView> {
    return fetch(base(projectId, conversationId)).then(r => ok(r, 'references GET'));
}

export function pinReference(projectId: string, conversationId: string, locator: string, title?: string): Promise<ConversationReference> {
    return fetch(base(projectId, conversationId), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locator, title: title ?? null }),
    }).then(r => ok(r, 'references POST'));
}

export function promoteReference(projectId: string, conversationId: string, refId: string): Promise<ConversationReference> {
    return fetch(`${base(projectId, conversationId)}/${encodeURIComponent(refId)}/pin`, { method: 'POST' })
        .then(r => ok(r, 'references promote'));
}

export function unpinReference(projectId: string, conversationId: string, refId: string): Promise<{ ok: boolean }> {
    return fetch(`${base(projectId, conversationId)}/${encodeURIComponent(refId)}`, { method: 'DELETE' })
        .then(r => ok(r, 'references DELETE'));
}

/**
 * Pure helpers for conversation references (design/conversation-references.md,
 * slice 3).  No fetches, no React: the composer and the per-message icon
 * import these; the tests pin them without mounting either.
 */
import { parseZiyaLink } from './ziyaLinks';
import type { ConversationReference } from '../apis/referencesApi';

const URL_RE = /^https?:\/\/\S+$/i;

/**
 * The locator a composer paste names when the clipboard text is nothing but
 * one bare http(s) URL or ziya://chat link; null otherwise.  A path is NOT
 * taken from a paste: a bare "src/main.py" is as likely a word in a question
 * as a reference, and the paperclip menu covers the deliberate case.
 */
export function bareReferenceFromPaste(text: string | null | undefined): string | null {
    if (!text) return null;
    const s = text.trim();
    if (!s || /\s/.test(s)) return null;
    if (URL_RE.test(s)) return s;
    const link = parseZiyaLink(s);
    return link && link.kind === 'chat' ? s : null;
}

/**
 * 1-based turn the message at ``index`` belongs to: the count of unmuted
 * human messages up to and including it.  Mirrors the server's turn_index,
 * which counts the history the frontend sends (muted messages omitted), so
 * the number here is the one on the record's ``uses[].turn``.
 */
export function turnOfMessage(messages: Array<{ role?: string; muted?: boolean } | undefined>, index: number): number {
    let n = 0;
    for (let i = 0; i <= index && i < messages.length; i++) {
        const m = messages[i];
        if (m && m.role === 'human' && !m.muted) n++;
    }
    return Math.max(1, n);
}

export interface TurnReferenceUses {
    read: ConversationReference[];
    cited: ConversationReference[];
    added: ConversationReference[];
}

export function referencesForTurn(refs: ConversationReference[], turn: number): TurnReferenceUses {
    return {
        read: refs.filter(r => r.uses.some(u => u.kind === 'read' && u.turn === turn)),
        cited: refs.filter(r => r.uses.some(u => u.kind === 'cited' && u.turn === turn)),
        added: refs.filter(r => r.added_turn === turn),
    };
}

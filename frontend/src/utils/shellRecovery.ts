/**
 * Recovery decision for appending a message to a SHELL conversation.
 *
 * A shell record carries only first+last messages (bodies stripped), so
 * appending to it would discard the intermediate history.  ChatContext's
 * SHELL_GUARD therefore queues the message and recovers the full body first.
 *
 * The ladder is IDB -> server.  The load-bearing case is the third outcome:
 * when neither tier can produce a fuller body, appending anyway is NOT a
 * safe fallback.  The sync push treats a fresher local _version as
 * authoritative, so persisting a truncated array would push it to the
 * server and destroy the turns the server still holds.  Holding the message
 * (and saying so) is the only non-destructive option.
 *
 * Pure apart from the two injected accessors, so every branch is testable
 * without React or a live IndexedDB.
 */
import type { Message } from './types';

export type ShellRecoveryOutcome =
    /** A fuller body was found; replace the shell's messages with it. */
    | { action: 'adopt'; messages: Message[]; source: 'idb' | 'server' }
    /**
     * The local array is ALREADY complete — the server, which is
     * authoritative, reports no more messages than we hold.  Safe to append
     * onto the local array and clear the shell markers.
     */
    | { action: 'apply-local'; source: 'server-complete' }
    /**
     * Completeness could not be established.  Keep the queued messages
     * pending: do not append, do not persist.
     *
     * 'gone' is TERMINAL: the server authoritatively has no such chat, so a
     * retry cannot change the answer and the caller must not invite one.
     * 'unreachable' is transient -- the question could not be asked, or was
     * answered unusably.  Both hold the message, but only one is worth
     * repeating.
     */
    | { action: 'hold'; reason: 'no-project' | 'unreachable' | 'gone' };

export interface ShellRecoveryDeps {
    getIdbRecord: (id: string) => Promise<any | null>;
    /**
     * Fetch the server's copy.  Three outcomes, which the supplied function
     * must keep distinct:
     *   - a record -> the server answered
     *   - null     -> the server authoritatively has NO such chat (404)
     *   - throw    -> the question could not be asked (5xx, timeout, transport)
     *
     * Supplying a function that returns null for a server error reports a
     * transient fault as a permanent absence.  api/conversationSyncApi's
     * getChat does exactly that, which is why getChatResult exists.
     */
    getServerChat: (projectId: string, id: string) => Promise<any | null>;
}

/**
 * True only with POSITIVE proof that a shell's in-state messages are
 * complete: a known, non-zero _fullMessageCount the array already meets.
 *
 * "Unknown" and "zero" are deliberately NOT complete.  Defaulting an absent
 * count to 0 (`_fullMessageCount || 0`) made every countless shell look
 * complete, which is how a truncated record reached a blind append.
 */
export function isKnownCompleteShell(conv: {
    messages?: unknown[];
    _fullMessageCount?: number;
} | null | undefined): boolean {
    if (!conv) return false;
    const full = conv._fullMessageCount;
    if (typeof full !== 'number' || !Number.isFinite(full) || full <= 0) return false;
    return (conv.messages?.length ?? 0) >= full;
}

/** Extract a real message array, or null when the record has none. */
function bodyOf(rec: any): Message[] | null {
    const msgs = rec?.messages;
    return Array.isArray(msgs) ? (msgs as Message[]) : null;
}

export async function recoverShellMessages(
    conversationId: string,
    projectId: string | undefined | null,
    localCount: number,
    deps: ShellRecoveryDeps,
): Promise<ShellRecoveryOutcome> {
    // 1. IndexedDB.  A record that is ITSELF flagged as a shell is not a
    //    recovery source, however many entries its array happens to hold.
    try {
        const rec = await deps.getIdbRecord(conversationId);
        const body = bodyOf(rec);
        if (body && !rec?._isShell && body.length > localCount) {
            return { action: 'adopt', messages: body, source: 'idb' };
        }
    } catch {
        /* fall through to the server */
    }

    // 2. Server.  Reached routinely, not only on IDB corruption: a record
    //    flagged _isShell is excluded from every IDB write, so the shell
    //    being recovered may have no IDB row at all.
    if (!projectId) return { action: 'hold', reason: 'no-project' };
    let serverRec: any;
    try {
        serverRec = await deps.getServerChat(projectId, conversationId);
    } catch {
        // We could not ask.  Absence is NOT proven, so this stays retryable.
        return { action: 'hold', reason: 'unreachable' };
    }
    // Null is the dep's authoritative "no such chat".  Terminal.
    if (serverRec === null || serverRec === undefined) {
        return { action: 'hold', reason: 'gone' };
    }
    const serverBody = bodyOf(serverRec);
    // The server HAS the chat but sent no message array: a malformed answer,
    // not an absence.  Deliberately retryable -- a bad payload can be a
    // transient serialization fault, and calling it 'gone' would tell the
    // user their history is lost on the strength of one broken response.
    if (!serverBody) return { action: 'hold', reason: 'unreachable' };
    if (serverBody.length > localCount) {
        return { action: 'adopt', messages: serverBody, source: 'server' };
    }
    // The server holds no more than we do, so the local array is not
    // truncated and appending to it cannot lose history.
    return { action: 'apply-local', source: 'server-complete' };
}

// ---------------------------------------------------------------------------
// Held-message ownership
// ---------------------------------------------------------------------------

export interface HeldMessagePartition {
    /** Human-authored text to hand back to the composer. */
    returnToComposer: Message[];
    /** Messages with no composer to return to; stay queued for a retry. */
    keepQueued: Message[];
}

/**
 * Decide who owns each message a hold could not deliver.
 *
 * Holding preserves the messages but strands the user: handleSend has already
 * cleared the composer, so their text is visible nowhere, and if recovery
 * never succeeds this session it is simply gone.  Handing it back is only
 * safe if the queue also releases it — otherwise the same text is owned twice
 * and can be sent twice.
 *
 * A human message therefore LEAVES the queue and goes to the composer, while
 * anything else (a streamed assistant turn, a system note) stays queued: it
 * has no composer to return to, and dropping it would lose content that
 * exists nowhere else.
 */
export function partitionHeldMessages(held: Message[]): HeldMessagePartition {
    const returnToComposer: Message[] = [];
    const keepQueued: Message[] = [];
    if (!Array.isArray(held)) return { returnToComposer, keepQueued };
    for (const m of held) {
        const anyM = m as any;
        if (anyM?.role === 'human' && typeof anyM?.content === 'string') {
            // Empty text is dropped rather than queued: there is nothing to
            // preserve, and re-injecting "" would clear whatever the user has
            // typed since while recovering nothing.
            if (anyM.content.trim().length > 0) returnToComposer.push(m);
            continue;
        }
        keepQueued.push(m);
    }
    return { returnToComposer, keepQueued };
}

/**
 * Flatten held human messages into composer text.  Blank line between
 * entries so two separate sends stay legible as two paragraphs.
 */
export function composerTextFromHeld(msgs: Message[]): string {
    if (!Array.isArray(msgs)) return '';
    return msgs
        .map(m => (typeof (m as any)?.content === 'string' ? (m as any).content : ''))
        .filter(s => s.length > 0)
        .join('\n\n');
}

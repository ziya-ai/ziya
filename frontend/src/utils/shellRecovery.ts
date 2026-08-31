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
     */
    | { action: 'hold'; reason: 'no-project' | 'unreachable' };

export interface ShellRecoveryDeps {
    getIdbRecord: (id: string) => Promise<any | null>;
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
    let serverBody: Message[] | null = null;
    try {
        serverBody = bodyOf(await deps.getServerChat(projectId, conversationId));
    } catch {
        return { action: 'hold', reason: 'unreachable' };
    }
    if (!serverBody) return { action: 'hold', reason: 'unreachable' };
    if (serverBody.length > localCount) {
        return { action: 'adopt', messages: serverBody, source: 'server' };
    }
    // The server holds no more than we do, so the local array is not
    // truncated and appending to it cannot lose history.
    return { action: 'apply-local', source: 'server-complete' };
}

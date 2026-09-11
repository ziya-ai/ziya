/**
 * The shell projection used for the sidebar and folder-tree conversation list.
 *
 * A "shell" is a Conversation whose message BODIES have been stripped to
 * first+last, so a sidebar listing 120 conversations does not hold every
 * message in memory.  The \`_isShell\` / \`_fullMessageCount\` markers it carries
 * are load-bearing well beyond display:
 *
 *   - ChatContext's SHELL_GUARD refuses to append to a marker-bearing record
 *     until it has recovered the full body (see utils/shellRecovery.ts).
 *   - Every IDB write filter drops marker-bearing records, so a shell can
 *     never overwrite the full record it was derived from.
 *   - bulkSync refuses to push them, so a shell can never truncate the
 *     authoritative server record.
 *
 * Because a marker makes a record unappendable AND unwritable until recovery
 * succeeds, stamping one on a record that was never actually stripped is not a
 * harmless over-approximation -- it is a write lock with no key.  That is the
 * empty-conversation case guarded below.
 *
 * Extracted from db.ts's getConversationShells so the marker decision is
 * directly testable without a live IndexedDB, in the same spirit as
 * utils/shellRecovery.ts.  db.ts imports this rather than re-inlining it, and
 * conversationShell.test.ts asserts that structurally.
 */
import type { Conversation } from './types';

/** Blank a message body while keeping the fields the sidebar reads. */
const stripMessage = (m: any) => m ? ({
    id: m.id, role: m.role, content: '', _timestamp: m._timestamp,
}) : m;

/**
 * Project a stored conversation to its shell form.
 *
 * Returns null for a record too malformed to display, which callers treat as
 * "drop from the cache" rather than "store as-is".
 */
export function stripToShell(conv: any): Conversation | null {
    if (!conv?.id || typeof conv.id !== 'string' || !Array.isArray(conv.messages)) return null;

    // A record with NO messages has had nothing stripped: the "shell" would be
    // byte-identical to the full record, so the markers describe a truncation
    // that did not happen.  The cost is not cosmetic.  SHELL_GUARD arms on the
    // very first message, because a _fullMessageCount of 0 cannot satisfy
    // isKnownCompleteShell's positive-count proof.  Recovery then finds no IDB
    // body (shells are excluded from every IDB write) and a 404 from a server
    // that has never seen this conversation, since empty conversations are
    // never pushed.  The outcome is 'hold' and the message is DISCARDED, so a
    // restored empty conversation can never accept its first message.
    //
    // shouldFetchFull stays correct without the markers: an unflagged empty
    // record compares on its real _version and a messages.length of 0, so a
    // populated server summary still forces a pull.
    if (conv.messages.length === 0) return { ...conv } as Conversation;

    const firstMsg = stripMessage(conv.messages[0]);
    const lastMsg = conv.messages.length > 1
        ? stripMessage(conv.messages[conv.messages.length - 1])
        : null;
    return {
        ...conv,
        // firstMsg can still be falsy: a stored null entry survives
        // stripMessage untouched, and [null] is worse than [] downstream.
        messages: firstMsg ? (lastMsg ? [firstMsg, lastMsg] : [firstMsg]) : [],
        _isShell: true,
        _fullMessageCount: conv.messages.length,
    } as Conversation;
}

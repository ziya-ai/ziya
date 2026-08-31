/**
 * Conversation title derivation.
 *
 * A conversation's title is pulled from the first *human* message the user
 * sends.  Utility actions (e.g. a model change) can insert system messages
 * into a fresh conversation before the user's first query, so "the message
 * array is empty" is not a reliable first-message signal.  Instead, the
 * title is derived when a human message arrives while no prior human
 * message exists and the title is still a placeholder.
 */

// Titles that are placeholders rather than meaningful (auto- or user-set)
// names.  Also used by the sync/save merge guards in ChatContext to avoid
// downgrading a resolved title back to a placeholder when in-memory
// _version is newer.
export const PLACEHOLDER_TITLES = new Set([
    'New Conversation', 'New Ephemeral Chat', 'Loading...', 'Untitled', '',
]);

/**
 * True when `message` should become the conversation's title: it is a human
 * message, no human message precedes it, and the current title is still a
 * placeholder (a seeded or user-renamed title is never clobbered).
 */
export function shouldDeriveTitleFromMessage(
    message: { role: string },
    existingMessages: ReadonlyArray<{ role: string }> | undefined,
    currentTitle: string | undefined,
): boolean {
    if (message.role !== 'human') return false;
    if (!PLACEHOLDER_TITLES.has(currentTitle ?? '')) return false;
    return !(existingMessages ?? []).some(m => m.role === 'human');
}

/** Truncate message content into a display title. */
export function deriveTitleFromContent(content: string, maxLength: number): string {
    return content.slice(0, maxLength) + (content.length > maxLength ? '...' : '');
}

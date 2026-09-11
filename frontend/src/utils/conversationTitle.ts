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

/**
 * True when a task-card launch into `conversation` should seed the
 * conversation's title from the card's name: the title is still a
 * placeholder and no human dialog exists yet.  A conversation whose first
 * content is a task run never receives the human message that
 * `shouldDeriveTitleFromMessage` keys on, so without this seed it would
 * stay "New Conversation" forever.  Shell records (content-stripped
 * sidebar entries) are excluded: their empty message list would
 * masquerade as "no dialog" while the real conversation has content.
 */
export function shouldSeedTitleFromTaskCard(
    conversation: {
        title?: string;
        messages?: ReadonlyArray<{ role: string }>;
        _isShell?: boolean;
    } | undefined | null,
): boolean {
    if (!conversation || conversation._isShell) return false;
    if (!PLACEHOLDER_TITLES.has(conversation.title ?? '')) return false;
    return !(conversation.messages ?? []).some(m => m.role === 'human');
}
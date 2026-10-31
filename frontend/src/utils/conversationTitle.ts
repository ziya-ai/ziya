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

/** Hard cap on a STORED title.  Not a display width: the row and the
 *  header breadcrumb ellipsize at render.  Only there so a pasted log
 *  cannot become a multi-kilobyte title. */
export const TITLE_STORE_MAX_LENGTH = 200;

/**
 * Derive a title from message content: the first non-blank line, capped.
 * The cap used to be the sidebar's width at creation time (width/6,
 * clamped 30-80), which persisted "..." into titles that then truncated
 * mid-screen in any wider panel.  See design/visual-refresh.md.
 */
export function deriveTitleFromContent(content: string, maxLength: number = TITLE_STORE_MAX_LENGTH): string {
    const firstLine = (content ?? '').split('\n').map(l => l.trim()).find(l => l.length > 0) ?? '';
    return firstLine.slice(0, maxLength) + (firstLine.length > maxLength ? '...' : '');
}

/**
 * Title to keep when a record from elsewhere (server summary, hydrated body)
 * is merged over a local one.  A real incoming title wins; a placeholder
 * never replaces a real local title.  Server records are often created or
 * re-stamped by backend writers (tool auto-vivify, bead/handoff/context
 * writes) before the client's derived title has landed, so "newer" does not
 * mean "has the title".
 */
export function mergeTitle(incoming: string | undefined, local: string | undefined): string | undefined {
    if (incoming && !PLACEHOLDER_TITLES.has(incoming)) return incoming;
    if (local && !PLACEHOLDER_TITLES.has(local)) return local;
    return incoming || local;
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
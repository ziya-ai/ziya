/**
 * jobSeen — when the user last opened each conversation, per project, for
 * the sidebar's clear-on-visit rule on finished jobs (jobStatusGlyph.ts).
 *
 * Separate from ``Conversation.lastAccessedAt``, which despite its name is
 * bumped when a message is ADDED, not when the chat is merely opened; a
 * conversation you looked at and left alone would never clear.  Stored in
 * localStorage: it is a per-browser reading habit, not conversation data,
 * and nothing else needs to know it.  Epoch SECONDS, to compare directly
 * with the server's ``ended_at``.
 */

export type JobsSeen = Record<string, number>;

const key = (projectId: string) => `ZIYA_JOBS_SEEN:${projectId}`;

export function readJobsSeen(projectId: string | undefined | null): JobsSeen {
    if (!projectId) return {};
    try {
        const raw = localStorage.getItem(key(projectId));
        const parsed = raw ? JSON.parse(raw) : null;
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch {
        return {};
    }
}

/** Record a visit; returns the updated map. */
export function markJobsSeen(
    projectId: string, conversationId: string, nowS: number = Date.now() / 1000,
): JobsSeen {
    const next = { ...readJobsSeen(projectId), [conversationId]: nowS };
    try {
        localStorage.setItem(key(projectId), JSON.stringify(next));
    } catch { /* quota / private mode: the in-memory map still updates the rows */ }
    return next;
}

/**
 * Data retention enforcement for the browser.
 *
 * Fetches the effective retention policy from the server and removes
 * any IndexedDB conversations whose creation time exceeds the TTL.
 */

interface RetentionPolicy {
    conversation_data_ttl_seconds: number | null;
    conversation_data_ttl_days: number | null;
    policy_reason: string;
    has_retention_policy: boolean;
}

let cachedPolicy: RetentionPolicy | null = null;
let lastFetchTime = 0;
const POLICY_CACHE_MS = 60_000; // re-fetch at most once per minute

async function fetchRetentionPolicy(): Promise<RetentionPolicy> {
    const now = Date.now();
    if (cachedPolicy && now - lastFetchTime < POLICY_CACHE_MS) {
        return cachedPolicy;
    }
    try {
        const resp = await fetch('/api/v1/retention-policy');
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }
        cachedPolicy = await resp.json();
        lastFetchTime = now;
        return cachedPolicy!;
    } catch (err) {
        console.warn('Could not fetch retention policy:', err);
        // Return a no-op policy so we never accidentally delete data
        return {
            conversation_data_ttl_seconds: null,
            conversation_data_ttl_days: null,
            policy_reason: '',
            has_retention_policy: false,
        };
    }
}

/**
 * Purge conversations from IndexedDB that exceed the retention TTL.
 *
 * @param db  The ConversationDB instance (must be initialised).
 */
export async function purgeExpiredConversations(db: any): Promise<void> {
    const policy = await fetchRetentionPolicy();
    if (!policy.has_retention_policy || policy.conversation_data_ttl_seconds == null) {
        return; // No retention policy active — nothing to purge
    }

    const ttlMs = policy.conversation_data_ttl_seconds * 1000;
    const cutoff = Date.now() - ttlMs;

    const conversations = await db.getConversations();
    if (!conversations || conversations.length === 0) return;

    const kept: any[] = [];
    let purgedCount = 0;

    for (const conv of conversations) {
        // Use the MOST RECENT activity timestamp for retention decisions.
        // A conversation created months ago but used yesterday must not be
        // purged.  Prefer lastAccessedAt > _version > lastActiveAt > createdAt.
        const lastActivity = conv.lastAccessedAt || conv._version || conv.lastActiveAt || conv.createdAt || 0;
        if (lastActivity > 0 && lastActivity < cutoff) {
            purgedCount++;
        } else {
            kept.push(conv);
        }
    }

    if (purgedCount > 0) {
        console.log(
            `🗑️ Retention policy: purging ${purgedCount} expired conversation(s) ` +
            `(policy: ${policy.policy_reason})`
        );
        await db.saveConversations(kept);
    }
}

/**
 * Garbage-collect empty "New Conversation" nodes that have sat idle
 * for longer than `maxAgeMs` (default: 1 hour).
 *
 * A conversation is considered empty when:
 *   - title is exactly "New Conversation"
 *   - messages array is empty (length === 0)
 *
 * Protected conversations (never collected):
 *   - The conversation currently selected in this tab
 *   - Any conversation that is actively streaming
 */

const DEFAULT_EMPTY_CONV_MAX_AGE_MS = 60 * 60 * 1000; // 1 hour

export interface GcResult {
    kept: any[];
    purgedIds: string[];
}

export function gcEmptyConversations(
    conversations: any[],
    protectedIds: Set<string>,
    maxAgeMs: number = DEFAULT_EMPTY_CONV_MAX_AGE_MS,
): GcResult {
    const cutoff = Date.now() - maxAgeMs;
    const kept: any[] = [];
    const purgedIds: string[] = [];

    for (const conv of conversations) {
        const isEmpty = conv.title === 'New Conversation'
            && (!conv.messages || conv.messages.length === 0);
        const age = conv.lastAccessedAt || conv._version || 0;
        const isStale = age > 0 && age < cutoff;

        if (isEmpty && isStale && !protectedIds.has(conv.id)) {
            purgedIds.push(conv.id);
        } else {
            kept.push(conv);
        }
    }

    return { kept, purgedIds };
}

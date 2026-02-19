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
        // Use the earliest meaningful timestamp
        const createdAt = conv.createdAt || conv.lastAccessedAt || conv._version || 0;
        if (createdAt > 0 && createdAt < cutoff) {
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

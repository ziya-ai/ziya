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

    // Cursor-based scan + delete.  Previously this called
    // getConversations() (full getAll including message bodies) and then
    // saveConversations(kept) which re-wrote every retained record.  On
    // databases with hundreds of conversations and large message bodies
    // that was tens of seconds of main-thread work and held the
    // ziya-db-read lock long enough to starve the sidebar's shell load.
    //
    // The cursor-based version walks records one at a time and deletes
    // expired ones in-place via cursor.delete().  Retained records are
    // not re-serialised.  Peak memory stays flat regardless of DB size.
    const rawDb = db?.db as IDBDatabase | undefined;
    if (!rawDb) {
        console.warn('Retention purge: no IDB handle, skipping');
        return;
    }
    if (!rawDb.objectStoreNames.contains('conversations')) {
        return;
    }

    const runPurge = () => new Promise<number>((resolve, reject) => {
        let tx: IDBTransaction;
        try {
            tx = rawDb.transaction(['conversations'], 'readwrite');
        } catch (err) {
            reject(err);
            return;
        }
        const store = tx.objectStore('conversations');
        const req = store.openCursor();
        let purgedCount = 0;

        req.onsuccess = () => {
            const cursor = req.result;
            if (!cursor) return; // tx.oncomplete will resolve
            const conv: any = cursor.value;
            // Most recent activity wins: lastAccessedAt > _version > lastActiveAt > createdAt.
            const lastActivity = conv?.lastAccessedAt || conv?._version || conv?.lastActiveAt || conv?.createdAt || 0;
            if (lastActivity > 0 && lastActivity < cutoff) {
                cursor.delete();
                purgedCount++;
            }
            cursor.continue();
        };
        req.onerror = () => reject(req.error);
        tx.oncomplete = () => resolve(purgedCount);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(new Error('Retention purge transaction aborted'));
    });

    // Serialise against other DB writers via the existing Web Lock.  This
    // is the same lock name db.ts uses for its write path.
    const purgedCount = navigator.locks
        ? await navigator.locks.request('ziya-db-write', runPurge)
        : await runPurge();

    if (purgedCount > 0) {
        console.log(
            `🗑️ Retention policy: purged ${purgedCount} expired conversation(s) ` +
            `(policy: ${policy.policy_reason})`
        );
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

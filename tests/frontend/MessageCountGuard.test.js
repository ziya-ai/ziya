/**
 * Tests for message-count regression guards.
 *
 * Validates the defence-in-depth strategy that prevents partial/stale/shell
 * conversation data from overwriting complete message histories.
 *
 * Covers:
 *   - SERVER_SYNC merge: in-memory preservation when merged has fewer messages
 *   - SERVER_SYNC merge: version-wins logic with message count check
 *   - Lazy-load guards: IDB and server fetch won't replace with fewer messages
 *   - addMessageToConversation: shell detection and recovery
 *   - IDB dedup: shell entries never overwrite non-shell entries
 *   - Server bulk-sync: message regression blocked
 */

function makeMessages(count) {
    return Array.from({ length: count }, (_, i) => ({
        role: i % 2 === 0 ? 'human' : 'assistant',
        content: `Message ${i}`,
    }));
}

function makeConversation(id, messageCount, overrides = {}) {
    return {
        id,
        title: `Conv ${id}`,
        messages: makeMessages(messageCount),
        _version: Date.now(),
        isActive: true,
        ...overrides,
    };
}

// ---------------------------------------------------------------------------
// 1. SERVER_SYNC in-memory preservation guard
// ---------------------------------------------------------------------------
describe('SERVER_SYNC in-memory message preservation', () => {
    /**
     * Simulates the guard at ChatContext.tsx ~line 2040 (post-fix):
     * When merged data has fewer messages than in-memory, keep in-memory.
     */
    function applyInMemoryGuard(mergedConvs, inMemoryConvs) {
        for (const mc of mergedConvs) {
            const mcMsgCount = mc.messages?.length || 0;
            const inMemory = inMemoryConvs.find(p => p.id === mc.id);
            const inMemoryCount = inMemory?.messages?.length || 0;
            if (inMemoryCount > mcMsgCount) {
                mc.messages = inMemory.messages;
            }
        }
        return mergedConvs;
    }

    it('preserves in-memory messages when merged has zero messages', () => {
        const merged = [makeConversation('a', 0)];
        const inMemory = [makeConversation('a', 50)];

        applyInMemoryGuard(merged, inMemory);

        expect(merged[0].messages.length).toBe(50);
    });

    it('preserves in-memory messages when merged has FEWER (non-zero) messages', () => {
        // This is the critical fix — the old guard only fired for length === 0
        const merged = [makeConversation('a', 30)];
        const inMemory = [makeConversation('a', 100)];

        applyInMemoryGuard(merged, inMemory);

        expect(merged[0].messages.length).toBe(100);
    });

    it('accepts merged messages when merged has MORE than in-memory', () => {
        const merged = [makeConversation('a', 120)];
        const inMemory = [makeConversation('a', 100)];

        applyInMemoryGuard(merged, inMemory);

        expect(merged[0].messages.length).toBe(120);
    });

    it('accepts merged messages when counts are equal', () => {
        const merged = [makeConversation('a', 50)];
        const inMemory = [makeConversation('a', 50)];

        applyInMemoryGuard(merged, inMemory);

        expect(merged[0].messages.length).toBe(50);
    });

    it('handles conversation not present in in-memory state', () => {
        const merged = [makeConversation('a', 30)];
        const inMemory = [];

        applyInMemoryGuard(merged, inMemory);

        expect(merged[0].messages.length).toBe(30);
    });
});

// ---------------------------------------------------------------------------
// 2. SERVER_SYNC version-wins with message count guard
// ---------------------------------------------------------------------------
describe('SERVER_SYNC version-wins merge guard', () => {
    /**
     * Simulates the guard at ChatContext.tsx ~line 1922 (post-fix):
     * When server version is newer but has fewer messages, keep local messages.
     */
    function mergeWithGuard(local, serverFull) {
        const localMsgCount = local.messages?.length || 0;
        const serverMsgCount = serverFull.messages?.length || 0;

        if (serverMsgCount < localMsgCount && localMsgCount > 2) {
            return { ...serverFull, messages: local.messages };
        }
        return { ...serverFull };
    }

    it('keeps local messages when server has fewer', () => {
        const local = makeConversation('a', 100, { _version: 1000 });
        const server = makeConversation('a', 70, { _version: 2000 });

        const result = mergeWithGuard(local, server);

        expect(result.messages.length).toBe(100);
        expect(result._version).toBe(2000); // metadata from server
    });

    it('accepts server messages when server has more', () => {
        const local = makeConversation('a', 50, { _version: 1000 });
        const server = makeConversation('a', 80, { _version: 2000 });

        const result = mergeWithGuard(local, server);

        expect(result.messages.length).toBe(80);
    });

    it('allows server to win when local has very few messages (shell threshold)', () => {
        const local = makeConversation('a', 2, { _version: 1000 });
        const server = makeConversation('a', 1, { _version: 2000 });

        const result = mergeWithGuard(local, server);

        expect(result.messages.length).toBe(1);
    });

    it('accepts equal message counts', () => {
        const local = makeConversation('a', 50, { _version: 1000 });
        const server = makeConversation('a', 50, { _version: 2000 });

        const result = mergeWithGuard(local, server);

        expect(result.messages.length).toBe(50);
    });
});

// ---------------------------------------------------------------------------
// 3. Lazy-load guards
// ---------------------------------------------------------------------------
describe('Lazy-load message count guard', () => {
    function shouldAcceptLazyLoad(currentMessages, loadedMessages) {
        return loadedMessages.length > 0 &&
            loadedMessages.length >= (currentMessages?.length || 0);
    }

    it('accepts loaded messages when current is empty', () => {
        expect(shouldAcceptLazyLoad([], makeMessages(50))).toBe(true);
    });

    it('accepts loaded messages when current has fewer (shell)', () => {
        expect(shouldAcceptLazyLoad(makeMessages(2), makeMessages(50))).toBe(true);
    });

    it('rejects loaded messages when they have fewer than current', () => {
        expect(shouldAcceptLazyLoad(makeMessages(100), makeMessages(50))).toBe(false);
    });

    it('accepts loaded messages when counts are equal', () => {
        expect(shouldAcceptLazyLoad(makeMessages(50), makeMessages(50))).toBe(true);
    });

    it('rejects empty loaded messages', () => {
        expect(shouldAcceptLazyLoad(makeMessages(50), [])).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// 4. Shell detection in addMessageToConversation
// ---------------------------------------------------------------------------
describe('addMessageToConversation shell detection', () => {
    function isShellWithDataLossRisk(conv) {
        if (!conv._isShell) return false;
        const fullCount = conv._fullMessageCount || 0;
        return fullCount > conv.messages.length;
    }

    it('detects shell with fewer messages than full count', () => {
        const shell = makeConversation('a', 2, {
            _isShell: true,
            _fullMessageCount: 100,
        });
        expect(isShellWithDataLossRisk(shell)).toBe(true);
    });

    it('does not flag non-shell conversations', () => {
        const full = makeConversation('a', 100, {
            _isShell: false,
            _fullMessageCount: 100,
        });
        expect(isShellWithDataLossRisk(full)).toBe(false);
    });

    it('does not flag shells where full count equals message count', () => {
        const shell = makeConversation('a', 2, {
            _isShell: true,
            _fullMessageCount: 2,
        });
        expect(isShellWithDataLossRisk(shell)).toBe(false);
    });

    it('does not flag shells without full count metadata', () => {
        const shell = makeConversation('a', 2, {
            _isShell: true,
        });
        expect(isShellWithDataLossRisk(shell)).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// 5. IDB dedup guard: shell vs non-shell
// ---------------------------------------------------------------------------
describe('IDB dedup shell guard', () => {
    function dedupConversations(conversations) {
        const deduped = new Map();
        for (const conv of conversations) {
            if (conv._isShell) {
                const fullCount = conv._fullMessageCount || 0;
                if (conv.messages.length < fullCount) {
                    continue;
                }
            }

            const existing = deduped.get(conv.id);
            if (!existing) {
                deduped.set(conv.id, conv);
            } else if (conv._isShell && !existing._isShell) {
                continue; // Never let a shell overwrite a non-shell
            } else {
                const existingMsgCount = existing.messages?.length || 0;
                const currentMsgCount = conv.messages?.length || 0;
                const existingVersion = existing._version || 0;
                const currentVersion = conv._version || 0;

                if (currentMsgCount > existingMsgCount ||
                    (currentMsgCount === existingMsgCount && currentVersion > existingVersion)) {
                    deduped.set(conv.id, conv);
                }
            }
        }
        return Array.from(deduped.values());
    }

    it('keeps non-shell when shell appears later in array', () => {
        const full = makeConversation('a', 100, { _version: 1000 });
        const shell = makeConversation('a', 2, {
            _version: 2000,
            _isShell: true,
            _fullMessageCount: 100,
        });

        const result = dedupConversations([full, shell]);

        expect(result.length).toBe(1);
        expect(result[0].messages.length).toBe(100);
    });

    it('keeps non-shell when shell appears first in array', () => {
        const shell = makeConversation('a', 2, {
            _version: 2000,
            _isShell: true,
            _fullMessageCount: 100,
        });
        const full = makeConversation('a', 100, { _version: 1000 });

        const result = dedupConversations([shell, full]);

        expect(result.length).toBe(1);
        expect(result[0].messages.length).toBe(100);
    });

    it('keeps entry with more messages when neither is shell', () => {
        const fewer = makeConversation('a', 50, { _version: 2000 });
        const more = makeConversation('a', 100, { _version: 1000 });

        const result = dedupConversations([fewer, more]);

        expect(result.length).toBe(1);
        expect(result[0].messages.length).toBe(100);
    });

    it('prefers newer version when message counts are equal', () => {
        const older = makeConversation('a', 50, { _version: 1000 });
        const newer = makeConversation('a', 50, { _version: 2000 });

        const result = dedupConversations([older, newer]);

        expect(result.length).toBe(1);
        expect(result[0]._version).toBe(2000);
    });
});

// ---------------------------------------------------------------------------
// 6. Server-side bulk-sync message regression guard
// ---------------------------------------------------------------------------
describe('Server bulk-sync message regression guard', () => {
    function applyServerGuard(existing, incoming) {
        const existingMsgCount = existing.messages?.length || 0;
        const incomingMsgCount = incoming.messages?.length || 0;

        if (incomingMsgCount < existingMsgCount && existingMsgCount > 2) {
            return { ...incoming, messages: existing.messages };
        }
        return { ...incoming };
    }

    it('preserves existing messages when incoming has fewer', () => {
        const existing = makeConversation('a', 100, { _version: 1000 });
        const incoming = makeConversation('a', 50, { _version: 2000 });

        const result = applyServerGuard(existing, incoming);

        expect(result.messages.length).toBe(100);
        expect(result._version).toBe(2000);
    });

    it('accepts incoming when it has more messages', () => {
        const existing = makeConversation('a', 50, { _version: 1000 });
        const incoming = makeConversation('a', 100, { _version: 2000 });

        const result = applyServerGuard(existing, incoming);

        expect(result.messages.length).toBe(100);
    });

    it('allows regression when existing has very few messages (shell threshold)', () => {
        const existing = makeConversation('a', 2, { _version: 1000 });
        const incoming = makeConversation('a', 1, { _version: 2000 });

        const result = applyServerGuard(existing, incoming);

        expect(result.messages.length).toBe(1);
    });
});

// ---------------------------------------------------------------------------
// 7. Retention purge timestamp priority
// ---------------------------------------------------------------------------
describe('Retention purge timestamp priority', () => {
    // Mirrors the logic in retentionPurge.ts purgeExpiredConversations
    function getRetentionTimestamp(conv) {
        // CORRECT order: most-recent activity first
        return conv.lastAccessedAt || conv._version || conv.lastActiveAt || conv.createdAt || 0;
    }

    // The OLD (buggy) order for comparison
    function getRetentionTimestampBuggy(conv) {
        return conv.createdAt || conv.lastAccessedAt || conv._version || 0;
    }

    it('uses lastAccessedAt over createdAt for retention decisions', () => {
        const conv = {
            id: 'old-but-active',
            createdAt: 1000,        // created a long time ago
            lastAccessedAt: 9999,   // but used very recently
            messages: [],
        };

        const cutoff = 5000;

        // The correct implementation should NOT purge this conversation
        const timestamp = getRetentionTimestamp(conv);
        expect(timestamp).toBe(9999); // lastAccessedAt wins
        expect(timestamp > cutoff).toBe(true); // NOT expired

        // The buggy implementation WOULD purge it
        const buggyTimestamp = getRetentionTimestampBuggy(conv);
        expect(buggyTimestamp).toBe(1000); // createdAt wins (wrong!)
        expect(buggyTimestamp > cutoff).toBe(false); // falsely expired
    });

    it('falls back through timestamp chain correctly', () => {
        // Only _version available
        expect(getRetentionTimestamp({ _version: 5000 })).toBe(5000);
        // Only createdAt available (last resort)
        expect(getRetentionTimestamp({ createdAt: 3000 })).toBe(3000);
        // Nothing available
        expect(getRetentionTimestamp({})).toBe(0);
        // lastActiveAt used when lastAccessedAt missing
        expect(getRetentionTimestamp({ lastActiveAt: 7000, createdAt: 1000 })).toBe(7000);
    });

    it('does not purge recently-accessed conversations regardless of createdAt', () => {
        const now = Date.now();
        const sixMonthsAgo = now - (180 * 24 * 60 * 60 * 1000);
        const yesterday = now - (24 * 60 * 60 * 1000);
        const ttlMs = 30 * 24 * 60 * 60 * 1000; // 30-day retention
        const cutoff = now - ttlMs;

        const conv = {
            id: 'old-creation',
            createdAt: sixMonthsAgo,
            lastAccessedAt: yesterday,
            messages: [{ content: 'still using this' }],
        };

        const timestamp = getRetentionTimestamp(conv);
        expect(timestamp).toBe(yesterday);
        expect(timestamp > cutoff).toBe(true); // NOT expired — used yesterday
    });
});

// ---------------------------------------------------------------------------
// 8. Cross-tab merge message count guard
// ---------------------------------------------------------------------------
describe('Cross-tab mergeConversations message count guard', () => {
    // Mirrors the mergeConversations logic in ChatContext.tsx
    function mergeConversations(local, remote) {
        const merged = new Map();
        local.forEach(conv => merged.set(conv.id, conv));

        remote.forEach(remoteConv => {
            const localConv = merged.get(remoteConv.id);
            if (!localConv) {
                merged.set(remoteConv.id, { ...remoteConv, isActive: true });
                return;
            }
            const localMsgCount = localConv.messages?.length || 0;
            const remoteMsgCount = remoteConv.messages?.length || 0;
            if ((remoteConv._version || 0) > (localConv._version || 0)
                && (remoteMsgCount >= localMsgCount || localMsgCount <= 2)) {
                merged.set(remoteConv.id, {
                    ...remoteConv,
                    isActive: localConv?.isActive ?? true,
                });
            }
        });

        return Array.from(merged.values());
    }

    it('blocks remote with fewer messages even if version is newer', () => {
        const local = [makeConversation('a', 100, { _version: 1000 })];
        const remote = [makeConversation('a', 50, { _version: 2000 })];

        const result = mergeConversations(local, remote);

        expect(result.length).toBe(1);
        expect(result[0].messages.length).toBe(100); // local preserved
    });

    it('accepts remote with more messages and newer version', () => {
        const local = [makeConversation('a', 50, { _version: 1000 })];
        const remote = [makeConversation('a', 100, { _version: 2000 })];

        const result = mergeConversations(local, remote);

        expect(result.length).toBe(1);
        expect(result[0].messages.length).toBe(100); // remote accepted
    });

    it('accepts remote with fewer messages when local has <= 2 (shell threshold)', () => {
        const local = [makeConversation('a', 2, { _version: 1000 })];
        const remote = [makeConversation('a', 1, { _version: 2000 })];

        const result = mergeConversations(local, remote);

        expect(result[0].messages.length).toBe(1); // remote accepted (shell threshold)
    });

    it('adds new remote conversations not present locally', () => {
        const local = [makeConversation('a', 10, { _version: 1000 })];
        const remote = [makeConversation('b', 5, { _version: 2000 })];

        const result = mergeConversations(local, remote);

        expect(result.length).toBe(2);
    });

    it('keeps local when versions are equal regardless of message count', () => {
        const local = [makeConversation('a', 100, { _version: 1000 })];
        const remote = [makeConversation('a', 50, { _version: 1000 })];

        const result = mergeConversations(local, remote);

        expect(result[0].messages.length).toBe(100); // local kept (version not greater)
    });
});

// ---------------------------------------------------------------------------
// 9. IDB read-before-write guard (last line of defense)
// ---------------------------------------------------------------------------
describe('IDB read-before-write guard', () => {
    // Simulates the guard logic inside db._saveConversationsWithLock:
    // before writing, read IDB state and preserve messages where the
    // incoming version would reduce message count.
    function applyWriteGuard(idbConversations, incomingConversations) {
        const idbMsgCounts = new Map();
        for (const c of idbConversations) {
            if (c && c.id && c.messages && c.messages.length) {
                idbMsgCounts.set(c.id, { count: c.messages.length, msgs: c.messages });
            }
        }
        const result = incomingConversations.map(function(conv) {
            var prev = idbMsgCounts.get(conv.id);
            if (!prev) return Object.assign({}, conv);
            var nextCount = (conv.messages && conv.messages.length) || 0;
            if (prev.count > nextCount && prev.count > 2) {
                return Object.assign({}, conv, { messages: prev.msgs });
            }
            return Object.assign({}, conv);
        });
        return result;
    }

    it('preserves IDB messages when caller has fewer (TOCTOU race)', () => {
        const idb = [makeConversation('a', 100, { _version: 2000 })];
        const stale = [Object.assign(makeConversation('a', 95, { _version: 1500 }), { title: 'Renamed' })];

        const result = applyWriteGuard(idb, stale);

        expect(result[0].messages.length).toBe(100); // Messages preserved
        expect(result[0].title).toBe('Renamed'); // Metadata kept from caller
    });

    it('allows write when caller has more messages', () => {
        const idb = [makeConversation('a', 50, { _version: 1000 })];
        const incoming = [makeConversation('a', 60, { _version: 2000 })];

        const result = applyWriteGuard(idb, incoming);

        expect(result[0].messages.length).toBe(60);
    });

    it('allows write for new conversations not in IDB', () => {
        const idb = [makeConversation('a', 50)];
        const incoming = [makeConversation('a', 50), makeConversation('b', 10)];

        const result = applyWriteGuard(idb, incoming);

        expect(result.length).toBe(2);
        expect(result[1].messages.length).toBe(10);
    });

    it('allows regression for tiny conversations (shell threshold)', () => {
        const idb = [makeConversation('a', 2, { _version: 1000 })];
        const incoming = [makeConversation('a', 1, { _version: 2000 })];

        const result = applyWriteGuard(idb, incoming);

        expect(result[0].messages.length).toBe(1); // Allowed: IDB had <=2
    });

    it('handles empty IDB gracefully', () => {
        const result = applyWriteGuard([], [makeConversation('a', 10)]);
        expect(result[0].messages.length).toBe(10);
    });

    it('handles concurrent rename + message add race', () => {
        // queueSave wrote M1-M101 to IDB, then rename captured stale
        // React state with M1-M100 + new title
        const idb = [makeConversation('a', 101, { _version: 3000 })];
        const renameCall = [Object.assign(
            makeConversation('a', 100, { _version: 2500 }),
            { title: 'User Renamed This' }
        )];

        const result = applyWriteGuard(idb, renameCall);

        expect(result[0].messages.length).toBe(101); // IDB messages preserved
        expect(result[0].title).toBe('User Renamed This'); // Rename metadata kept
    });
});

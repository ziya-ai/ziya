/**
 * Tests for the SAVE_GUARD metadata merge logic in db.ts.
 *
 * When a "shell" conversation (lazy-loaded with fewer messages than the full
 * record in IDB) is saved, the SAVE_GUARD blocks the full write to prevent
 * message data loss.  However, metadata-only changes (folderId, _version,
 * lastAccessedAt, groupId, isGlobal) must still be persisted.
 *
 * These tests validate the classification logic and the metadata merge
 * contract without requiring a real IndexedDB instance.
 */

// ---------------------------------------------------------------------------
// Types mirroring the Conversation shape relevant to the save guard
// ---------------------------------------------------------------------------
interface MinimalConversation {
    id: string;
    folderId?: string | null;
    groupId?: string | null;
    isGlobal?: boolean;
    _version?: number;
    _isShell?: boolean;
    _fullMessageCount?: number;
    lastAccessedAt?: number;
    messages: { id: string; content: string; _timestamp?: number }[];
}

// ---------------------------------------------------------------------------
// Extracted logic from db.ts _saveConversationsWithLock — the classification
// step that decides which conversations are saved normally vs. queued for
// metadata-only merge.
// ---------------------------------------------------------------------------
function classifySaveGuard(conversations: MinimalConversation[]) {
    const deduped = new Map<string, MinimalConversation>();
    const shellMetadataUpdates = new Map<string, MinimalConversation>();

    conversations.forEach(conv => {
        if (conv._isShell) {
            const fullCount = conv._fullMessageCount || 0;
            if (conv.messages.length < fullCount) {
                shellMetadataUpdates.set(conv.id, conv);
                return;
            }
        }

        const existing = deduped.get(conv.id);
        if (!existing) {
            deduped.set(conv.id, conv);
        } else if (conv._isShell && !existing._isShell) {
            // Never let a shell overwrite a non-shell entry in the same batch
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
    });

    return {
        toSave: Array.from(deduped.values()),
        toMergeMetadata: Array.from(shellMetadataUpdates.values()),
    };
}

// ---------------------------------------------------------------------------
// Extracted logic: apply metadata from a shell onto an existing IDB record.
// This mirrors the metaTx loop in db.ts.
// ---------------------------------------------------------------------------
function mergeShellMetadata(
    existing: MinimalConversation,
    shell: MinimalConversation
): MinimalConversation {
    return {
        ...existing,
        folderId: shell.folderId,
        _version: shell._version || existing._version,
        lastAccessedAt: shell.lastAccessedAt || existing.lastAccessedAt,
        groupId: shell.groupId !== undefined ? shell.groupId : existing.groupId,
        isGlobal: shell.isGlobal !== undefined ? shell.isGlobal : existing.isGlobal,
    };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('SAVE_GUARD shell metadata merge', () => {
    const CONV_ID = '230135e2-fbd6-40fb-9b46-67874f638910';
    const OLD_FOLDER = '076f5776-6a27-4b77-a84d-77a077792b08';
    const NEW_FOLDER = '8925d6cd-76e9-4a43-bd55-23e8b615776d';

    // A shell conversation: 2 messages loaded, but the full record has 116
    const makeShell = (overrides?: Partial<MinimalConversation>): MinimalConversation => ({
        id: CONV_ID,
        folderId: NEW_FOLDER,
        _version: Date.now(),
        _isShell: true,
        _fullMessageCount: 116,
        lastAccessedAt: Date.now(),
        messages: [
            { id: 'm1', content: 'hello' },
            { id: 'm2', content: 'world' },
        ],
        ...overrides,
    });

    // The full IDB record with all 116 messages
    const makeFullRecord = (overrides?: Partial<MinimalConversation>): MinimalConversation => ({
        id: CONV_ID,
        folderId: OLD_FOLDER,
        _version: 1775838353883,
        lastAccessedAt: 1775838000000,
        messages: Array.from({ length: 116 }, (_, i) => ({
            id: `msg-${i}`,
            content: `message ${i}`,
        })),
        ...overrides,
    });

    // -----------------------------------------------------------------------
    // Classification tests
    // -----------------------------------------------------------------------
    describe('classifySaveGuard', () => {
        it('routes shell conversations to metadata-only merge', () => {
            const shell = makeShell();
            const result = classifySaveGuard([shell]);

            expect(result.toSave).toHaveLength(0);
            expect(result.toMergeMetadata).toHaveLength(1);
            expect(result.toMergeMetadata[0].id).toBe(CONV_ID);
        });

        it('allows non-shell conversations through normally', () => {
            const full = makeFullRecord();
            const result = classifySaveGuard([full]);

            expect(result.toSave).toHaveLength(1);
            expect(result.toMergeMetadata).toHaveLength(0);
        });

        it('allows shell conversations where message count matches full count', () => {
            const shell = makeShell({
                _fullMessageCount: 2,  // matches messages.length
            });
            const result = classifySaveGuard([shell]);

            expect(result.toSave).toHaveLength(1);
            expect(result.toMergeMetadata).toHaveLength(0);
        });

        it('allows shell conversations where _fullMessageCount is 0 (unknown)', () => {
            const shell = makeShell({ _fullMessageCount: 0 });
            const result = classifySaveGuard([shell]);

            // fullCount=0, messages.length=2, 2 < 0 is false => goes to deduped
            expect(result.toSave).toHaveLength(1);
            expect(result.toMergeMetadata).toHaveLength(0);
        });

        it('handles mixed batch: one shell, one full', () => {
            const shell = makeShell();
            const full = makeFullRecord({ id: 'other-conv-id' });
            const result = classifySaveGuard([shell, full]);

            expect(result.toSave).toHaveLength(1);
            expect(result.toSave[0].id).toBe('other-conv-id');
            expect(result.toMergeMetadata).toHaveLength(1);
            expect(result.toMergeMetadata[0].id).toBe(CONV_ID);
        });

        it('dedup: non-shell wins over shell for same id in batch', () => {
            const full = makeFullRecord();
            const shell: MinimalConversation = {
                ...makeShell(),
                _isShell: true,
                _fullMessageCount: 2,  // passes the guard (messages.length == fullCount)
            };
            // Full first, then shell tries to overwrite
            const result = classifySaveGuard([full, shell]);

            expect(result.toSave).toHaveLength(1);
            expect(result.toSave[0].messages).toHaveLength(116);
        });
    });

    // -----------------------------------------------------------------------
    // Metadata merge tests
    // -----------------------------------------------------------------------
    describe('mergeShellMetadata', () => {
        it('updates folderId on existing IDB record', () => {
            const existing = makeFullRecord();
            const shell = makeShell({ folderId: NEW_FOLDER });

            const merged = mergeShellMetadata(existing, shell);

            expect(merged.folderId).toBe(NEW_FOLDER);
            // Messages must be preserved from the existing record
            expect(merged.messages).toHaveLength(116);
        });

        it('clears folderId when shell has null', () => {
            const existing = makeFullRecord({ folderId: OLD_FOLDER });
            const shell = makeShell({ folderId: null });

            const merged = mergeShellMetadata(existing, shell);
            expect(merged.folderId).toBeNull();
        });

        it('updates _version from shell', () => {
            const existing = makeFullRecord({ _version: 100 });
            const shell = makeShell({ _version: 200 });

            const merged = mergeShellMetadata(existing, shell);
            expect(merged._version).toBe(200);
        });

        it('preserves existing _version when shell has no version', () => {
            const existing = makeFullRecord({ _version: 100 });
            const shell = makeShell({ _version: undefined });

            const merged = mergeShellMetadata(existing, shell);
            expect(merged._version).toBe(100);
        });

        it('updates groupId from shell', () => {
            const existing = makeFullRecord({ groupId: 'old-group' });
            const shell = makeShell({ groupId: 'new-group' });

            const merged = mergeShellMetadata(existing, shell);
            expect(merged.groupId).toBe('new-group');
        });

        it('preserves existing groupId when shell groupId is undefined', () => {
            const existing = makeFullRecord({ groupId: 'keep-me' });
            const shell = makeShell({ groupId: undefined });

            const merged = mergeShellMetadata(existing, shell);
            expect(merged.groupId).toBe('keep-me');
        });

        it('updates isGlobal from shell', () => {
            const existing = makeFullRecord({ isGlobal: false });
            const shell = makeShell({ isGlobal: true });

            const merged = mergeShellMetadata(existing, shell);
            expect(merged.isGlobal).toBe(true);
        });

        it('preserves all messages from existing record', () => {
            const existing = makeFullRecord();
            const shell = makeShell();

            const merged = mergeShellMetadata(existing, shell);
            expect(merged.messages).toHaveLength(116);
            expect(merged.messages[0].id).toBe('msg-0');
        });
    });

    // -----------------------------------------------------------------------
    // Integration: classify then merge
    // -----------------------------------------------------------------------
    describe('end-to-end: classify + merge', () => {
        it('shell folderId change is preserved through classify+merge pipeline', () => {
            const shell = makeShell({ folderId: NEW_FOLDER });
            const existingIdb = makeFullRecord({ folderId: OLD_FOLDER });

            // Step 1: classify — shell is routed to metadata merge
            const { toSave, toMergeMetadata } = classifySaveGuard([shell]);
            expect(toSave).toHaveLength(0);
            expect(toMergeMetadata).toHaveLength(1);

            // Step 2: merge metadata onto existing IDB record
            const merged = mergeShellMetadata(existingIdb, toMergeMetadata[0]);

            // folderId updated, messages preserved
            expect(merged.folderId).toBe(NEW_FOLDER);
            expect(merged.messages).toHaveLength(116);
        });

        it('reproduces the original bug scenario without the fix', () => {
            // Before the fix, the shell was simply dropped with no metadata merge.
            // Simulating that: classify routes to metadata, but if we skip the
            // merge step, the existing IDB record retains the old folderId.
            const shell = makeShell({ folderId: NEW_FOLDER });
            const existingIdb = makeFullRecord({ folderId: OLD_FOLDER });

            const { toMergeMetadata } = classifySaveGuard([shell]);
            expect(toMergeMetadata).toHaveLength(1);

            // Without merge: IDB still has old folder
            expect(existingIdb.folderId).toBe(OLD_FOLDER);

            // With merge: IDB gets updated
            const merged = mergeShellMetadata(existingIdb, toMergeMetadata[0]);
            expect(merged.folderId).toBe(NEW_FOLDER);
        });
    });
});

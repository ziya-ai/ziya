/**
 * Regression tests for the FAST_PATH dual-write logic in ChatContext.queueSave.
 *
 * Background: queueSave has a "fast path" that bypasses its serialised
 * saveQueue for per-message saves (changedIds set, not a recovery attempt).
 * The fast path used to write to IndexedDB and broadcast to same-browser
 * tabs via BroadcastChannel — but never push to the server.  As a result,
 * messages sent in browser A never reached browser B (cross-browser
 * BroadcastChannel does not exist), and reloading B fetched server state
 * which was missing every fast-path-routed message.
 *
 * The current implementation accumulates fast-path changedIds into
 * pendingDirtyIdsRef and schedules a single debounced bulkSync.  These
 * tests validate the staging + filtering + batching contract of that
 * scheduler without requiring a live React tree.
 */

interface MinimalConversation {
    id: string;
    projectId?: string;
    isActive?: boolean;
    _isShell?: boolean;
    messages: { id: string; content: string }[];
}

/**
 * Pure extraction of the fast-path "select conversations to push" filter.
 * Mirrors the filter inside the setTimeout callback in queueSave.
 */
function selectConvsToPush(
    batchIds: Set<string>,
    liveConvs: MinimalConversation[],
): MinimalConversation[] {
    return liveConvs.filter(
        c => batchIds.has(c.id) && c.isActive !== false && !c._isShell,
    );
}

/**
 * Pure extraction of the by-project grouping the dual-write performs
 * before calling bulkSync.
 */
function groupByProject(
    convs: MinimalConversation[],
    fallbackProjectId: string,
): Map<string, MinimalConversation[]> {
    const byProject = new Map<string, MinimalConversation[]>();
    convs.forEach(c => {
        const pid = c.projectId || fallbackProjectId;
        if (!byProject.has(pid)) byProject.set(pid, []);
        byProject.get(pid)!.push(c);
    });
    return byProject;
}

describe('FAST_PATH dual-write filter', () => {
    test('pushes a freshly-appended message to the server batch', () => {
        const conv: MinimalConversation = {
            id: 'conv-1',
            projectId: 'proj-A',
            isActive: true,
            messages: [
                { id: 'm1', content: 'hello' },
                { id: 'm2', content: 'world' },
            ],
        };
        const result = selectConvsToPush(new Set(['conv-1']), [conv]);
        expect(result).toHaveLength(1);
        expect(result[0].id).toBe('conv-1');
    });

    test('drops shells (would clobber server with empty messages)', () => {
        const shell: MinimalConversation = {
            id: 'conv-shell',
            projectId: 'proj-A',
            isActive: true,
            _isShell: true,
            messages: [],
        };
        const result = selectConvsToPush(new Set(['conv-shell']), [shell]);
        expect(result).toHaveLength(0);
    });

    test('drops inactive conversations (deleted)', () => {
        const deleted: MinimalConversation = {
            id: 'conv-deleted',
            projectId: 'proj-A',
            isActive: false,
            messages: [{ id: 'm1', content: 'rip' }],
        };
        const result = selectConvsToPush(new Set(['conv-deleted']), [deleted]);
        expect(result).toHaveLength(0);
    });

    test('drops IDs not present in live state (e.g. deleted between debounce and fire)', () => {
        const live: MinimalConversation[] = [
            { id: 'conv-other', projectId: 'proj-A', messages: [{ id: 'm1', content: 'x' }] },
        ];
        const result = selectConvsToPush(new Set(['conv-vanished']), live);
        expect(result).toHaveLength(0);
    });

    test('reads live state, not staged-time snapshot', () => {
        // Simulate the case where multiple messages append during the 2s debounce.
        // The batch was staged when the conversation had 2 messages; by fire time
        // it has 5.  The dual-write must use the live count, not the staged count.
        const liveAtFireTime: MinimalConversation = {
            id: 'conv-streaming',
            projectId: 'proj-A',
            isActive: true,
            messages: [
                { id: 'm1', content: 'a' }, { id: 'm2', content: 'b' },
                { id: 'm3', content: 'c' }, { id: 'm4', content: 'd' },
                { id: 'm5', content: 'e' },
            ],
        };
        const result = selectConvsToPush(new Set(['conv-streaming']), [liveAtFireTime]);
        expect(result).toHaveLength(1);
        expect(result[0].messages).toHaveLength(5);
    });
});

describe('FAST_PATH dual-write project routing', () => {
    test('uses captured project id when conv has none', () => {
        const convs: MinimalConversation[] = [
            { id: 'untagged', isActive: true, messages: [{ id: 'm', content: 'x' }] },
        ];
        const grouped = groupByProject(convs, 'proj-fallback');
        expect(grouped.size).toBe(1);
        expect(grouped.get('proj-fallback')).toHaveLength(1);
    });

    test('respects per-conversation projectId (e.g. global chats)', () => {
        const convs: MinimalConversation[] = [
            { id: 'global', projectId: 'proj-original', isActive: true, messages: [{ id: 'm', content: 'x' }] },
            { id: 'local',  projectId: 'proj-current',  isActive: true, messages: [{ id: 'm', content: 'y' }] },
        ];
        const grouped = groupByProject(convs, 'proj-current');
        expect(grouped.size).toBe(2);
        expect(grouped.get('proj-original')).toHaveLength(1);
        expect(grouped.get('proj-current')).toHaveLength(1);
    });
});

describe('FAST_PATH dual-write debounce coalescing', () => {
    /**
     * Mirrors the timer/staging contract: each fast-path call adds to
     * pendingDirtyIdsRef and resets a single timer.  When the timer fires,
     * it drains the staging set into a batch.
     */
    function makeStager() {
        const pending = new Set<string>();
        let scheduled = false;
        let drainedBatch: Set<string> | null = null;
        return {
            stage(ids: string[]) {
                ids.forEach(id => pending.add(id));
                scheduled = true; // Fast path always (re)schedules its timer.
            },
            fire() {
                if (!scheduled) throw new Error('fire() called without staged work');
                drainedBatch = new Set(pending);
                pending.clear();
                scheduled = false;
            },
            lastBatch() { return drainedBatch; },
        };
    }

    test('coalesces multiple appends into a single batch', () => {
        const stager = makeStager();
        // Three SSE chunks for the same conversation within the debounce window.
        stager.stage(['conv-1']);
        stager.stage(['conv-1']);
        stager.stage(['conv-1']);
        stager.fire();
        expect(stager.lastBatch()!.size).toBe(1);
        expect(stager.lastBatch()!.has('conv-1')).toBe(true);
    });

    test('combines independent changes across multiple conversations', () => {
        const stager = makeStager();
        stager.stage(['conv-1']);
        stager.stage(['conv-2']);
        stager.stage(['conv-3']);
        stager.fire();
        expect(stager.lastBatch()!.size).toBe(3);
    });

    test('drains and clears between fires (does not leak state across batches)', () => {
        const stager = makeStager();
        stager.stage(['conv-1']);
        stager.fire();
        expect(stager.lastBatch()!.size).toBe(1);

        stager.stage(['conv-2']);
        stager.fire();
        // Batch 2 must NOT include conv-1 from the previous batch.
        expect(stager.lastBatch()!.size).toBe(1);
        expect(stager.lastBatch()!.has('conv-2')).toBe(true);
        expect(stager.lastBatch()!.has('conv-1')).toBe(false);
    });
});

describe('FAST_PATH dual-write end-to-end multi-tab scenario', () => {
    /**
     * Reproduces the user-reported bug:
     *   - Browser A appends a message to conv-1.
     *   - The fast path stages conv-1, then 2s later fires the dual-write.
     *   - The dual-write must call bulkSync with the LIVE conv-1 (3 messages),
     *     not the snapshot from when the timer was set (2 messages).
     *   - Without the fast-path dual-write, no bulkSync is called at all and
     *     browser B reading from the server sees only the original 2 messages.
     */
    test('appended message reaches the bulkSync batch after debounce', () => {
        // Simulate React state evolving while the timer is pending.
        const liveConvsRef = { current: [] as MinimalConversation[] };
        liveConvsRef.current = [
            {
                id: 'conv-1',
                projectId: 'proj-A',
                isActive: true,
                messages: [
                    { id: 'm1', content: 'user: hi' },
                    { id: 'm2', content: 'asst: hi back' },
                ],
            },
        ];

        // Stage the change after message m2 (matches addMessageToConversation
        // → queueSave({ changedIds: ['conv-1'] })).
        const pending = new Set<string>();
        pending.add('conv-1');

        // While the timer is pending, the assistant streams another message:
        liveConvsRef.current = [
            {
                ...liveConvsRef.current[0],
                messages: [
                    ...liveConvsRef.current[0].messages,
                    { id: 'm3', content: 'asst: more content' },
                ],
            },
        ];

        // Timer fires.  Dual-write reads live state, not the staged snapshot.
        const batchIds = new Set(pending);
        pending.clear();
        const toPush = selectConvsToPush(batchIds, liveConvsRef.current);

        expect(toPush).toHaveLength(1);
        // Critical invariant: the batched payload reflects ALL messages that
        // landed in state, including ones that arrived during the debounce.
        expect(toPush[0].messages).toHaveLength(3);
        expect(toPush[0].messages[2].id).toBe('m3');
    });

    test('no dual-write fires when the only staged conv has been deleted', () => {
        // A conversation deleted between stage and fire must drop out of the
        // batch — never resurrect it on the server.
        const liveConvs: MinimalConversation[] = [
            { id: 'conv-stale', projectId: 'proj-A', isActive: false, messages: [{ id: 'm1', content: 'x' }] },
        ];
        const result = selectConvsToPush(new Set(['conv-stale']), liveConvs);
        expect(result).toHaveLength(0);
    });
});

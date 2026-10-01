/**
 * Regression: edit-and-resubmit in an OLDER conversation (early messages
 * predate per-message ids) left IndexedDB holding the pre-edit tail forever.
 *
 * Observed (conv e04a626a): state/server at 13 messages after the edit, IDB
 * still at 18.  Every later reply streamed, was added to state (14), and
 * then vanished:
 *   🛡️ FAST_PATH_TOMBSTONE: Preserving 18 messages (caller had 14)
 *   📡 SERVER_SYNC: msg-count divergence (local=18 server=13) — pushing
 * followed by the sync commit replacing state with the stale 18.
 *
 * Two client gaps, both fixed by mirroring the server's content
 * fingerprints (app/utils/chat_message_ops.py):
 *   1. addTombstones only recorded ids, so dropped id-less messages were
 *      never tombstoned.
 *   2. shrinkExplainedByTombstones returned false on ANY id-less message in
 *      the existing record, even one that was kept.
 *
 * DIRECTION: against the unpatched code `messageFingerprint` does not exist
 * and shouldPreserveExistingMessages returns true for the id-less case.
 */
import {
    addTombstones,
    droppedMessages,
    growthExplainedByTombstones,
    shouldPreserveExistingMessages,
    shouldRehydrateActiveFromFuller,
    tombstonedFps,
    TOMBSTONE_FIELD,
} from '../syncMerge';
import { messageFingerprint } from '../messageFingerprint';

const role = (i: number) => (i % 2 ? 'assistant' : 'human');

/** 18 messages; the first 10 predate id minting (no id), the rest have uuids. */
const legacy = Array.from({ length: 18 }, (_, i) =>
    i < 10 ? { role: role(i), content: `m${i}` } : { id: `id-${i}`, role: role(i), content: `m${i}` });

/** replaceMessages after editing message 8 (id-less) and resubmitting:
 *  truncate to 9 messages, edited copy in place, plus the streamed reply. */
function editAndResubmit(before: any[], editIdx: number) {
    const truncated = before.slice(0, editIdx + 1);
    truncated[editIdx] = { ...truncated[editIdx], content: 'edited', _edited: true };
    const base = { id: 'c1', messages: before, _version: 1 };
    const dropped = droppedMessages(before, truncated);
    return {
        record: {
            ...base,
            messages: truncated,
            [TOMBSTONE_FIELD]: addTombstones(base, dropped),
            _version: 2,
        },
        dropped,
    };
}

describe('droppedMessages — identity by id when real, by content otherwise', () => {
    it('reports the truncated tail AND the old content of an id-less edited message', () => {
        const { dropped } = editAndResubmit(legacy, 8);
        // Tail m9..m17 (9 messages) plus the pre-edit m8.
        expect(dropped.map(m => m.content).sort()).toEqual(
            ['m8', ...Array.from({ length: 9 }, (_, i) => `m${i + 9}`)].sort());
    });

    it('does not report an id-ful message edited in place (same id, new content)', () => {
        const before = [{ id: 'a', role: 'human', content: 'x' }, { id: 'b', role: 'assistant', content: 'y' }];
        const after = [{ id: 'a', role: 'human', content: 'x2' }];
        expect(droppedMessages(before, after).map(m => m.id)).toEqual(['b']);
    });

    it('treats a push-minted msg- id as no identity', () => {
        const before = [{ id: 'msg-1-aa', role: 'human', content: 'x' }];
        expect(droppedMessages(before, [{ id: 'msg-2-bb', role: 'human', content: 'x' }])).toEqual([]);
        expect(droppedMessages(before, [{ id: 'msg-2-bb', role: 'human', content: 'z' }])).toHaveLength(1);
    });
});

describe('addTombstones writes server-compatible fingerprints', () => {
    it('records an fp for every removed message, and ids only for real ids', () => {
        const removed = [{ role: 'human', content: 'old' }, { id: 'id-3', role: 'assistant', content: 'r' }];
        const t = addTombstones({}, removed);
        expect(t.ids).toEqual(['id-3']);
        expect(new Set(t.fps)).toEqual(new Set(removed.map(messageFingerprint)));
        expect(tombstonedFps({ [TOMBSTONE_FIELD]: t })).toEqual(new Set(removed.map(messageFingerprint)));
    });

    it('carries existing server fps through', () => {
        const prev = { ids: [], fps: ['serverfp'] };
        const t = addTombstones({ [TOMBSTONE_FIELD]: prev }, [{ role: 'human', content: 'x' }]);
        expect(t.fps).toContain('serverfp');
    });
});

describe('shouldPreserveExistingMessages — the reported case (id-less early messages)', () => {
    it('lets the edit-and-resubmit shrink through: IDB may drop the pre-edit tail', () => {
        const { record } = editAndResubmit(legacy, 8);
        expect(shouldPreserveExistingMessages(legacy, record)).toBe(false);
    });

    it('still lets the shrink through once the streamed reply has been appended (18 → 10)', () => {
        const { record } = editAndResubmit(legacy, 8);
        const withReply = { ...record, messages: [...record.messages, { id: 'id-new', role: 'assistant', content: 'answer' }] };
        expect(shouldPreserveExistingMessages(legacy, withReply)).toBe(false);
    });

    it('honours fps written by the SERVER rewind, not just the client', () => {
        // Server-style record, as _add_tombstones writes it: an id for each
        // removed message that has one, a fingerprint for every one.  The
        // id-less m9 is explainable ONLY through its fingerprint.
        const truncated = legacy.slice(0, 9);
        const removed = legacy.slice(9);
        const serverRecord = {
            id: 'c1', messages: truncated,
            [TOMBSTONE_FIELD]: {
                ids: removed.map(m => m.id).filter(Boolean),
                fps: removed.map(messageFingerprint),
            },
        };
        expect(shouldPreserveExistingMessages(legacy, serverRecord)).toBe(false);
        // Positive control: without the fps the id-less m9 is unexplained.
        const idsOnly = { ...serverRecord, [TOMBSTONE_FIELD]: { ids: removed.map(m => m.id).filter(Boolean), fps: [] } };
        expect(shouldPreserveExistingMessages(legacy, idsOnly)).toBe(true);
    });

    it('a real-id message is never explained by a fingerprint alone (a repeated "continue" must not be swallowed)', () => {
        const older = { role: 'human', content: 'continue' };            // id-less, deleted earlier
        const newer = { id: 'id-c', role: 'human', content: 'continue' }; // real id, still live
        const existing = [...legacy.slice(0, 3), newer];
        const shorter = { messages: legacy.slice(0, 3), [TOMBSTONE_FIELD]: { ids: [], fps: [messageFingerprint(older)] } };
        expect(shouldPreserveExistingMessages(existing, shorter)).toBe(true);
    });

    it('a caller that is a plain stale/partial copy of id-less history is still rejected', () => {
        // No tombstones at all — this is the shell/partial case the guard exists for.
        expect(shouldPreserveExistingMessages(legacy, { messages: legacy.slice(0, 9) })).toBe(true);
        // Tombstones that do not cover one of the missing id-less messages.
        const partial = { ids: [], fps: legacy.slice(10).map(messageFingerprint) }; // m9 missing
        expect(shouldPreserveExistingMessages(legacy, { messages: legacy.slice(0, 9), [TOMBSTONE_FIELD]: partial })).toBe(true);
    });

    it('matches existing msg- placeholder ids by content against an id-less caller copy', () => {
        const existing = legacy.map((m, i) => i < 10 ? { ...m, id: `msg-170000-${i}` } : m);
        const { record } = editAndResubmit(legacy, 8);
        expect(shouldPreserveExistingMessages(existing, record)).toBe(false);
    });
});

describe('post-sync rehydration respects fingerprint tombstones too', () => {
    it('does not re-adopt the longer stale copy after an id-less edit', () => {
        const { record } = editAndResubmit(legacy, 8);
        const local = { ...record, _version: 2 };
        const stale = { id: 'c1', messages: legacy, _version: 2 }; // same version, longer
        expect(growthExplainedByTombstones(local, legacy)).toBe(true);
        expect(shouldRehydrateActiveFromFuller(local, stale, { isStreaming: false })).toBe(false);
    });
});

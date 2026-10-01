/**
 * Edit-and-resubmit near the top of a long conversation must not have the
 * removed tail restored by the post-sync active-conversation rehydration.
 *
 * Observed defect (2026-09-30): editing the FIRST message of a long
 * conversation and pressing Submit truncated the local list to one message
 * (correctly — the request was built from the truncated list), but the UI
 * then showed the whole old history with the new answer appended.
 *
 * Mechanism: syncWithServer's `finally` block rehydrates the active
 * conversation whenever it holds <= 2 messages, adopting any LONGER copy it
 * finds in IndexedDB (the debounced save of the truncation may not have
 * flushed) or on the server (the rewind may not have landed, or the poll
 * fetched before it).  That `finally` also runs on the early "streaming in
 * progress" return, so it fired mid-stream.  A deliberate shrink looked
 * exactly like an un-hydrated shell.
 *
 * DIRECTION: `shouldRehydrateActiveFromFuller` does not exist before the
 * fix, so this file fails to compile on the unpatched tree.  The seam test
 * fails if the helper exists but ChatContext still adopts on bare length.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    addTombstones,
    growthExplainedByTombstones,
    shouldRehydrateActiveFromFuller,
    TOMBSTONE_FIELD,
} from '../syncMerge';

const msg = (id: string, i: number) => ({ id, role: i % 2 ? 'assistant' : 'human', content: `m${i}` });
const full = Array.from({ length: 12 }, (_, i) => msg(`m${i}`, i));

/** What replaceMessages leaves in React state after editing message 0 and submitting. */
function truncatedRecord(version: number) {
    const kept = [{ ...full[0], content: 'edited', _edited: true, _truncatedAfter: true }];
    const base = { id: 'c1', messages: full, _version: version - 1000 };
    return {
        ...base,
        messages: kept,
        [TOMBSTONE_FIELD]: addTombstones(base, full.slice(1)),
        _version: version,
    };
}

describe('shouldRehydrateActiveFromFuller — the reported case', () => {
    const NOW = 1_700_000_000_000;

    it('does NOT adopt the stale full IndexedDB copy after an edit-and-resubmit truncation', () => {
        const local = truncatedRecord(NOW);
        const idbCopy = { id: 'c1', messages: full, _version: NOW - 5_000 };
        expect(shouldRehydrateActiveFromFuller(local, idbCopy, { isStreaming: false })).toBe(false);
    });

    it('does NOT adopt the pre-rewind server copy either', () => {
        const local = truncatedRecord(NOW);
        const srv = { id: 'c1', messages: full, _version: NOW - 60_000 };
        expect(shouldRehydrateActiveFromFuller(local, srv, { isStreaming: false })).toBe(false);
    });

    it('never adopts anything while the active conversation is streaming (local state is authoritative)', () => {
        // Even a candidate that would otherwise win: newer version, no tombstones.
        const local = { id: 'c1', messages: full.slice(0, 1), _version: NOW - 10 };
        const cand = { id: 'c1', messages: full, _version: NOW };
        expect(shouldRehydrateActiveFromFuller(local, cand, { isStreaming: true })).toBe(false);
        expect(shouldRehydrateActiveFromFuller(local, cand, { isStreaming: false })).toBe(true);
    });

    it('tombstones alone block adoption even when the candidate carries a newer version', () => {
        // A sibling-tab save could stamp a later _version on the old body;
        // the ids we removed are still ours to keep removed.
        const local = truncatedRecord(NOW);
        const cand = { id: 'c1', messages: full, _version: NOW + 5 };
        expect(growthExplainedByTombstones(local, full)).toBe(true);
        expect(shouldRehydrateActiveFromFuller(local, cand, { isStreaming: false })).toBe(false);
    });

    // Positive controls: the rehydration this guard sits in front of still
    // works for the race it was written for.
    it('adopts a fuller copy for a genuine shell', () => {
        const local = { id: 'c1', messages: [], _isShell: true, _fullMessageCount: 12, _version: NOW };
        const cand = { id: 'c1', messages: full, _version: NOW };
        expect(shouldRehydrateActiveFromFuller(local, cand, { isStreaming: false })).toBe(true);
    });

    it('adopts a fuller copy for a clobbered lazy-load (same version, no tombstones, <= 2 messages)', () => {
        const local = { id: 'c1', messages: full.slice(0, 2), _version: NOW };
        const cand = { id: 'c1', messages: full, _version: NOW };
        expect(shouldRehydrateActiveFromFuller(local, cand, { isStreaming: false })).toBe(true);
    });

    it('is false when the candidate is missing or not longer', () => {
        const local = { id: 'c1', messages: full.slice(0, 2), _version: NOW };
        expect(shouldRehydrateActiveFromFuller(local, null, { isStreaming: false })).toBe(false);
        expect(shouldRehydrateActiveFromFuller(local, { messages: full.slice(0, 2) }, { isStreaming: false })).toBe(false);
        expect(shouldRehydrateActiveFromFuller(local, { messages: 'nope' }, { isStreaming: false })).toBe(false);
    });
});

describe('growthExplainedByTombstones', () => {
    it('is false when an extra message is neither present nor tombstoned locally (partial local copy)', () => {
        const local = { id: 'c1', messages: full.slice(0, 2), [TOMBSTONE_FIELD]: { ids: ['m5'], fps: [] } };
        expect(growthExplainedByTombstones(local, full)).toBe(false);
    });
    it('is false when the extra message has no id (cannot be tombstoned)', () => {
        const local = truncatedRecord(1);
        const fuller = [...local.messages, { role: 'assistant', content: 'no id' }];
        expect(growthExplainedByTombstones(local, fuller)).toBe(false);
    });
    it('is false with no tombstones at all', () => {
        expect(growthExplainedByTombstones({ messages: full.slice(0, 1) }, full)).toBe(false);
    });
});

describe('seam — ChatContext post-sync rehydration goes through the guard at BOTH hops', () => {
    const src = fs.readFileSync(
        path.join(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8',
    );
    const start = src.indexOf('Re-hydrate the active conversation if it');
    const block = src.slice(start, start + 6000);

    it('the block exists', () => {
        expect(start).toBeGreaterThan(-1);
    });

    it('IDB and server candidates are both gated by shouldRehydrateActiveFromFuller', () => {
        const calls = block.match(/syncMerge\.shouldRehydrateActiveFromFuller\(/g) || [];
        expect(calls.length).toBeGreaterThanOrEqual(2);
        // The old bare-length adoption conditions are gone.
        expect(block).not.toMatch(/fcMsgs\.length > \(activeConv\.messages\?\.length \|\| 0\)\s*&&\s*!isStale\(\)\)/);
        expect(block).not.toMatch(/srvMsgs\.length > localMsgCount && !isStale\(\)/);
    });

    it('passes the active conversation\'s streaming state into the guard', () => {
        expect(block).toMatch(/isStreaming:\s*streamingConversationsRef\.current\.has\(activeId\)/);
    });
});

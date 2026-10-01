/**
 * The IndexedDB message-count guards must let a DELIBERATE shrink through.
 *
 * Observed defect (2026-09-30): editing the first message of a long
 * conversation and pressing Submit truncated the list on screen for about a
 * second, then the whole old history came back.  `replaceMessages` had
 * written the truncation to state and queued the IDB save — but all three
 * IDB write paths (`FAST_PATH_TOMBSTONE`, `IDB_WRITE_GUARD`, `SAVE_GUARD` in
 * utils/db.ts) refuse any write that lowers a message count above 2: they
 * keep the EXISTING messages and stamp the INCOMING `_version`.  IDB then
 * held the 12 old messages under the truncation's version — a record both
 * longer and no older than state — and every IDB reader (post-sync
 * rehydration, cross-tab merge, lazy-load) adopted it.  The guards were
 * written before explicit delete/rewind existed, when a shorter caller list
 * could only mean a stale or partial copy.
 *
 * The rule: preserve the existing messages UNLESS every existing message the
 * caller lacks is tombstoned on the caller's record — the same test
 * decideCrossTabMerge already applies (shrinkExplainedByTombstones).
 *
 * DIRECTION: `shouldPreserveExistingMessages` does not exist before the fix,
 * so this file fails to compile on the unpatched tree.  The seam tests fail
 * if the helper exists but db.ts still decides on bare length at any of the
 * three sites.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    addTombstones,
    shouldPreserveExistingMessages,
    TOMBSTONE_FIELD,
} from '../syncMerge';

const msg = (id: string, i: number) => ({ id, role: i % 2 ? 'assistant' : 'human', content: `m${i}` });
const full = Array.from({ length: 12 }, (_, i) => msg(`m${i}`, i));

/** The record replaceMessages hands to queueSave after editing message 0 and submitting. */
function truncatedIncoming() {
    const base = { id: 'c1', messages: full, _version: 1 };
    return {
        ...base,
        messages: [{ ...full[0], content: 'edited' }],
        [TOMBSTONE_FIELD]: addTombstones(base, full.slice(1)),
        _version: 2,
    };
}

describe('shouldPreserveExistingMessages — the reported case', () => {
    it('lets an edit-and-resubmit truncation through (every dropped message is tombstoned)', () => {
        expect(shouldPreserveExistingMessages(full, truncatedIncoming())).toBe(false);
    });

    it('lets a single-message delete through', () => {
        const base = { id: 'c1', messages: full };
        const incoming = {
            ...base,
            messages: full.filter(m => m.id !== 'm5'),
            [TOMBSTONE_FIELD]: addTombstones(base, [full[5]]),
        };
        expect(shouldPreserveExistingMessages(full, incoming)).toBe(false);
    });

    // Positive controls: the data-loss protection the guards were written for.
    it('still preserves when a shorter caller carries no tombstones (stale/partial copy)', () => {
        expect(shouldPreserveExistingMessages(full, { messages: full.slice(0, 1) })).toBe(true);
    });

    it('still preserves when the caller tombstones only SOME of the missing messages', () => {
        const base = { id: 'c1', messages: full };
        const incoming = {
            ...base,
            messages: full.slice(0, 1),
            [TOMBSTONE_FIELD]: addTombstones(base, full.slice(1, 6)),
        };
        expect(shouldPreserveExistingMessages(full, incoming)).toBe(true);
    });

    it('still preserves when a missing existing message has no id (cannot be proven deleted)', () => {
        const existing = [...full, { role: 'assistant', content: 'legacy, no id' }];
        expect(shouldPreserveExistingMessages(existing, truncatedIncoming())).toBe(true);
    });

    it('keeps the original thresholds: never preserves when the caller is not shorter or existing <= 2', () => {
        expect(shouldPreserveExistingMessages(full, { messages: full })).toBe(false);
        expect(shouldPreserveExistingMessages(full.slice(0, 2), { messages: [] })).toBe(false);
        expect(shouldPreserveExistingMessages(undefined, { messages: [] })).toBe(false);
        expect(shouldPreserveExistingMessages(full, { messages: 'nope' })).toBe(true);
    });
});

describe('seam — every IDB write guard decides through shouldPreserveExistingMessages', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'db.ts'), 'utf8');

    it('imports the helper from syncMerge', () => {
        expect(src).toMatch(/import\s*\{[^}]*shouldPreserveExistingMessages[^}]*\}\s*from\s*'\.\/syncMerge'/);
    });

    it('all three guard sites call it, and none decides on bare length any more', () => {
        const calls = src.match(/shouldPreserveExistingMessages\(/g) || [];
        expect(calls.length).toBe(3);
        // FAST_PATH_TOMBSTONE
        expect(src).not.toMatch(/existingLen > callerLen && existingLen > 2\)/);
        // IDB_WRITE_GUARD
        expect(src).not.toMatch(/existing\.messages\?\.length > conv\.messages\?\.length\s*&&\s*existing\.messages\.length > 2/);
        // SAVE_GUARD (single-record path)
        expect(src).not.toMatch(/existing\?\.messages\?\.length > conversation\.messages\?\.length\s*&&\s*existing\.messages\.length > 2/);
    });

    it('the three guard labels are still present (the protection itself was not removed)', () => {
        for (const label of ['FAST_PATH_TOMBSTONE: Preserving', 'IDB_WRITE_GUARD: Preserving', 'SAVE_GUARD: Preserving']) {
            expect(src).toContain(label);
        }
    });
});

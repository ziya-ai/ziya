/**
 * Deleted / rewound messages must not come back through the client-side
 * count guards.
 *
 * WHY THIS EXISTS
 *
 * decideCrossTabMerge keeps a LOCAL message list when another tab's newer
 * IndexedDB record has fewer messages, to survive stale or partial copies.
 * It predates explicit delete/rewind, so a deliberate shrink looked exactly
 * like a stale copy: tab A deletes a message, tab B keeps it and writes it
 * back on its next save.  And shouldFetchFull never re-pulled a server
 * record that had shrunk once "already fetched", so a delete made in another
 * browser left this one pushing the removed message every 30s
 * ("msg-count divergence (local=3 server=2) — pushing") while the server's
 * tombstone dropped it each time.
 *
 * The rule under test: a shorter remote list is adopted only when every
 * local message it lacks is tombstoned on the remote record.  Anything else
 * keeps the old protective behavior.
 */
import {
    decideCrossTabMerge,
    shouldFetchFull,
    shrinkExplainedByTombstones,
    addTombstones,
    tombstonedIds,
    TOMBSTONE_FIELD,
} from '../syncMerge';
import { messageFingerprint } from '../messageFingerprint';

const NOW = 1_750_000_000_000;
const msgs = (...ids: string[]) => ids.map(id => ({ id, role: 'human', content: id }));
const tomb = (...ids: string[]) => ({ [TOMBSTONE_FIELD]: { ids, fps: [] } });

describe('shrinkExplainedByTombstones', () => {
    it('true when every missing local message is tombstoned on the remote', () => {
        expect(shrinkExplainedByTombstones(msgs('a', 'b', 'c'),
            { messages: msgs('a', 'c'), ...tomb('b') })).toBe(true);
    });

    it('false when a missing message is NOT tombstoned (stale/partial remote)', () => {
        expect(shrinkExplainedByTombstones(msgs('a', 'b', 'c', 'd'),
            { messages: msgs('a', 'c'), ...tomb('b') })).toBe(false);
    });

    it('false when the remote has no tombstones at all', () => {
        expect(shrinkExplainedByTombstones(msgs('a', 'b', 'c'), { messages: msgs('a') })).toBe(false);
    });

    it('an id-less local message is proven deleted only by a content fingerprint', () => {
        const idless = { role: 'human', content: 'x' };
        // Tombstoned by id only: the id-less message is unexplained → stale/partial.
        expect(shrinkExplainedByTombstones([...msgs('a', 'b'), idless],
            { messages: msgs('a'), ...tomb('b') })).toBe(false);
        // Its fingerprint tombstoned (as the server, and now the client, write): explained.
        expect(shrinkExplainedByTombstones([...msgs('a', 'b'), idless],
            { messages: msgs('a'), [TOMBSTONE_FIELD]: { ids: ['b'], fps: [messageFingerprint(idless)] } })).toBe(true);
        // An id-less message the remote still HAS (by content) never blocks.
        expect(shrinkExplainedByTombstones([idless, ...msgs('a', 'b')],
            { messages: [idless, ...msgs('a')], ...tomb('b') })).toBe(true);
    });
});

describe('addTombstones', () => {
    it('appends ids AND fingerprints of removed messages, keeping server fingerprints', () => {
        const rec = { [TOMBSTONE_FIELD]: { ids: ['old'], fps: ['fp1'] } };
        expect(addTombstones(rec, msgs('b'))).toEqual({
            ids: ['old', 'b'],
            fps: ['fp1', messageFingerprint(msgs('b')[0])],
        });
    });

    it('records a fingerprint for an id-less removed message; unchanged when nothing is removed', () => {
        expect(addTombstones({}, [{ content: 'x' }])).toEqual({
            ids: [],
            fps: [messageFingerprint({ content: 'x' })],
        });
        expect(addTombstones({ [TOMBSTONE_FIELD]: { ids: ['z'], fps: [] } }, [])).toEqual({ ids: ['z'], fps: [] });
        expect(tombstonedIds({ ...tomb('z') }).has('z')).toBe(true);
    });
});

describe('decideCrossTabMerge — deletes in another tab', () => {
    const atRest = { isStreaming: false };

    it('adopts a newer, shorter remote whose shrink is tombstoned (the resurrection bug)', () => {
        const local = { id: 'c', _version: 1, messages: msgs('a', 'b', 'c', 'd') };
        const remote = { id: 'c', _version: 2, messages: msgs('a', 'c', 'd'), ...tomb('b') };
        expect(decideCrossTabMerge(local, remote, atRest)).toEqual({ action: 'adopt-remote' });
    });

    it('still keeps local for a shorter remote with no tombstone (stale IDB copy)', () => {
        const local = { id: 'c', _version: 1, messages: msgs('a', 'b', 'c', 'd') };
        const remote = { id: 'c', _version: 2, messages: msgs('a', 'c', 'd') };
        expect(decideCrossTabMerge(local, remote, atRest)).toEqual({ action: 'keep-local' });
    });

    it('streaming still never takes remote messages, tombstoned or not', () => {
        const local = { id: 'c', _version: 1, messages: msgs('a', 'b', 'c', 'd') };
        const remote = { id: 'c', _version: 2, messages: msgs('a', 'c', 'd'), ...tomb('b') };
        expect(decideCrossTabMerge(local, remote, { isStreaming: true }))
            .toEqual({ action: 'adopt-metadata-only' });
    });
});

describe('shouldFetchFull — server shrank', () => {
    it('re-pulls a newer, shorter server record even if already fetched this session', () => {
        expect(shouldFetchFull(
            { id: 'c', messageCount: 2, _version: NOW },
            { id: 'c', _version: NOW - 60_000, messages: msgs('a', 'b', 'c') },
            { isActiveConv: false, alreadyFetchedThisSession: true, now: NOW },
        )).toBe(true);
    });

    it('positive control: equal counts + already fetched still skips', () => {
        expect(shouldFetchFull(
            { id: 'c', messageCount: 3, _version: NOW },
            { id: 'c', _version: NOW - 60_000, messages: msgs('a', 'b', 'c') },
            { isActiveConv: false, alreadyFetchedThisSession: true, now: NOW },
        )).toBe(false);
    });

    it('unknown server count is not treated as a shrink', () => {
        expect(shouldFetchFull(
            { id: 'c', _version: NOW },
            { id: 'c', _version: NOW - 60_000, messages: msgs('a', 'b', 'c') },
            { isActiveConv: false, alreadyFetchedThisSession: true, now: NOW },
        )).toBe(false);
    });
});

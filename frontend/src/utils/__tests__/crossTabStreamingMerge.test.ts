/**
 * Cross-tab merge behaviour during an active stream.
 *
 * Regression under test: a conversation-flag toggle in another tab calls
 * mutateConversationMeta, which stamps `_version: Date.now()` and posts
 * `conversations-changed`.  The receiving tab merged the (debounce-stale)
 * IndexedDB record into live state.  The `localMsgCount <= 2` escape hatch
 * meant a remote record with FEWER messages won, dropping the human turn
 * the in-flight response was answering.
 *
 * The periodic server sync already refuses to run while streaming; these
 * tests pin the equivalent protection on the broadcast path.
 */
import { decideCrossTabMerge, adoptMetadataOnly } from '../syncMerge';

const T0 = 1_750_000_000_000;

const conv = (over: any = {}) => ({
    id: 'conv-1',
    title: 'Hello',
    messages: [{ role: 'human', content: 'q' }],
    _version: T0,
    isActive: true,
    ...over,
});

const atRest = { isStreaming: false };
const streaming = { isStreaming: true };

describe('decideCrossTabMerge — version gate (unchanged at rest)', () => {
    it('adopts a remote record with no local counterpart', () => {
        expect(decideCrossTabMerge(undefined, conv(), atRest))
            .toEqual({ action: 'adopt-remote' });
    });

    it('keeps local when remote is older', () => {
        const local = conv({ _version: T0 + 1000 });
        const remote = conv({ _version: T0 });
        expect(decideCrossTabMerge(local, remote, atRest))
            .toEqual({ action: 'keep-local' });
    });

    it('keeps local when versions are equal (strict >)', () => {
        expect(decideCrossTabMerge(conv(), conv(), atRest))
            .toEqual({ action: 'keep-local' });
    });

    it('adopts a newer remote with an equal message count', () => {
        const remote = conv({ _version: T0 + 1000 });
        expect(decideCrossTabMerge(conv(), remote, atRest))
            .toEqual({ action: 'adopt-remote' });
    });

    it('keeps local when a newer remote would shrink a long history', () => {
        const local = conv({ messages: [1, 2, 3, 4, 5].map(n => ({ n })) });
        const remote = conv({ _version: T0 + 1000, messages: [{ n: 1 }] });
        expect(decideCrossTabMerge(local, remote, atRest))
            .toEqual({ action: 'keep-local' });
    });

    it('still honours the stub escape hatch at rest (localMsgCount <= 2)', () => {
        // A local 2-message stub may legitimately be replaced by an
        // authoritative remote record.  This is the behaviour the streaming
        // guard narrows — it must remain intact when nothing is in flight.
        const local = conv({ messages: [{ n: 1 }, { n: 2 }] });
        const remote = conv({ _version: T0 + 1000, messages: [] });
        expect(decideCrossTabMerge(local, remote, atRest))
            .toEqual({ action: 'adopt-remote' });
    });
});

describe('decideCrossTabMerge — streaming protection', () => {
    it('THE BUG: never adopts a shorter remote over a 2-message stream', () => {
        // Exactly the flag-toggle case: remote is newer (flag write bumped
        // _version) and local sits at the count the escape hatch permits.
        const local = conv({ messages: [{ n: 1 }, { n: 2 }] });
        const remote = conv({ _version: T0 + 1000, messages: [{ n: 1 }] });
        expect(decideCrossTabMerge(local, remote, atRest).action)
            .toBe('adopt-remote');           // the destructive path…
        expect(decideCrossTabMerge(local, remote, streaming).action)
            .toBe('adopt-metadata-only');    // …closed while streaming
    });

    it('does not take messages even when remote has MORE', () => {
        // An equal-or-greater count is not evidence the remote is correct:
        // IndexedDB can hold a different tail than live state mid-turn.
        const local = conv({ messages: [{ n: 1 }] });
        const remote = conv({ _version: T0 + 1000, messages: [{ n: 1 }, { n: 2 }] });
        expect(decideCrossTabMerge(local, remote, streaming).action)
            .toBe('adopt-metadata-only');
    });

    it('an older remote is still ignored while streaming', () => {
        const local = conv({ _version: T0 + 1000 });
        const remote = conv({ _version: T0 });
        expect(decideCrossTabMerge(local, remote, streaming))
            .toEqual({ action: 'keep-local' });
    });

    it('a brand-new remote is still adopted while another conv streams', () => {
        expect(decideCrossTabMerge(undefined, conv(), streaming))
            .toEqual({ action: 'adopt-remote' });
    });
});

describe('adoptMetadataOnly', () => {
    it('keeps local messages and takes remote metadata', () => {
        const local = conv({ messages: [{ n: 1 }, { n: 2 }], flags: [] });
        const remote = conv({
            _version: T0 + 1000, messages: [{ n: 1 }],
            flags: ['priority'], flagColor: 'red', title: 'Renamed',
        });
        const out = adoptMetadataOnly(local, remote);
        expect(out.messages).toEqual(local.messages);
        expect(out.flags).toEqual(['priority']);
        expect(out.flagColor).toBe('red');
        expect(out.title).toBe('Renamed');
    });

    it('propagates a metadata field the helper has never heard of', () => {
        // The exclusion-based shape is deliberate: the next `flags` should
        // reach other tabs without an edit here.
        const out = adoptMetadataOnly(conv(), conv({ someFutureField: 42 }));
        expect(out.someFutureField).toBe(42);
    });

    it('takes remote _version so the merge does not re-fire every broadcast', () => {
        const out = adoptMetadataOnly(conv(), conv({ _version: T0 + 1000 }));
        expect(out._version).toBe(T0 + 1000);
    });

    it('preserves local isActive rather than remote', () => {
        const local = conv({ isActive: false });
        const out = adoptMetadataOnly(local, conv({ isActive: true }));
        expect(out.isActive).toBe(false);
    });

    it('tolerates a local record with no messages array', () => {
        const out = adoptMetadataOnly({ id: 'c' }, conv());
        expect(out.messages).toEqual([]);
    });
});

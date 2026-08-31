/**
 * Pure tests for the deletion-pass suspicion guard.
 *
 * THE HAZARD
 *
 * syncWithServer's step 3b treats any locally-present conversation absent
 * from the server's list as "deleted by another instance" and splices it out
 * of React state.  The per-conversation guards (active chat, 60s grace,
 * previously-seen-on-server) all assume the LIST ITSELF is trustworthy.
 *
 * `listChats` returns [] on a non-2xx response.  An empty list makes
 * serverIdSet empty, which makes EVERY previously-seen conversation look
 * deleted at once — a project's whole sidebar vanishing because one request
 * returned 500.  It self-heals on the next successful poll (IDB is not
 * touched), but a mass disappearance is indistinguishable from data loss
 * from where the user sits.
 *
 * The guard: an empty list is only credible when we never knew the server to
 * hold anything.  If we previously saw server chats and now see none, the
 * list is suspect and the deletion pass is skipped.
 *
 * Keep in lockstep with utils/syncMerge.ts.
 */
import { shouldRunDeletionPass } from '../syncMerge';

describe('shouldRunDeletionPass', () => {
    it('runs when the server returned chats (the normal path)', () => {
        expect(shouldRunDeletionPass(101, 101)).toBe(true);
    });

    it('runs on a genuine single deletion (100 of 101 remain)', () => {
        // A real cross-instance delete must still propagate — the guard is
        // about an EMPTY list, not about the list having shrunk.
        expect(shouldRunDeletionPass(100, 101)).toBe(true);
    });

    it('runs when the server returned exactly one chat', () => {
        expect(shouldRunDeletionPass(1, 101)).toBe(true);
    });

    it('SKIPS an empty list when we previously knew of server chats', () => {
        // The production hazard: listChats returned [] from an error path.
        expect(shouldRunDeletionPass(0, 101)).toBe(false);
    });

    it('skips an empty list even when only one server chat was ever known', () => {
        expect(shouldRunDeletionPass(0, 1)).toBe(false);
    });

    it('runs on an empty list for a project that never had server chats', () => {
        // A genuinely empty project.  The pass is a no-op there (nothing has
        // been seen on the server, so its per-conversation guard rejects
        // every candidate), but suppressing it would be dishonest about why.
        expect(shouldRunDeletionPass(0, 0)).toBe(true);
    });

    it('is not fooled by a negative or non-finite count', () => {
        // Defensive: a count derived from a mis-shaped response must not read
        // as "server has chats" and re-open the hazard.
        expect(shouldRunDeletionPass(-1, 5)).toBe(false);
        expect(shouldRunDeletionPass(NaN, 5)).toBe(false);
    });

    it('treats an unknown local count as "we knew something" (fail safe)', () => {
        // If the caller cannot say how many ids it has seen, the safe reading
        // of an empty server list is still "suspect".
        expect(shouldRunDeletionPass(0, NaN)).toBe(false);
    });
});

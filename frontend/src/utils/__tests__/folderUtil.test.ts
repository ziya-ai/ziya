/**
 * Tests for effective-global resolution (folder inheritance).
 *
 * Globalness is inherited DOWN a folder subtree: marking a folder global
 * shares its entire contents, so a descendant whose own isGlobal is false is
 * still cross-project-visible while any ancestor is global (see commit
 * 2290a25, folder-inherited global surfacing).
 *
 * The 'conv with own isGlobal:false inside a global folder' case encodes a
 * specific production bug: such a conversation was dropped from the sidebar
 * by the startup visibility filter, which scoped by raw own-isGlobal instead
 * of effective-globalness. Keep this in lockstep with utils/folderUtil.ts and
 * the startup filter in context/ChatContext.tsx.
 */
import {
    folderIsEffectivelyGlobal,
    conversationIsEffectivelyGlobal,
} from '../folderUtil';

interface F { id: string; parentId?: string | null; isGlobal?: boolean; }

// root(global) > mid(local) > leaf(local)
const NESTED: F[] = [
    { id: 'root', parentId: null, isGlobal: true },
    { id: 'mid', parentId: 'root', isGlobal: false },
    { id: 'leaf', parentId: 'mid', isGlobal: false },
];

describe('folderIsEffectivelyGlobal', () => {
    it('is true for a folder whose own flag is set', () => {
        expect(folderIsEffectivelyGlobal(NESTED[0], NESTED)).toBe(true);
    });

    it('inherits global from an ancestor (deep)', () => {
        // leaf and mid are own-local but live under a global root
        expect(folderIsEffectivelyGlobal(NESTED[2], NESTED)).toBe(true);
        expect(folderIsEffectivelyGlobal(NESTED[1], NESTED)).toBe(true);
    });

    it('is false when no ancestor is global', () => {
        const local: F[] = [
            { id: 'a', parentId: null, isGlobal: false },
            { id: 'b', parentId: 'a', isGlobal: false },
        ];
        expect(folderIsEffectivelyGlobal(local[1], local)).toBe(false);
    });

    it('returns false for null/undefined folder', () => {
        expect(folderIsEffectivelyGlobal(undefined, NESTED)).toBe(false);
        expect(folderIsEffectivelyGlobal(null, NESTED)).toBe(false);
    });

    it('terminates on an unknown parentId (parent not in set)', () => {
        const orphan: F[] = [{ id: 'x', parentId: 'missing', isGlobal: false }];
        expect(folderIsEffectivelyGlobal(orphan[0], orphan)).toBe(false);
    });

    it('is cycle-safe (a -> b -> a)', () => {
        const cyclic: F[] = [
            { id: 'a', parentId: 'b', isGlobal: false },
            { id: 'b', parentId: 'a', isGlobal: false },
        ];
        expect(folderIsEffectivelyGlobal(cyclic[0], cyclic)).toBe(false);
    });

    it('still reports global when a cycle contains a global node', () => {
        const cyclic: F[] = [
            { id: 'a', parentId: 'b', isGlobal: false },
            { id: 'b', parentId: 'a', isGlobal: true },
        ];
        expect(folderIsEffectivelyGlobal(cyclic[0], cyclic)).toBe(true);
    });
});

describe('conversationIsEffectivelyGlobal', () => {
    it('is true when the conversation own flag is set', () => {
        const conv = { isGlobal: true, folderId: null };
        expect(conversationIsEffectivelyGlobal(conv, [])).toBe(true);
    });

    it('is true for own-local conversation inside a global folder (bug #2 repro)', () => {
        // Mirrors the ASR folder (global) holding conversation 01c462f9
        // whose own isGlobal was false — it must still be effectively global.
        const conv = { isGlobal: false, folderId: 'root' };
        expect(conversationIsEffectivelyGlobal(conv, NESTED)).toBe(true);
    });

    it('inherits through a deep folder chain', () => {
        const conv = { isGlobal: false, folderId: 'leaf' };
        expect(conversationIsEffectivelyGlobal(conv, NESTED)).toBe(true);
    });

    it('is false for own-local conversation in a non-global folder', () => {
        const local: F[] = [{ id: 'plain', parentId: null, isGlobal: false }];
        const conv = { isGlobal: false, folderId: 'plain' };
        expect(conversationIsEffectivelyGlobal(conv, local)).toBe(false);
    });

    it('is false for a loose conversation (no folderId) without own flag', () => {
        const conv = { isGlobal: false, folderId: null };
        expect(conversationIsEffectivelyGlobal(conv, NESTED)).toBe(false);
    });

    it('is false when folderId references a folder not in the set', () => {
        const conv = { isGlobal: false, folderId: 'ghost' };
        expect(conversationIsEffectivelyGlobal(conv, NESTED)).toBe(false);
    });

    it('returns false for null/undefined conversation', () => {
        expect(conversationIsEffectivelyGlobal(undefined, NESTED)).toBe(false);
        expect(conversationIsEffectivelyGlobal(null, NESTED)).toBe(false);
    });
});

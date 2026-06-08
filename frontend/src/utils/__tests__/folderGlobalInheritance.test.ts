import {
    folderIsEffectivelyGlobal,
    conversationIsEffectivelyGlobal,
    globalMenuItemState,
} from '../folderUtil';

// Folder tree used across cases:
//   root (global)
//     └─ mid (not global)
//          └─ leaf (not global)
//   orphan (global, no parent)
//   plain (not global)
const folders = [
    { id: 'root', parentId: null, isGlobal: true },
    { id: 'mid', parentId: 'root', isGlobal: false },
    { id: 'leaf', parentId: 'mid', isGlobal: false },
    { id: 'orphan', parentId: null, isGlobal: true },
    { id: 'plain', parentId: null, isGlobal: false },
];

describe('folderIsEffectivelyGlobal', () => {
    test('own flag set → global', () => {
        expect(folderIsEffectivelyGlobal(folders[0], folders)).toBe(true); // root, isGlobal: true
        expect(folderIsEffectivelyGlobal(folders[3], folders)).toBe(true); // orphan
    });

    test('inherits from immediate parent', () => {
        expect(folderIsEffectivelyGlobal(folders[1], folders)).toBe(true); // mid under global root
    });

    test('inherits from grandparent (full chain)', () => {
        expect(folderIsEffectivelyGlobal(folders[2], folders)).toBe(true); // leaf under mid under global root
    });

    test('no ancestor global → not global', () => {
        expect(folderIsEffectivelyGlobal(folders[4], folders)).toBe(false); // plain
    });

    test('null/undefined folder → false', () => {
        expect(folderIsEffectivelyGlobal(null, folders)).toBe(false);
        expect(folderIsEffectivelyGlobal(undefined, folders)).toBe(false);
    });

    test('unknown parentId terminates walk, judged on known ancestors', () => {
        const detached = { id: 'd', parentId: 'gone', isGlobal: false };
        expect(folderIsEffectivelyGlobal(detached, [detached])).toBe(false);
    });

    test('cycle is safe (A→B→A), no infinite loop', () => {
        const cyc = [
            { id: 'a', parentId: 'b', isGlobal: false },
            { id: 'b', parentId: 'a', isGlobal: false },
        ];
        expect(folderIsEffectivelyGlobal(cyc[0], cyc)).toBe(false);
    });

    test('cycle with a global node still resolves true before looping', () => {
        const cyc = [
            { id: 'a', parentId: 'b', isGlobal: false },
            { id: 'b', parentId: 'a', isGlobal: true },
        ];
        expect(folderIsEffectivelyGlobal(cyc[0], cyc)).toBe(true);
    });

    test('un-globaling a child of a global parent does NOT remove inherited visibility', () => {
        // This is the exact semantics agreed: child.isGlobal=false but parent
        // global → child stays effectively global (inherited), so toggling the
        // child off is a no-op for current visibility.
        const child = { id: 'leaf', parentId: 'mid', isGlobal: false };
        expect(folderIsEffectivelyGlobal(child, folders)).toBe(true);
    });
});

describe('conversationIsEffectivelyGlobal', () => {
    test('own flag set → global regardless of folder', () => {
        expect(conversationIsEffectivelyGlobal({ isGlobal: true, folderId: 'plain' }, folders)).toBe(true);
    });

    test('inherits through folder ancestor chain', () => {
        expect(conversationIsEffectivelyGlobal({ folderId: 'leaf' }, folders)).toBe(true); // leaf → mid → global root
    });

    test('in a non-global folder with no global ancestor → not global', () => {
        expect(conversationIsEffectivelyGlobal({ folderId: 'plain' }, folders)).toBe(false);
    });

    test('loose conversation (no folderId), own flag false → not global', () => {
        expect(conversationIsEffectivelyGlobal({ isGlobal: false }, folders)).toBe(false);
        expect(conversationIsEffectivelyGlobal({ folderId: null }, folders)).toBe(false);
    });

    test('folderId pointing at missing folder → judged on own flag only', () => {
        expect(conversationIsEffectivelyGlobal({ folderId: 'gone' }, folders)).toBe(false);
        expect(conversationIsEffectivelyGlobal({ isGlobal: true, folderId: 'gone' }, folders)).toBe(true);
    });

    test('null/undefined conversation → false', () => {
        expect(conversationIsEffectivelyGlobal(null, folders)).toBe(false);
        expect(conversationIsEffectivelyGlobal(undefined, folders)).toBe(false);
    });
});

describe('globalMenuItemState', () => {
    test('not global → enabled "Share across projects"', () => {
        const s = globalMenuItemState(false, false);
        expect(s.disabled).toBe(false);
        expect(s.label).toBe('🌐 Share across projects');
        expect(s.tooltip).toBeUndefined();
    });

    test('own-global → enabled "This project only" (un-share)', () => {
        const s = globalMenuItemState(true, true);
        expect(s.disabled).toBe(false);
        expect(s.label).toBe('📌 This project only');
        expect(s.tooltip).toBeUndefined();
    });

    test('inheritance-only (effective && !own) → disabled with tooltip', () => {
        const s = globalMenuItemState(true, false);
        expect(s.disabled).toBe(true);
        expect(s.label).toBe('Shared via parent folder');
        expect(s.tooltip).toMatch(/unshare the parent/i);
    });

    test('own-global wins over inheritance — a node with its own flag set is never disabled', () => {
        // effective && own: even if an ancestor is also global, the node has
        // an independent flag, so the toggle stays actionable.
        const s = globalMenuItemState(true, true);
        expect(s.disabled).toBe(false);
    });

    test('impossible state (own set but not effective) is treated as own-global, enabled', () => {
        // ownGlobal true should imply effectiveGlobal true; if a caller ever
        // passes (false, true) the item must still be actionable, never
        // silently disabled.
        const s = globalMenuItemState(false, true);
        expect(s.disabled).toBe(false);
        expect(s.label).toBe('📌 This project only');
    });

    test('composes with the effective-global helpers (leaf under global root)', () => {
        // leaf inherits global from root but has own flag false → inheritance-only.
        const eff = folderIsEffectivelyGlobal(folders[2], folders); // leaf
        const own = folders[2].isGlobal === true;
        const s = globalMenuItemState(eff, own);
        expect(s.disabled).toBe(true);
        expect(s.label).toBe('Shared via parent folder');
    });

    test('composes for an own-global root → enabled un-share', () => {
        const eff = folderIsEffectivelyGlobal(folders[0], folders); // root
        const own = folders[0].isGlobal === true;
        const s = globalMenuItemState(eff, own);
        expect(s.disabled).toBe(false);
        expect(s.label).toBe('📌 This project only');
    });
});

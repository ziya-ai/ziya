/**
 * The gauge's per-key total.  The first two cases are the live bug: a file
 * pinned from a .gitignore'd (symlinked) directory has no tree node, and the
 * old inline rule returned 0 before consulting the accurate count.
 */
import { folderTotalTokens, expandCheckedKeysToFiles } from '../contextTokenTotals';

const tree = {
    internal: {
        token_count: 300,
        children: {
            'a.py': { token_count: 100 },
            'b.py': { token_count: 200 },
            sub: { token_count: 50, children: { 'c.py': { token_count: 50 } } },
        },
    },
    'AGENTS.md': { token_count: 40 },
};
const at = (n: number) => ({ count: n, timestamp: 1 });

describe('folderTotalTokens', () => {
    it('credits a file the tree does not carry when the server counted it', () => {
        const acc = { 'public/frontend/src/components/MarkdownRenderer.tsx': at(96787) };
        expect(folderTotalTokens('public/frontend/src/components/MarkdownRenderer.tsx', tree, acc)).toBe(96787);
    });

    it('honours an accurate count above the old 50k ceiling', () => {
        expect(folderTotalTokens('public/app/mcp/manager.py', tree, { 'public/app/mcp/manager.py': at(49216) })).toBe(49216);
    });

    it('is 0 for an unknown path with no accurate count (folder keys under an ignored dir)', () => {
        expect(folderTotalTokens('public/app', tree, {})).toBe(0);
    });

    it('prefers the accurate count over the tree estimate for a known file', () => {
        expect(folderTotalTokens('AGENTS.md', tree, { 'AGENTS.md': at(1676) })).toBe(1676);
    });

    it('falls back to the tree estimate for a known file with no accurate count', () => {
        expect(folderTotalTokens('AGENTS.md', tree, {})).toBe(40);
    });

    it('sums a folder recursively, using accurate counts per child where present', () => {
        expect(folderTotalTokens('internal', tree, {})).toBe(350);
        expect(folderTotalTokens('internal', tree, { 'internal/a.py': at(1000) })).toBe(1250);
        expect(folderTotalTokens('internal/sub', tree, {})).toBe(50);
    });

    it('ignores a zero accurate count (the old "valid zero") and falls back', () => {
        expect(folderTotalTokens('AGENTS.md', tree, { 'AGENTS.md': at(0) })).toBe(40);
    });

    it('handles a missing tree without throwing', () => {
        expect(folderTotalTokens('x.py', null, { 'x.py': at(7) })).toBe(7);
        expect(folderTotalTokens('x.py', undefined, {})).toBe(0);
    });

    it('walks repeated path segments correctly', () => {
        const t = { a: { children: { b: { children: { a: { token_count: 9 } } } } } };
        expect(folderTotalTokens('a/b/a', t, {})).toBe(9);
    });
});

describe('expandCheckedKeysToFiles', () => {
    it('expands a folder key to its leaf files, recursively', () => {
        expect(expandCheckedKeysToFiles(['internal'], tree).sort()).toEqual(
            ['internal/a.py', 'internal/b.py', 'internal/sub/c.py']);
    });

    it('passes through a key the tree does not carry so the server can price or flag it', () => {
        expect(expandCheckedKeysToFiles(['public/app/mcp/manager.py', 'public/app'], tree).sort())
            .toEqual(['public/app', 'public/app/mcp/manager.py']);
    });

    it('drops keys already covered by a checked ancestor', () => {
        expect(expandCheckedKeysToFiles(['internal', 'internal/a.py', 'internal/sub'], tree).sort())
            .toEqual(['internal/a.py', 'internal/b.py', 'internal/sub/c.py']);
    });

    it('keeps file keys and [external] keys unchanged', () => {
        expect(expandCheckedKeysToFiles(['AGENTS.md', '[external]/tmp/x.py'], tree).sort())
            .toEqual(['AGENTS.md', '[external]/tmp/x.py']);
    });

    it('handles a missing tree by passing everything through', () => {
        expect(expandCheckedKeysToFiles(['a', 'b/c.py'], undefined).sort()).toEqual(['a', 'b/c.py']);
    });
});

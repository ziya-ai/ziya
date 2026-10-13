/**
 * [external] folders are listed on demand (app/utils/external_tree.py).
 * The server marks a folder whose listing is missing or incomplete with
 * `_lazy: true`; the tree must still show it as an expandable folder, and the
 * explorer must know which lazy folders to fetch: the ones the user has
 * expanded, and the ones on the path to a checked file.
 */
import {
    convertToTreeData,
    externalKeyToPath,
    mergeExternalNode,
    treeNodeAtKey,
    lazyExternalKeysToLoad,
    subtreeHasLazy,
} from '../folderUtil';
import { Folders } from '../types';

const folders = (): Folders => ({
    app: { token_count: 3, children: { 'a.py': { token_count: 3 } } },
    '[external]': {
        token_count: 0,
        children: {
            Users: {
                token_count: 0,
                children: {
                    me: {
                        token_count: 0,
                        children: {
                            Specs: {
                                token_count: 7,
                                children: {
                                    'Roadmap.pdf': { token_count: 7 },
                                    CPSS: { token_count: 0, children: {}, _lazy: true },
                                    Empty: { token_count: 0, children: {} },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
});

const find = (nodes: any[], key: string): any => {
    for (const n of nodes) {
        if (n.key === key) return n;
        const hit = n.children && find(n.children, key);
        if (hit) return hit;
    }
    return undefined;
};

const SPECS = '[external]/Users/me/Specs';

describe('lazy [external] folders in the tree', () => {
    it('a lazy folder is an expandable folder, an empty listed one is not', () => {
        const tree = convertToTreeData(folders());
        const cpss = find(tree, `${SPECS}/CPSS`);
        expect(cpss.lazy).toBe(true);
        expect(cpss.isLeaf).toBe(false);
        const empty = find(tree, `${SPECS}/Empty`);
        expect(empty.lazy).toBeUndefined();
        expect(empty.isLeaf).toBe(true);
        expect(find(tree, 'app').lazy).toBeUndefined();
    });

    it('maps tree keys to absolute paths', () => {
        expect(externalKeyToPath(`${SPECS}/CPSS`)).toBe('/Users/me/Specs/CPSS');
        expect(externalKeyToPath('[external]')).toBeNull();
        expect(externalKeyToPath('app/a.py')).toBeNull();
    });

    it('merges a fetched listing without touching siblings or the input', () => {
        const before = folders();
        const listing = {
            token_count: 2,
            children: { 'p.html': { token_count: 2 }, html: { token_count: 0, children: {}, _lazy: true } },
        };
        const after = mergeExternalNode(before, '/Users/me/Specs/CPSS', listing);

        const specs = after['[external]'].children!.Users.children!.me.children!.Specs;
        expect(specs.children!.CPSS).toBe(listing);
        expect(specs.children!['Roadmap.pdf']).toEqual({ token_count: 7 });
        expect(after.app).toBe(before.app);
        expect(before['[external]'].children!.Users.children!.me.children!.Specs.children!.CPSS._lazy).toBe(true);

        const cpss = find(convertToTreeData(after), `${SPECS}/CPSS`);
        expect(cpss.lazy).toBeUndefined();
        expect(cpss.children.map((c: any) => c.key)).toEqual([`${SPECS}/CPSS/html`, `${SPECS}/CPSS/p.html`]);
    });

    it('builds the tree node for a fetched subtree under its own key', () => {
        const node = treeNodeAtKey(`${SPECS}/CPSS`, { token_count: 1, children: { 'x.md': { token_count: 1 } } });
        expect(node.key).toBe(`${SPECS}/CPSS`);
        expect((node.children as any[])[0].key).toBe(`${SPECS}/CPSS/x.md`);
    });

    it('loads lazy folders that are expanded or lead to a checked file, nothing else', () => {
        const tree = convertToTreeData(folders());
        expect(lazyExternalKeysToLoad(tree, [], [])).toEqual([]);
        expect(lazyExternalKeysToLoad(tree, [`${SPECS}/CPSS`], [])).toEqual([`${SPECS}/CPSS`]);
        expect(lazyExternalKeysToLoad(tree, [], [`${SPECS}/CPSS/html/p0.html`])).toEqual([`${SPECS}/CPSS`]);
        // A checked lazy folder itself is not a reason to list it.
        expect(lazyExternalKeysToLoad(tree, [], [`${SPECS}/CPSS`])).toEqual([]);
    });

    it('detects unloaded folders anywhere in a subtree', () => {
        const tree = convertToTreeData(folders());
        expect(subtreeHasLazy(find(tree, SPECS))).toBe(true);
        expect(subtreeHasLazy(find(tree, `${SPECS}/Empty`))).toBe(false);
        expect(subtreeHasLazy(find(tree, 'app'))).toBe(false);
    });
});

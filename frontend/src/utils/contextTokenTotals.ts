/**
 * Token total for one checked key in the file tree.
 *
 * The tree (`/api/folders`) is built with the project's ignore rules, so a
 * pinned file under a .gitignore'd directory — the symlinked `public/` in
 * ZiyaInternal is the motivating case — has no node at all.  The prompt
 * builder still sends it (selection cleanup validates keys against disk, not
 * against the tree).  The previous rule returned 0 as soon as the tree walk
 * missed, before ever consulting the server's accurate count, which is how
 * the gauge reported <200k while the provider billed 878k.
 *
 * Rule: a server-side accurate count for the exact path wins outright; only
 * then does the tree's own token_count (files) or recursive sum (folders)
 * apply.  A FOLDER key the tree does not carry still totals 0 — the accurate
 * endpoint counts files, not directories — and closing that gap belongs to
 * the server-side context-estimate endpoint, not to this helper.
 */
export type AccurateCounts = Record<string, { count: number; timestamp: number }>;

interface TreeNode {
    token_count?: number;
    children?: Record<string, TreeNode>;
}

export function folderTotalTokens(
    path: string,
    folderData: Record<string, TreeNode> | null | undefined,
    accurate: AccurateCounts,
): number {
    const exact = accurate[path];
    if (exact && exact.count > 0) return exact.count;
    if (!folderData) return 0;

    let current: any = folderData;
    const parts = path.split('/').filter(p => p.length > 0);
    for (let i = 0; i < parts.length; i++) {
        if (!current || !current[parts[i]]) return 0;
        current = current[parts[i]];
        if (i < parts.length - 1) {
            current = current.children;
            if (!current) return 0;
        }
    }

    if (!current.children) return current.token_count || 0;

    let total = 0;
    for (const [name, child] of Object.entries(current.children as Record<string, TreeNode>)) {
        const childPath = path ? `${path}/${name}` : name;
        if (child.children) {
            total += folderTotalTokens(childPath, folderData, accurate);
        } else {
            const a = accurate[childPath];
            const t = (a && a.count > 0) ? a.count : (child.token_count || 0);
            if (t > 0) total += t;
        }
    }
    return total;
}

/**
 * Leaf file paths for a set of checked keys, for POST /api/context-estimate.
 *
 * The prompt builder skips directories, so a checked folder contributes its
 * files, not itself.  A folder key is expanded through the tree; a key the
 * tree does not carry is passed through unchanged so the server can price it
 * (a pinned file under a .gitignore'd directory) or report it as unreadable.
 * A key whose checked ancestor IS in the tree is dropped — the ancestor's
 * expansion covers it.  An ancestor the tree lacks covers nothing (the
 * server sees only a directory), so its descendants are kept.
 */
export function expandCheckedKeysToFiles(
    keys: string[],
    folderData: Record<string, TreeNode> | null | undefined,
): string[] {
    const checked = new Set(keys);
    const out = new Set<string>();
    const lookup = (path: string): TreeNode | undefined => {
        let node: any = folderData ? { children: folderData } : undefined;
        for (const part of path.split('/').filter(Boolean)) {
            node = node?.children?.[part];
            if (!node) return undefined;
        }
        return node;
    };
    const coveredByExpandableAncestor = (path: string): boolean => {
        const parts = path.split('/');
        for (let i = parts.length - 1; i > 0; i--) {
            const anc = parts.slice(0, i).join('/');
            if (checked.has(anc) && lookup(anc)?.children) return true;
        }
        return false;
    };
    const collect = (node: TreeNode, path: string) => {
        if (!node.children) { out.add(path); return; }
        for (const [name, child] of Object.entries(node.children)) collect(child, `${path}/${name}`);
    };
    for (const key of keys) {
        if (coveredByExpandableAncestor(key)) continue;
        const node = lookup(key);
        if (node) collect(node, key); else out.add(key);
    }
    return Array.from(out);
}

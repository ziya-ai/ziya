/**
 * "By package" grouping for the Bench board.  Pure: it files the cards of one
 * lane under where they came from (``item.groups``, supplied by the server:
 * package, then component), so the tree can be asserted without a DOM.
 *
 * An item shipped by several packages is filed under each of them.  It is
 * still ONE item with one placement; moving it anywhere moves it everywhere.
 * Items with no package go under "Ziya" (built-in) or "Not from a package".
 */
import type { BenchItemView } from '../../hooks/useBench';

export type GroupBy = 'none' | 'package';
export const BUILTIN_GROUP = 'Ziya';
export const UNGROUPED = 'Not from a package';
export const PATH_SEP = ' › ';

export interface GroupNode {
    label: string;
    /** Labels from the root joined by PATH_SEP; unique within one tree. */
    path: string;
    /** Items filed directly here (not those of child groups). */
    items: BenchItemView[];
    children: GroupNode[];
}

export function groupPaths(item: BenchItemView): string[][] {
    const paths = (item.groups ?? []).filter(p => p.length > 0);
    if (paths.length > 0) return paths;
    return [[item.provenance === 'builtin' ? BUILTIN_GROUP : UNGROUPED]];
}

/** True when the server attributed at least one item to a package. */
export function hasPackages(items: BenchItemView[]): boolean {
    return items.some(i => (i.groups ?? []).some(p => p.length > 0));
}

/** How many OTHER top-level packages also ship this item. */
export function otherPackageCount(item: BenchItemView, root: string): number {
    const roots = new Set(groupPaths(item).map(p => p[0]));
    roots.delete(root);
    return roots.size;
}

const rank = (label: string) => (label === BUILTIN_GROUP ? 1 : label === UNGROUPED ? 2 : 0);

function sortNodes(nodes: GroupNode[]): GroupNode[] {
    nodes.sort((a, b) => rank(a.label) - rank(b.label) || a.label.localeCompare(b.label));
    nodes.forEach(n => sortNodes(n.children));
    return nodes;
}

/** Packages first (A-Z), then built-ins, then everything unattributed. */
export function buildPackageTree(cards: BenchItemView[]): GroupNode[] {
    const roots: GroupNode[] = [];
    for (const item of cards) {
        for (const segments of groupPaths(item)) {
            let level = roots;
            let node: GroupNode | undefined;
            let path = '';
            for (const label of segments) {
                path = path ? `${path}${PATH_SEP}${label}` : label;
                node = level.find(n => n.label === label);
                if (!node) {
                    node = { label, path, items: [], children: [] };
                    level.push(node);
                }
                level = node.children;
            }
            if (node && !node.items.some(i => i.key === item.key)) node.items.push(item);
        }
    }
    return sortNodes(roots);
}

/** Every item under a node, each once, children first. */
export function nodeItems(node: GroupNode): BenchItemView[] {
    const seen = new Set<string>();
    const out: BenchItemView[] = [];
    const walk = (n: GroupNode) => {
        n.children.forEach(walk);
        for (const i of n.items) {
            if (!seen.has(i.key)) { seen.add(i.key); out.push(i); }
        }
    };
    walk(node);
    return out;
}

export function countText(items: BenchItemView[]): string {
    const skills = items.filter(i => i.kind === 'skill').length;
    const servers = items.filter(i => i.kind === 'mcp').length;
    const parts: string[] = [];
    if (skills) parts.push(`${skills} skill${skills === 1 ? '' : 's'}`);
    if (servers) parts.push(`${servers} server${servers === 1 ? '' : 's'}`);
    return parts.join(' · ');
}

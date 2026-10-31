/**
 * Grouping and title helpers for the compact layout of hierarchical tool
 * results (search hits).  A formatter opts in by returning
 * `layout: 'compact'` and may tag each row with `group`, `detail`, `dim`.
 */
export interface HierarchicalRow {
    title: string;
    content: string;
    language?: string;
    metadata?: Record<string, any>;
    group?: string;
    detail?: string;
    dim?: boolean;
}

export interface HierarchicalGroup {
    /** null for a run of ungrouped rows */
    name: string | null;
    items: Array<{ row: HierarchicalRow; index: number }>;
}

/**
 * Named groups collect at their first appearance, so a group's rows keep
 * their relative order; consecutive ungrouped rows form one unnamed group
 * at their own position.  `index` is the row's position in the input.
 */
export function groupHierarchicalRows(rows: HierarchicalRow[]): HierarchicalGroup[] {
    const groups: HierarchicalGroup[] = [];
    const byName = new Map<string, HierarchicalGroup>();
    rows.forEach((row, index) => {
        const name = typeof row?.group === 'string' && row.group ? row.group : null;
        if (name === null) {
            const last = groups[groups.length - 1];
            if (last && last.name === null) last.items.push({ row, index });
            else groups.push({ name: null, items: [{ row, index }] });
            return;
        }
        let group = byName.get(name);
        if (!group) {
            group = { name, items: [] };
            byName.set(name, group);
            groups.push(group);
        }
        group.items.push({ row, index });
    });
    return groups;
}

/**
 * Split a row title for display.  A leading "<group>/" is dropped (the group
 * header already shows it); a path-like title (no whitespace) is split into
 * its directory and last segment so the directory can be dimmed.
 */
export function splitRowTitle(title: string, group?: string | null): { dir: string; base: string } {
    let text = String(title ?? '');
    if (group && text.startsWith(`${group}/`)) text = text.slice(group.length + 1);
    const slash = text.lastIndexOf('/');
    if (/\s/.test(text) || slash < 0) return { dir: '', base: text };
    return { dir: text.slice(0, slash + 1), base: text.slice(slash + 1) };
}

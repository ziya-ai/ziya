import {Folders} from "./types";
import {TreeDataNode} from "antd";

/**
 * Validate that a string looks like a real file/directory path.
 *
 * Used to defend checkedKeys against corruption: bad keys have been
 * observed in user-reported state ('frontendingConversation,' is the
 * canonical example, suggesting a string-concatenation accident
 * somewhere in the event-driven add paths).  Without sanitization
 * these persist forever in sessionStorage and clutter every render.
 *
 * Rules mirror extractAllFilesFromDiff's path validator: rejects
 * obviously-malformed strings without being so strict that legitimate
 * paths with unusual chars get filtered out.
 */
export const isValidCheckedKey = (key: string): boolean => {
    if (!key || typeof key !== 'string') return false;
    if (key.length === 0 || key.length > 500) return false;
    // External paths from the file browser are prefixed and always valid here.
    if (key.startsWith('[external]')) return true;
    // Reject characters that don't appear in real file paths.  Whitespace
    // and shell metacharacters are the highest-confidence corruption signal.
    if (/[)(;{}!@#$%^&*+=<>?\s",]/.test(key)) return false;
    // Trailing slashes (e.g. 'frontend/src/') aren't valid file keys —
    // they correspond to no leaf in the tree.  Strip-and-test elsewhere
    // if the caller wants to keep the path; here we just reject.
    if (key !== '/' && key.endsWith('/')) return false;
    return true;
};

/**
 * Sanitize an array of checkedKeys by dropping entries that fail
 * `isValidCheckedKey` and deduplicating.  Returns a new array.
 * Logs the dropped entries so the user can see what was scrubbed.
 */
export const sanitizeCheckedKeys = (keys: unknown): string[] => {
    if (!Array.isArray(keys)) return [];
    const seen = new Set<string>();
    const dropped: string[] = [];
    const cleaned: string[] = [];
    for (const k of keys) {
        const s = typeof k === 'string' ? k : (k != null ? String(k) : '');
        if (!isValidCheckedKey(s)) {
            dropped.push(s);
            continue;
        }
        if (seen.has(s)) continue;
        seen.add(s);
        cleaned.push(s);
    }
    if (dropped.length > 0) {
        // eslint-disable-next-line no-console
        console.warn(`🧹 SANITIZE: Dropped ${dropped.length} corrupt checkedKeys:`, dropped);
    }
    return cleaned;
};

/**
 * Walk a folders tree and collect every node path (leaves + intermediate
 * directories).  Used to validate checkedKeys against the real tree —
 * paths in keys that don't exist in the tree are stale and should be
 * dropped during cleanup.
 */
export const collectAllTreePaths = (folders: Folders | undefined): Set<string> => {
    const paths = new Set<string>();
    if (!folders) return paths;
    const recurse = (node: Folders, prefix: string) => {
        for (const [name, meta] of Object.entries(node)) {
            if (!meta || typeof meta !== 'object') continue;
            const path = prefix ? `${prefix}/${name}` : name;
            paths.add(path);
            if (meta.children) recurse(meta.children, path);
        }
    };
    recurse(folders, '');
    return paths;
};

export const convertToTreeData = (folders: Folders, parentKey = ''): TreeDataNode[] => {
    if (!folders || typeof folders !== 'object') return [];
    return Object.entries(folders)
        .filter(([key]) => {
            // Skip metadata flags and children property
            if (key === 'children') return false;
            if (key.startsWith('_') && ['_timeout', '_partial', '_cancelled', '_stale', '_scanning', '_error'].includes(key)) return false;
            return true;
        })
        .sort(([a], [b]) => a.toLowerCase().localeCompare(b.toLowerCase())).map(([key, value]) => {
        const currentKey = parentKey ? `${parentKey}/${key}` : key;
        const tokenCount = (value?.token_count ?? 0);
        const title = key;  // Just use the filename without token count
        const node: TreeDataNode = {
            title,
            key: currentKey,
            // All folders are collapsed by default
            isLeaf: !value.children || Object.keys(value.children).length === 0,
        };

        if (value.children) {
            node.children = convertToTreeData(value.children, currentKey);
        }
        return node;
    });
};

/**
 * Insert a new file into the Folders structure at the given relative path.
 * Creates intermediate directory nodes as needed.
 */
export const insertIntoFolders = (root: Folders, relPath: string, tokenCount: number): void => {
    const parts = relPath.split('/');
    let current = root;

    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        const isLast = i === parts.length - 1;

        if (isLast) {
            current[part] = { token_count: tokenCount };
        } else {
            if (!current[part]) {
                current[part] = { token_count: 0, children: {} };
            }
            if (!current[part].children) {
                current[part].children = {};
            }
            current = current[part].children!;
        }
    }
};

/**
 * Update the token count for an existing file in the Folders structure.
 * No-op if the path doesn't exist.
 */
export const updateTokenInFolders = (root: Folders, relPath: string, tokenCount: number): void => {
    const parts = relPath.split('/');
    let current = root;

    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        const isLast = i === parts.length - 1;

        if (!current[part]) return;

        if (isLast) {
            current[part].token_count = tokenCount;
        } else {
            if (!current[part].children) return;
            current = current[part].children!;
        }
    }
};

/**
 * Remove a file from the Folders structure.
 * Cleans up empty parent directories.
 */
export const removeFromFolders = (root: Folders, relPath: string): void => {
    const parts = relPath.split('/');

    const remove = (node: Folders, depth: number): boolean => {
        const part = parts[depth];
        if (!node[part]) return false;

        if (depth === parts.length - 1) {
            delete node[part];
            return true;
        }

        if (!node[part].children) return false;
        const deleted = remove(node[part].children!, depth + 1);

        if (deleted && Object.keys(node[part].children!).length === 0) {
            delete node[part];
        }
        return deleted;
    };

    remove(root, 0);
};

const hasSearchTerm = (n, searchTerm) =>
    n.toLowerCase().indexOf(searchTerm.toLowerCase()) !== -1;

const filterData = (arr, searchTerm) =>
    arr?.filter(
        (n) =>
            hasSearchTerm(n.title, searchTerm) ||
            filterData(n.children, searchTerm)?.length > 0
    );

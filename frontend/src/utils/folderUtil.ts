import {Folders} from "./types";
import {TreeDataNode} from "antd";

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

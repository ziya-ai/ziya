import {Folders} from "./types";
import {TreeDataNode} from "antd";

export const convertToTreeData = (folders: Folders, parentKey = ''): TreeDataNode[] => {
    if (!folders || typeof folders !== 'object') return [];
    return Object.entries(folders).filter(([key]) => key !== 'children').sort(([a], [b]) => a.toLowerCase().localeCompare(b.toLowerCase())).map(([key, value]) => {
        const currentKey = parentKey ? `${parentKey}/${key}` : key;
        const tokenCount = (value?.token_count ?? 0);
        const title = `${key} (${tokenCount.toLocaleString()} tokens)`;
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

const hasSearchTerm = (n, searchTerm) =>
    n.toLowerCase().indexOf(searchTerm.toLowerCase()) !== -1;

const filterData = (arr, searchTerm) =>
    arr?.filter(
        (n) =>
            hasSearchTerm(n.title, searchTerm) ||
            filterData(n.children, searchTerm)?.length > 0
    );

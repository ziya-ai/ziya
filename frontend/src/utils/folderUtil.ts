import {Folders} from "./types";
import {TreeDataNode} from "antd";

export const convertToTreeData = (folders: Folders, parentKey = ''): TreeDataNode[] => {
    return Object.entries(folders).map(([key, value]) => {
        const currentKey = parentKey ? `${parentKey}/${key}` : key;
        const title = `${key} (${value.token_count.toLocaleString()} tokens)`;
        const node: TreeDataNode = {
            title,
            key: currentKey,
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
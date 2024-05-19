import {CheckboxTreeNodes, Folders} from "./types";

export const convertToNodes = (folder: Folders, parentPath = '') :  CheckboxTreeNodes[] => {
    return Object.entries(folder).map(([key, value]) => {
        const path = parentPath ? `${parentPath}/${key}` : key;
        return {
            // @ts-ignore
            label: `${key} (${value.token_count.toLocaleString("en-US")} tokens)`,
            value: path,
            // @ts-ignore
            children: value.children ? convertToNodes(value.children, path) : undefined,
        };
    });
};

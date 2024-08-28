import React, {useEffect, useState} from 'react';
import {Folders} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";

const TOKEN_LIMIT = 160000;
const WARNING_THRESHOLD = 120000;
const DANGER_THRESHOLD = 160000;

export const TokenCountDisplay = () => {

    const {folders, checkedKeys} = useFolderContext();

    const [totalTokenCount, setTotalTokenCount] = useState(0);

    useEffect(() => {
        calculateTotalTokenCount(checkedKeys as string[]);
    }, [checkedKeys]);

    const getTokenCountClass = () => {
        if (totalTokenCount > DANGER_THRESHOLD) return 'red';
        if (totalTokenCount > WARNING_THRESHOLD) return 'orange';
        return 'green';
    };

    const getFolderTokenCount = (filePath: string, folders: Folders) => {
        let segments = filePath.split('/');
        let lastNode;
        for (const segment of segments) {
            if (folders[segment] && folders[segment].children) {
                folders = folders[segment].children!;
            } else {
                lastNode = folders[segment];
            }
        }
        return lastNode ? lastNode.token_count : 0;
    };

    const calculateTotalTokenCount = (checked: string[]) => {
        let totalTokenCount = 0;
        checked.forEach(item => {
            const folderTokenCount = getFolderTokenCount(item, folders!);
            totalTokenCount += folderTokenCount;
        });
        setTotalTokenCount(totalTokenCount);
    };

    return (
        <h3 className={`token-count ${getTokenCountClass()}`}>
            Tokens: {totalTokenCount.toLocaleString()} / {TOKEN_LIMIT.toLocaleString()}
        </h3>
    );
};
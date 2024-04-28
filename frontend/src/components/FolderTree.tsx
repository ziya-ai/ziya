import React, {useEffect, useState} from 'react';
import CheckboxTree from 'react-checkbox-tree';
import 'react-checkbox-tree/lib/react-checkbox-tree.css';
import {convertToNodes} from "../utils/folderUtil";
import {Folders} from "../utils/types";

export const FolderTree = ({setCheckedItems}) => {

    const [checked, setChecked] = useState<string[]>([]);
    const [expanded, setExpanded] = useState([]);
    const [totalTokenCount, setTotalTokenCount] = useState(0);
    const [folders, setFolders] = useState<Folders>();

    useEffect(() => {
        const fetchFolders = async () => {
            try {
                const response = await fetch('/api/folders');
                const data = await response.json();
                setFolders(data)
            } catch (error) {
                console.error('Error fetching folders:', error);
            }
        };
        fetchFolders();
    }, []);

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
        return lastNode.token_count;
    };


    const calculateTotalTokenCount = (checked: string[]) => {
        let totalTokenCount = 0;
        checked.forEach(item => {
            const folderTokenCount = getFolderTokenCount(item, folders!);
            totalTokenCount += folderTokenCount;
        });
        setTotalTokenCount(totalTokenCount);
    };

    const getTokenCountClass = () => {
        if (totalTokenCount > 180000) {
            return 'red';
        } else if (totalTokenCount > 150000) {
            return 'orange';
        }
        return 'green';
    }

    return (
        <div className="folder-tree-panel">
            <h3 className={`token-count ${getTokenCountClass()}`}>
                Tokens: {totalTokenCount.toLocaleString()} / 160,000
            </h3>
            {folders ? <CheckboxTree
                nodes={convertToNodes(folders)}
                checked={checked}
                expanded={expanded}
                onCheck={checked => {
                    // @ts-ignore
                    setChecked(checked);
                    setCheckedItems(checked);
                    calculateTotalTokenCount(checked)
                }}
                // @ts-ignore
                onExpand={e => setExpanded(e)}
            /> : <div>Loading Folders...</div>}

        </div>
    );
};
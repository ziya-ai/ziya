import React, {useEffect, useState} from 'react';
import CheckboxTree from 'react-checkbox-tree';
import 'react-checkbox-tree/lib/react-checkbox-tree.css';
import {convertToNodes} from "../utils/folderUtil";
import {Folders, CheckboxTreeNodes} from "../utils/types";

export const FolderTree = ({setCheckedItems}) => {
    const [checked, setChecked] = useState<string[]>([]);
    const [expanded, setExpanded] = useState<string[]>([]);
    const [totalTokenCount, setTotalTokenCount] = useState(0);
    const [folders, setFolders] = useState<Folders>();
    const [nodes, setNodes] = useState<CheckboxTreeNodes[]>([]);
    const [searchTerm, setSearchTerm] = useState('');
    const [filteredNodes, setFilteredNodes] = useState<CheckboxTreeNodes[]>([]);

    useEffect(() => {
        const fetchFolders = async () => {
            try {
                const response = await fetch('/api/folders');
                const data = await response.json();
                setFolders(data);
                const convertedNodes = convertToNodes(data);
                setNodes(convertedNodes);
                setFilteredNodes(convertedNodes);
            } catch (error) {
                console.error('Error fetching folders:', error);
            }
        };
        fetchFolders();
    }, []);

    useEffect(() => {
        if (searchTerm) {
            const filtered = filterNodes(nodes, searchTerm.toLowerCase());
            setFilteredNodes(filtered);
            setExpanded(getExpandedNodes(filtered));
        } else {
            setFilteredNodes(nodes);
            setExpanded([]);
        }
    }, [searchTerm, nodes]);

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

    const filterNodes = (nodes: CheckboxTreeNodes[], term: string): CheckboxTreeNodes[] => {
        return nodes.reduce((acc: CheckboxTreeNodes[], node) => {
            if (node.label.toLowerCase().includes(term) || node.value.toLowerCase().includes(term)) {
                acc.push(node);
            } else if (node.children) {
                const filteredChildren = filterNodes(node.children, term);
                if (filteredChildren.length > 0) {
                    acc.push({...node, children: filteredChildren});
                }
            }
            return acc;
        }, []);
    };

    const getExpandedNodes = (nodes: CheckboxTreeNodes[]): string[] => {
        return nodes.reduce((acc: string[], node) => {
            if (node.children) {
                acc.push(node.value);
                acc.push(...getExpandedNodes(node.children));
            }
            return acc;
        }, []);
    };

    return (
        <div className="folder-tree-panel">
            <h3 className={`token-count ${getTokenCountClass()}`}>
                Tokens: {totalTokenCount.toLocaleString()} / 160,000
            </h3>
            <input
                type="text"
                placeholder="Search folders and files..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="search-input"
            />
            {folders ? (
                <CheckboxTree
                    nodes={filteredNodes}
                    checked={checked}
                    expanded={expanded}
                    onCheck={checked => {
                        setChecked(checked);
                        setCheckedItems(checked);
                        calculateTotalTokenCount(checked);
                    }}
                    onExpand={expanded => setExpanded(expanded)}
                />
            ) : (
                <div>Loading Folders...</div>
            )}
        </div>
    );
};
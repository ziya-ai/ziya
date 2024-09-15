import React, {useEffect, useState} from 'react';
import {Input, Tabs, Tree, TreeDataNode, Button} from 'antd';
import {useFolderContext} from '../context/FolderContext';
import {TokenCountDisplay} from "./TokenCountDisplay";
import union from 'lodash/union';
import {ChatHistory} from "./ChatHistory";

const {TabPane} = Tabs;

const {Search} = Input;

export const FolderTree: React.FC = () => {
    const {
        folders,
        treeData,
        checkedKeys,
        setCheckedKeys
    } = useFolderContext();

    const [filteredTreeData, setFilteredTreeData] = useState<TreeDataNode[]>([]);
    const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
    const [searchValue, setSearchValue] = useState('');
    const [autoExpandParent, setAutoExpandParent] = useState(true);
    const [allKeys, setAllKeys] = useState<React.Key[]>([]);
    const [matchedKeys, setMatchedKeys] = useState<React.Key[]>([]);

    useEffect(() => {
        if (searchValue) {
            const {filteredData, expandedKeys} = filterTreeData(treeData, searchValue);
            setFilteredTreeData(filteredData);
            setExpandedKeys(expandedKeys);
            
            // Update matchedKeys
            const getMatchedKeys = (nodes: TreeDataNode[]): React.Key[] => {
                return nodes.reduce((keys, node) => {
                    return [...keys, node.key, ...(node.children ? getMatchedKeys(node.children) : [])];
                }, [] as React.Key[]);
            };
            setMatchedKeys(getMatchedKeys(filteredData));
        } else {
            setFilteredTreeData(treeData);
            setExpandedKeys([]);
            setMatchedKeys([]);
        }
    }, [searchValue, treeData]);

    useEffect(() => {
        const getAllKeys = (nodes: TreeDataNode[]): React.Key[] => {
            return nodes.reduce((keys, node) => {
                return [...keys, node.key, ...(node.children ? getAllKeys(node.children) : [])];
            }, [] as React.Key[]);
        };
        setAllKeys(getAllKeys(treeData));
    }, [treeData]);

    const filterTreeData = (data: TreeDataNode[], searchValue: string): {
        filteredData: TreeDataNode[],
        expandedKeys: React.Key[]
    } => {
        const expandedKeys: React.Key[] = [];

        const filter = (node: TreeDataNode): TreeDataNode | null => {
            const nodeTitle = node.title as string;
            if (nodeTitle.toLowerCase().includes(searchValue.toLowerCase())) {
                expandedKeys.push(node.key);
                return node;
            }

            if (node.children) {
                const filteredChildren = node.children
                    .map(child => filter(child))
                    .filter((child): child is TreeDataNode => child !== null);

                if (filteredChildren.length > 0) {
                    expandedKeys.push(node.key);
                    return {...node, children: filteredChildren};
                }
            }
            return null;
        };

        const filteredData = data.map(node => filter(node)).filter((node): node is TreeDataNode => node !== null);

        return {filteredData, expandedKeys};
    };

    const handleToggleAll = () => {
        if (searchValue) {
            // If there's a search term, toggle only matched results
            if (matchedKeys.every(key => checkedKeys.includes(key as string))) {
                // Unselect all matched
                setCheckedKeys(prevKeys => prevKeys.filter(key => !matchedKeys.includes(key)));
            } else {
                // Select all matched
                setCheckedKeys(prevKeys => [...new Set([...prevKeys, ...matchedKeys])]);
            }
        } else {
            // If no search term, toggle all
            if (checkedKeys.length === allKeys.length) {
                // Unselect all
                setCheckedKeys([]);
            } else {
                // Select all
                setCheckedKeys(allKeys);
            }
        }
    };
 
    const toggleButtonText = searchValue
    ? (matchedKeys.every(key => checkedKeys.includes(key as string)) ? "Unselect Filtered" : "Select Filtered")
    : (checkedKeys.length === allKeys.length ? "Unselect All" : "Select All");

    const onExpand = (newExpandedKeys: React.Key[]) => {
        setExpandedKeys(newExpandedKeys);
        setAutoExpandParent(false);
    };

    const onCheck = React.useCallback(
        (checkedKeysValue, e) => {
            const getAllChildKeys = (node: TreeDataNode): string[] => {
                let keys: string[] = [node.key as string];
                if (node.children) {
                    node.children.forEach(child => {
                        keys = keys.concat(getAllChildKeys(child));
                    });
                }
                return keys;
            };

            const getAllParentKeys = (key: React.Key, tree: TreeDataNode[]): string[] => {
                let parentKeys: string[] = [];
                const findParent = (currentKey: React.Key, nodes: TreeDataNode[]): React.Key | null => {
                    for (let i = 0; i < nodes.length; i++) {
                        const node = nodes[i];
                        if (node.children && node.children.some(child => child.key === currentKey)) {
                            parentKeys.push(node.key as string);
                            return node.key;
                        } else if (node.children) {
                            const foundParent = findParent(currentKey, node.children);
                            if (foundParent) {
                                parentKeys.push(node.key as string);
                                return foundParent;
                            }
                        }
                    }
                    return null;
                };

                findParent(key, tree);
                return parentKeys;
            };

            if (e.checked || e.selected) {
                if (e.node.children?.length) {
                    const keysToAdd = getAllChildKeys(e.node);
                    setCheckedKeys(prevKeys =>
                        union(prevKeys as string[], keysToAdd)
                    );
                } else {
                    setCheckedKeys(prevKeys =>
                        union(prevKeys as string[], [e.node.key as string])
                    );
                }
            } else {
                const keysToRemove = e.node.children?.length ? getAllChildKeys(e.node) : [e.node.key as string];
                const parentKeys = getAllParentKeys(e.node.key, treeData);
                setCheckedKeys(prevKeys =>
                    (prevKeys as string[]).filter(key => !keysToRemove.includes(key) && !parentKeys.includes(key))
                );
            }
        },
        [searchValue, treeData]
    );

    const getParentKey = (key: React.Key, tree: TreeDataNode[]): React.Key => {
        let parentKey: React.Key;
        for (let i = 0; i < tree.length; i++) {
            const node = tree[i];
            if (node.children) {
                if (node.children.some((item) => item.key === key)) {
                    parentKey = node.key;
                } else if (getParentKey(key, node.children)) {
                    parentKey = getParentKey(key, node.children);
                }
            }
        }
        return parentKey!;
    };

    const onSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
        const value = e.target.value;
        setSearchValue(value);
        setAutoExpandParent(true);
    };

    const titleRender = (nodeData) => (
        <span style={{userSelect: 'text', cursor: 'text'}}>{nodeData.title}</span>
    );

    return (
        <div className="folder-tree-panel">
            <Tabs defaultActiveKey="1">
                <TabPane tab="File Explorer" key="1">
                    <TokenCountDisplay/>
                    <Search style={{marginBottom: 8}} placeholder="Search folders" onChange={onSearch}
                             allowClear
                    />
                    <Button 
                        onClick={handleToggleAll} 
                        style={{marginBottom: 8, width: '100%'}}
                    >{toggleButtonText}</Button>
                    {folders ? (
                        <Tree
                            checkable
                            onExpand={onExpand}
                            expandedKeys={expandedKeys}
                            autoExpandParent={autoExpandParent}
                            onCheck={onCheck}
                            checkedKeys={checkedKeys}
                            treeData={filteredTreeData}
                            titleRender={titleRender}
                        />
                    ) : (
                        <div>Loading Folders...</div>
                    )}
                </TabPane>
                <TabPane tab="Chat History" key="2">
                    <ChatHistory/>
                </TabPane>
            </Tabs>
        </div>
    );
};

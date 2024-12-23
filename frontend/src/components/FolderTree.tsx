import React, {useEffect, useState, useCallback} from 'react';
import {Input, Tabs, Tree, TreeDataNode} from 'antd';
import {useFolderContext} from '../context/FolderContext';
import {TokenCountDisplay} from "./TokenCountDisplay";
import union from 'lodash/union';
import {ChatHistory} from "./ChatHistory";
import {useTheme} from '../context/ThemeContext';


const {TabPane} = Tabs;

const {Search} = Input;

interface FolderTreeProps {
    isPanelCollapsed: boolean;
}

export const FolderTree: React.FC<FolderTreeProps> = ({ isPanelCollapsed }) => {
    const {
        folders,
        treeData,
        checkedKeys,
        setCheckedKeys,
	expandedKeys,
	setExpandedKeys
    } = useFolderContext();
    const [modelId, setModelId] = useState<string>('');
    const {isDarkMode} = useTheme();


    const [filteredTreeData, setFilteredTreeData] = useState<TreeDataNode[]>([]);
    const [searchValue, setSearchValue] = useState('');
    const [autoExpandParent, setAutoExpandParent] = useState(true);

    useEffect(() => {
        if (searchValue) {
            const {filteredData, expandedKeys} = filterTreeData(treeData, searchValue);
            setFilteredTreeData(filteredData);
            setExpandedKeys(expandedKeys);
        } else {
            setFilteredTreeData(treeData);
            setExpandedKeys([]);
        }
    }, [searchValue, treeData]);

        const fetchModelId = useCallback(async () => {
        try {
            const response = await fetch('/api/model-id');
            const data = await response.json();
            setModelId(data.model_id);
        } catch (error) {
            console.error('Error fetching model ID:', error);
        }
    }, []);

    useEffect(() => {
        fetchModelId();
    }, [fetchModelId]);

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

    const onExpand = (keys: React.Key[]) => {
        setExpandedKeys(keys);
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
        <span style={{
            userSelect: 'text',
            cursor: 'text',
            color: isDarkMode ? '#ffffff' : '#000000',
        }}>
            {nodeData.title}
        </span>
    );

    return (
            <div className={`folder-tree-panel ${isPanelCollapsed ? 'collapsed' : ''}`}>
	                <Tabs
                defaultActiveKey="1"
		style={{
                    height: '100%',
                    display: 'flex',
                    flexDirection: 'column',
                    color: isDarkMode ? '#ffffff' : undefined,
                    overflow: 'hidden'
                }}
                items={[
                    {
                        key: '1',
                        label: 'File Explorer',
                        children: (
                            <>
                                <TokenCountDisplay/>
	                        <div style={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    height: '100%',
                                    overflow: 'hidden',
                                    position: 'relative'
                                }}>
                                <Search
                                    style={{
                                        marginBottom: 8,
                                        backgroundColor: isDarkMode ? '#1f1f1f' : undefined,
                                    }}
                                    placeholder="Search folders"
                                    onChange={onSearch}
                                    allowClear
                                />
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
				        style={{
                                            background: 'transparent',
                                            color: isDarkMode ? '#ffffff' : '#000000',
					    height: 'calc(100% - 40px)',
                                            overflow: 'auto',
                                            position: 'relative'
                                        }}
                                        className={isDarkMode ? 'dark' : ''}
                                    />
                                ) : (
                                    <div>Loading Folders...</div>
                                )}
				</div>
                            </>
                        ),
                    },
                    {
                        key: '2',
                        label: 'Chat History',
                        children: <ChatHistory/>,
                    }
                ]}
            />
            <div className="model-id-display">
                {modelId && <span>Model: {modelId}</span>}
            </div>
        </div>
    );
};

import React, {useEffect, useState, useCallback} from 'react';
import {Input, Tabs, Tree, TreeDataNode, Button, message} from 'antd';
import {useFolderContext} from '../context/FolderContext';
import {Folders} from '../utils/types';
import {useChatContext} from '../context/ChatContext';
import {TokenCountDisplay} from "./TokenCountDisplay";
import union from 'lodash/union';
import {ChatHistory} from "./ChatHistory";
import {useTheme} from '../context/ThemeContext';
import {ModelConfigButton} from './ModelConfigButton';
import {ReloadOutlined, FolderOutlined, MessageOutlined} from '@ant-design/icons';
import { convertToTreeData } from '../utils/folderUtil';
const {TabPane} = Tabs;

const {Search} = Input;

interface FolderTreeProps {
    isPanelCollapsed: boolean;
}

const ACTIVE_TAB_KEY = 'ZIYA_ACTIVE_TAB';
const DEFAULT_TAB = '1'; // File Explorer tab

export const FolderTree: React.FC<FolderTreeProps> = ({ isPanelCollapsed }) => {
    const {
        folders,
        treeData,
	setTreeData,
        checkedKeys,
        setCheckedKeys,
	expandedKeys,
	setExpandedKeys
    } = useFolderContext();
    const [modelId, setModelId] = useState<string>('');
    const {isDarkMode} = useTheme();
    const {currentConversationId} = useChatContext();
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [activeTab, setActiveTab] = useState(() => localStorage.getItem(ACTIVE_TAB_KEY) || DEFAULT_TAB);
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

    // Save active tab whenever it changes
    useEffect(() => {
        localStorage.setItem(ACTIVE_TAB_KEY, activeTab);
    }, [activeTab]);

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

    const refreshFolders = async () => {
        setIsRefreshing(true);
        try {
            const response = await fetch('/api/folders?refresh=true');
            if (!response.ok) {
                throw new Error('Failed to refresh folders');
            }
	    const data: Folders = await response.json();

            // Sort the tree data recursively
            const sortTreeData = (nodes: TreeDataNode[]): TreeDataNode[] => {
		return nodes.sort((a, b) =>
                    String(a.title).toLowerCase()
                        .localeCompare(String(b.title).toLowerCase())
                )
                    .map(node => ({
                        ...node,
                        children: node.children ? sortTreeData(node.children) : undefined
                    }));
            };

	    const sortedData = sortTreeData(convertToTreeData(data));
	    console.debug('Refreshed and sorted folder structure:', { nodeCount: sortedData.length });

            setTreeData(sortedData);
            message.success('Folder structure refreshed');
        } catch (err) {
            console.error('Failed to refresh folders:', err);
            message.error('Failed to refresh folders');
        } finally {
            setIsRefreshing(false);
        }
    };

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
	    <TokenCountDisplay/>
            <Tabs 
                activeKey={activeTab}
                defaultActiveKey="1"
                destroyInactiveTabPane={false}
                style={{
                    height: '100%',
                    display: 'flex',
                    flexDirection: 'column',
                    color: isDarkMode ? '#ffffff' : undefined,
                    overflow: 'hidden',
		    margin: '0 -8px'
                }}
                onChange={setActiveTab}
                items={[
                    {
                        key: '1',
			label: (
                            <span>
                                <FolderOutlined style={{ marginRight: 8 }} />
                                File Explorer
                            </span>
                        ),
                        children: (
                            <>
                                <div style={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    height: '100%',
                                    overflow: 'hidden',
				    padding: '0 8px'
                                }}>
                                    <div style={{
                                        flex: 1,
                                        overflowY: 'auto',
                                        overflowX: 'hidden'
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
                                            <>
                                                <Button
                                                    icon={<ReloadOutlined spin={isRefreshing}/>}
                                                    onClick={refreshFolders}
                                                    style={{marginBottom: 8}}
                                                    loading={isRefreshing}
                                                >
                                                    Refresh Files
                                                </Button>
                                                <Tree
                                                    checkable
                                                    onExpand={onExpand}
                                                    expandedKeys={expandedKeys}
                                                    autoExpandParent={autoExpandParent}
                                                    onCheck={onCheck}
                                                    checkedKeys={checkedKeys}
                                                treeData={searchValue ? filteredTreeData : treeData}
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
                                            </>
                                        ) : (
                                            <div>Loading Folders...</div>
                                        )}
                                    </div>
                                </div>
                            </>
                        )
                    },
                    {
                        key: '2',
			label: (
                            <span>
                                <MessageOutlined style={{ marginRight: 8 }} />
                                Chat History
                            </span>
                        ),
                        children: <ChatHistory/>
                    }
                ]}
            />
            <div className="model-id-display" style={{ 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'space-between',
                padding: '0 8px' 
            }}>
                {modelId && <span style={{ flex: 1 }}>Model: {modelId}</span>}
                {modelId && <ModelConfigButton modelId={modelId} />}
            </div>
        </div>
    );
};

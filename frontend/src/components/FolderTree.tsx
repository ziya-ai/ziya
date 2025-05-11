import React, { useEffect, useState, useCallback, useRef, useMemo, useLayoutEffect } from 'react';
import { Input, Tabs, Tree, TreeDataNode, Button, message } from 'antd';
import { useFolderContext } from '../context/FolderContext';
import { Folders } from '../utils/types';
import { useChatContext } from '../context/ChatContext';
import { TokenCountDisplay } from "./TokenCountDisplay";
import union from 'lodash/union';
import { debounce } from 'lodash';
import { ChatHistory } from "./ChatHistory";
import { useTheme } from '../context/ThemeContext';
import { ModelConfigButton } from './ModelConfigButton';
import { ReloadOutlined, FolderOutlined, MessageOutlined } from '@ant-design/icons';
import { FolderButton } from './FolderButton';
import { convertToTreeData } from '../utils/folderUtil';
const { TabPane } = Tabs;

const { Search } = Input;

interface FolderTreeProps {
    isPanelCollapsed: boolean;
}

// Create a cache for token calculations to avoid redundant logs
const tokenCalculationCache = new Map<string, { included: number, total: number }>();
const DEBUG_LOGGING_ENABLED = false; // Set to false to disable verbose logging

const ACTIVE_TAB_KEY = 'ZIYA_ACTIVE_TAB';
const DEFAULT_TAB = '1'; // File Explorer tab

export const FolderTree = React.memo(({ isPanelCollapsed }: FolderTreeProps) => {
    const {
        treeData,
        setTreeData,
        checkedKeys,
        setCheckedKeys,
        expandedKeys,
        setExpandedKeys,
        getFolderTokenCount
    } = useFolderContext();

    // Extract only the specific values needed from ChatContext
    // to prevent unnecessary re-renders
    const [modelId, setModelId] = useState<string>('');
    const { isDarkMode } = useTheme();

    // Use a more selective approach to extract only what we need from ChatContext
    const chatContext = useChatContext();
    const currentConversationId = chatContext.currentConversationId;
    const currentFolderId = chatContext.currentFolderId;
    const chatFolders = chatContext.folders;
    const folderFileSelections = chatContext.folderFileSelections;
    const setFolderFileSelections = chatContext.setFolderFileSelections;

    const { folders } = useFolderContext(); // Keep this to get the proper Folders type

    // Add state to track panel width
    const [panelWidth, setPanelWidth] = useState<number>(300);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [activeTab, setActiveTab] = useState(() => localStorage.getItem(ACTIVE_TAB_KEY) || DEFAULT_TAB);
    const [filteredTreeData, setFilteredTreeData] = useState<TreeDataNode[]>([]);
    const [searchValue, setSearchValue] = useState('');
    const [autoExpandParent, setAutoExpandParent] = useState(true);
    const [modelDisplayName, setModelDisplayName] = useState<string>('');

    // Add ref for the panel element
    const panelRef = useRef<HTMLDivElement>(null);

    // Add effect to track panel width
    useEffect(() => {
        if (!panelRef.current) return;

        const resizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                setPanelWidth(entry.contentRect.width);
                // Dispatch custom event for other components to react to width change
                window.dispatchEvent(new CustomEvent('folderPanelResize', {
                    detail: { width: entry.contentRect.width }
                }));
            }
        });

        resizeObserver.observe(panelRef.current);
        return () => resizeObserver.disconnect();
    }, []);

    const debouncedSearch = useCallback(debounce((value: string) => {
        if (searchValue) {
            const { filteredData, expandedKeys } = filterTreeData(treeData, searchValue);
            setFilteredTreeData(filteredData);
            setExpandedKeys(expandedKeys);
        } else {
            setFilteredTreeData(treeData);
            setExpandedKeys([]);
        }
    }, 300), [treeData]);

    // Save active tab whenever it changes
    useEffect(() => {
        localStorage.setItem(ACTIVE_TAB_KEY, activeTab);
    }, [activeTab]);

    // Update model info when it changes
    const updateModelInfo = useCallback(async () => {
        try {
            const response = await fetch('/api/current-model');
            const data = await response.json();
            setModelId(data.model_id);
            setModelDisplayName(data.display_model_id || data.model_alias || data.model_id);
            console.info(`Updated model info: ${data.model_id} (${data.display_model_id || 'no display name'})`);
        } catch (error) {
            console.error('Error fetching model info:', error);
            // Fallback to basic model ID if detailed info fails
            fetchModelId();
        }
    }, []);

    const fetchModelId = useCallback(async () => {
        try {
            const response = await fetch('/api/model-id');
            const data = await response.json();
            setModelId(data.model_id);
            if (!modelDisplayName) {
                setModelDisplayName(data.model_id);
            }
        } catch (error) {
            console.error('Error fetching model ID:', error);
        }
    }, []);

    useEffect(() => {
        fetchModelId();
        updateModelInfo();

        // Listen for model changes
        const handleModelChange = () => {
            console.log("Model change detected, updating model info");
            // Use setTimeout to ensure the backend has updated
            updateModelInfo();
            // And check again after a delay to ensure we have the latest
            setTimeout(updateModelInfo, 500);
        };

        window.addEventListener('modelChanged', handleModelChange);
        return () => {
            window.removeEventListener('modelChanged', handleModelChange);
        };
    }, [fetchModelId, updateModelInfo]);

    // Also listen for model settings changes
    useEffect(() => {
        const handleSettingsChanged = () => {
            updateModelInfo();
        };
        window.addEventListener('modelSettingsChanged', handleSettingsChanged);
        return () => window.removeEventListener('modelSettingsChanged', handleSettingsChanged);
    }, [updateModelInfo]);

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
                    return { ...node, children: filteredChildren };
                }
            }
            return null;
        };

        const filteredData = data.map(node => filter(node)).filter((node): node is TreeDataNode => node !== null);

        return { filteredData, expandedKeys };
    };

    const onExpand = (keys: React.Key[]) => {
        setExpandedKeys(keys);
        setAutoExpandParent(false);
    };

    // Save folder-specific file selections when they change
    useEffect(() => {
        if (currentFolderId) {
            const folder = chatFolders.find(f => f.id === currentFolderId);
            if (folder && !folder.useGlobalContext) {
                // Store the current selections for this folder
                setFolderFileSelections(prev => {
                    const next = new Map(prev);
                    next.set(currentFolderId, [...checkedKeys].map(key => String(key)));
                    return next;
                });
            }
        }
    }, [checkedKeys, currentFolderId, chatFolders, setFolderFileSelections]);

    // Update checked keys when folder changes
    useEffect(() => {
        // This is handled in FolderContext now
    }, [currentFolderId, chatFolders, folderFileSelections]);


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

    // Alternative approach: Calculate tokens directly from the tree structure
    const calculateTokens = useCallback((path: string): number => {
        if (!folders) return 0; // Early return if folders is undefined

        // Navigate through the folders structure to find the node
        let current: any = folders;
        const parts = path.split('/');

        for (const part of parts) {
            if (!current[part]) {
                return 0; // Path not found
            }
            current = current[part];
        }

        // If we found the node, return its token count
        return current.token_count || 0;
    }, [folders]);

    // Get all selected paths under a directory
    const getSelectedPaths = (basePath: string): string[] => {
        return checkedKeys
            .map(key => key.toString())
            .filter(path => path.startsWith(basePath + '/') || path === basePath);
    };

    // Calculate included tokens for a directory using direct path checking
    const getDirectIncludedTokens = useCallback((node: TreeDataNode): { included: number, total: number } => {
        const nodePath = node.key as string;

        // Calculate total tokens by summing up all children
        let totalTokens = 0;

        if (folders) {
            // If this is a file, get its token count directly
            if (!node.children || node.children.length === 0) {
                totalTokens = getFolderTokenCount(nodePath, folders);
            } else {
                // For directories, sum up tokens from all children recursively
                // This ensures we get the total tokens for the entire subtree
                totalTokens = calculateTotalTokensForDirectory(nodePath, folders);
            }
        }

        // If this node is directly checked, all tokens are included
        if (checkedKeys.includes(node.key)) {
            return { included: totalTokens, total: totalTokens };
        }

        // If this is a directory, check if any children are selected
        if (node.children && node.children.length > 0) {
            let includedTokens = 0;

            // Process each child node
            for (const child of node.children) {
                const childPath = child.key as string;

                // Case 1: Child is directly selected
                if (checkedKeys.includes(child.key)) {
                    const childTokens = folders ? getFolderTokenCount(childPath, folders) : 0;
                    includedTokens += childTokens;
                }
                // Case 2: Child is a directory that might have selected descendants
                else if (child.children && child.children.length > 0) {
                    // Recursively check this child directory
                    const childResult = getDirectIncludedTokens(child);
                    includedTokens += childResult.included;
                }
            }

            return { included: includedTokens, total: totalTokens };
        }

        return { included: 0, total: totalTokens };
    }, [folders, checkedKeys]);

    // Helper function to calculate total tokens for a directory by traversing the folder structure
    const calculateTotalTokensForDirectory = useCallback((dirPath: string, folderData: Folders): number => {
        if (!folderData) return 0;

        // Navigate to the directory in the folder structure
        let current = folderData;
        const parts = dirPath.split('/');

        for (let i = 0; i < parts.length; i++) {
            const part = parts[i];
            if (!current[part]) return 0;
            if (i === parts.length - 1) break; // Don't navigate into the last part
            current = current[part].children || {};
        }

        // Get the directory node
        const dirNode = current[parts[parts.length - 1]];
        if (!dirNode) return 0;

        // Return the token_count if it exists (this should be the sum of all files in the directory)
        return dirNode.token_count || 0;
    }, [getFolderTokenCount, calculateTokens, checkedKeys]);

    // Original implementation with improved debugging
    const getIncludedTokens = useCallback((node: TreeDataNode): { included: number, total: number } => {
        if (!folders) return { included: 0, total: 0 };

        const nodePath = node.key as string;

        // Get the total tokens for this path directly from the folders data
        // Extract the token count from the title if needed
        let totalTokens = 0;

        // Try to get token count from the folders structure
        totalTokens = folders ? getFolderTokenCount(nodePath, folders) : 0;

        // If we couldn't get tokens from folders, try to extract from title
        if (totalTokens === 0) {
            const titleMatch = String(node.title).match(/\(([0-9,]+) tokens\)/);
            if (titleMatch && titleMatch[1]) {
                totalTokens = parseInt(titleMatch[1].replace(/,/g, ''), 10);
            }
        }

        // If this node is checked, all its tokens are included
        if (checkedKeys.includes(node.key)) {
            return { included: totalTokens, total: totalTokens };
        }

        // If no children, no tokens are included
        if (!node.children || node.children.length === 0) {
            return { included: 0, total: totalTokens };
        }

        // Initialize included tokens counter
        let includedTokens = 0;

        // Process each child node
        for (const child of node.children) {
            const childPath = child.key as string;

            // Case 1: Child is directly selected
            if (checkedKeys.includes(child.key)) {
                const childTokens = getFolderTokenCount(childPath, folders);
                includedTokens += childTokens;
                console.log(`Direct selection: ${childPath} adds ${childTokens} tokens`);
            }
            // Case 2: Child is a directory that might have selected descendants
            else if (child.children && child.children.length > 0) {
                // Recursively check this child directory
                const childResult = getIncludedTokens(child);

                // Only add if there are included tokens
                if (childResult.included > 0) {
                    includedTokens += childResult.included;
                    console.log(`Partial selection: ${childPath} adds ${childResult.included}/${childResult.total} tokens`);
                }
            }
        }

        // Debug output
        if (includedTokens > 0) {
            console.log(`Directory ${nodePath}: ${includedTokens}/${totalTokens} tokens included`);
        } else {
            console.log(`Directory ${nodePath}: No tokens included (total: ${totalTokens})`);
        }

        // Return both the included and total token counts
        console.log(`Node ${nodePath}: included=${includedTokens}, total=${totalTokens}`);
        return { included: includedTokens, total: totalTokens };
    }, [folders, checkedKeys, getFolderTokenCount]);

    // Use the alternative implementation for now
    const getTokensForDisplay = useCallback((node: TreeDataNode): { included: number, total: number } => {
        return getDirectIncludedTokens(node);
    }, [getDirectIncludedTokens]);

    const titleRender = (nodeData: any): React.ReactNode => {
        // Check if this is a directory (has children)
        if (nodeData.children && nodeData.children.length > 0) {
            // Calculate included vs total tokens
            const { included, total } = getTokensForDisplay(nodeData);
            const nodePath = nodeData.key as string;

            // Extract just the folder name without the token count
            const titleText = String(nodeData.title).split(' (')[0];

            // Show fraction for partially included directories, otherwise show total
            const isPartiallyIncluded = included > 0 && included < total;
            const hasTokens = total > 0;  // Keep this line to define hasTokens

            // Only log if debug logging is enabled
            if (DEBUG_LOGGING_ENABLED) {
                console.log(`Rendering ${titleText}: included=${included}, total=${total}, partial=${isPartiallyIncluded}`);
            }

            // Cache the calculation result
            tokenCalculationCache.set(nodePath, { included, total });

            const titleContent = (
                <span style={{
                    userSelect: 'text',
                    cursor: 'text',
                    color: isDarkMode ? '#ffffff' : '#000000',
                }}>
                    {titleText} {hasTokens ? (isPartiallyIncluded ?
                        `(${included.toLocaleString()}/${total.toLocaleString()} tokens)` :
                        `(${total.toLocaleString()} tokens)`) : '(0 tokens)'}
                </span>
            );

            // Return memoized content
            return titleContent;
        }

        // For files, just show the original title
        return (
            <span style={{
                userSelect: 'text',
                cursor: 'text',
                color: isDarkMode ? '#ffffff' : '#000000',
            }}>
                {nodeData.title}
            </span>
        );
    };

    // Memoize the title render function to prevent unnecessary recalculations
    const memoizedTitleRender = useCallback((nodeData: any): React.ReactNode => {
        // Use node key as cache key
        const cacheKey = nodeData.key as string;

        // For directories with children, check if we have a cached calculation
        if (nodeData.children && nodeData.children.length > 0 && tokenCalculationCache.has(cacheKey)) {
            // Skip recalculation and just render the title
            return titleRender(nodeData);
        }

        // For uncached items or files, calculate normally
        return titleRender(nodeData);
    }, [folders, checkedKeys, getFolderTokenCount, isDarkMode]);

    // Memoize the entire tree component to prevent re-renders when typing in chat
    const memoizedTree = useMemo(() => {
        return (
            <Tree
                checkable
                onExpand={onExpand}
                expandedKeys={expandedKeys}
                autoExpandParent={autoExpandParent}
                onCheck={onCheck}
                checkedKeys={checkedKeys}
                treeData={searchValue ? filteredTreeData : treeData}
                titleRender={memoizedTitleRender}
                style={{
                    background: 'transparent',
                    color: isDarkMode ? '#ffffff' : '#000000',
                    height: 'calc(100% - 40px)',
                    overflow: 'auto',
                    position: 'relative'
                }}
                className={isDarkMode ? 'dark' : ''}
            />
        );
    }, [
        onExpand,
        expandedKeys,
        autoExpandParent,
        onCheck,
        checkedKeys,
        searchValue,
        filteredTreeData,
        treeData,
        memoizedTitleRender,
        isDarkMode
    ]);

    // Memoize the search component
    const memoizedSearch = useMemo(() => {
        return (
            <Search
                style={{ marginBottom: 8, backgroundColor: isDarkMode ? '#1f1f1f' : undefined }}
                placeholder="Search folders"
                onChange={onSearch}
                id="folder-search-input"
                allowClear
            />
        );
    }, [onSearch, isDarkMode]);

    return (
        <div ref={panelRef} className={`folder-tree-panel ${isPanelCollapsed ? 'collapsed' : ''}`}>
            <TokenCountDisplay />
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
                                        {memoizedSearch}
                                        {folders ? (
                                            <>
                                                <Button
                                                    icon={<ReloadOutlined spin={isRefreshing} />}
                                                    onClick={refreshFolders}
                                                    style={{ marginBottom: 8 }}
                                                    loading={isRefreshing}
                                                >
                                                    Refresh Files
                                                </Button>
                                                {memoizedTree}
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
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                                <span>
                                    <MessageOutlined style={{ marginRight: 8 }} />
                                    Chat History
                                </span>
                                <div style={{ marginLeft: 'auto', marginRight: '-8px' }}>
                                    <FolderButton />
                                </div>
                            </div>
                        ),
                        children: <ChatHistory />
                    }
                ]}
            >
                {activeTab === '2' && <div style={{ display: 'none' }} id="panel-width-tracker" data-width={panelWidth}></div>}
            </Tabs>
            <div className="model-id-display" style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0 8px'
            }}>
                {modelId && <span style={{ flex: 1 }}>Model: {modelDisplayName || modelId}</span>}
                {modelId && <ModelConfigButton modelId={modelId} />}
            </div>
        </div>
    );
}, (prevProps, nextProps) => prevProps.isPanelCollapsed === nextProps.isPanelCollapsed);

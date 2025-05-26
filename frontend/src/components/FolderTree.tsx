import React, { useEffect, useState, useCallback, useRef, useMemo, useLayoutEffect } from 'react';
import { Input, Tabs, Tree, TreeDataNode, Button, message, Tooltip } from 'antd';
import { useFolderContext } from '../context/FolderContext';
import { Folders } from '../utils/types';
import { useChatContext } from '../context/ChatContext';
import { TokenCountDisplay } from "./TokenCountDisplay";
import { FolderOutlined, FileOutlined } from '@ant-design/icons'; // Import icons
import { debounce } from 'lodash';
import { ChatHistory } from "./ChatHistory";
import { ModelConfigButton } from './ModelConfigButton';
import { ReloadOutlined, MessageOutlined, PlusOutlined } from '@ant-design/icons';
import { convertToTreeData } from '../utils/folderUtil';
import MUIChatHistory from './MUIChatHistory';
import { MUIFileExplorer } from './MUIFileExplorer';
import { useTheme } from '../context/ThemeContext';

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
        getFolderTokenCount,
        searchValue, setSearchValue
    } = useFolderContext();

    // Extract only the specific values needed from ChatContext
    // to prevent unnecessary re-renders
    const [modelId, setModelId] = useState<string>('');
    const { startNewChat, createFolder } = useChatContext();
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
    const [filteredTreeData, setFilteredTreeData] = useState<TreeDataNode[]>([]);
    const [autoExpandParent, setAutoExpandParent] = useState(true);
    const [modelDisplayName, setModelDisplayName] = useState<string>('');

    // Add ref for the panel element
    const panelRef = useRef<HTMLDivElement>(null);
    const [activeTab, setActiveTab] = useState(() => localStorage.getItem(ACTIVE_TAB_KEY) || DEFAULT_TAB);

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

    const debouncedSearch = useCallback(debounce((value: string) => {
        if (value) {
            const { filteredData, expandedKeys } = filterTreeData(treeData, value);
            setFilteredTreeData(filteredData);
            setExpandedKeys(expandedKeys);
            setAutoExpandParent(true);
        } else {
            setFilteredTreeData([]);
            setAutoExpandParent(false);
        }
    }, 300), [treeData]);

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

    const onExpand = useCallback((keys: React.Key[]) => {
        setExpandedKeys(keys);
        setAutoExpandParent(false);
    }, [setExpandedKeys]);

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
                    setCheckedKeys(prevKeys => {
                        const newKeys = new Set([...prevKeys as string[], ...keysToAdd]);
                        return Array.from(newKeys);
                    });
                } else {
                    setCheckedKeys(prevKeys => {
                        const newKeys = new Set([...prevKeys as string[], e.node.key as string]);
                        return Array.from(newKeys);
                    });
                }
            } else {
                const keysToRemove = e.node.children?.length ? getAllChildKeys(e.node) : [e.node.key as string];
                const parentKeys = getAllParentKeys(e.node.key, treeData);
                setCheckedKeys(prevKeys =>
                    (prevKeys as string[]).filter(key => !keysToRemove.includes(key) && !parentKeys.includes(key))
                );
            }
        },
        [treeData]
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
        return checkedKeys.map(key => String(key)).filter(path => path.startsWith(basePath + '/') || path === basePath)
    };

    // Calculate included tokens for a directory using direct path checking
    const getDirectIncludedTokens = useCallback((node: TreeDataNode): { included: number, total: number } => {
        const nodePath = node.key as string;

        if (!node.children || node.children.length === 0) { // It's a file
            const fileTotalTokens = folders ? getFolderTokenCount(nodePath, folders) : 0;
            const fileIncludedTokens = checkedKeys.includes(node.key) ? fileTotalTokens : 0;
            return { included: fileIncludedTokens, total: fileTotalTokens };
        }

        // It's a directory
        let directoryTotalTokens = 0;
        let directoryIncludedTokens = 0;

        if (node.children && node.children.length > 0) {

            for (const child of node.children) {
                const childResult = getDirectIncludedTokens(child); // Recursive call
                directoryTotalTokens += childResult.total;
                directoryIncludedTokens += childResult.included;
            }
        }

        // If the directory itself is checked, then all its content is included.
        if (checkedKeys.includes(node.key)) {
            directoryIncludedTokens = directoryTotalTokens;
        }

        return { included: directoryIncludedTokens, total: directoryTotalTokens };
    }, [folders, checkedKeys, getFolderTokenCount]);

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

    // Handle creating a new folder
    const handleCreateNewFolder = async () => {
        try {
            // Use the currently selected folder as parent, or null for root level
            const parentFolderId = currentFolderId || null;
            console.log('Creating new folder with parent:', parentFolderId);
            // If creating in a parent folder, ensure it's expanded
            if (parentFolderId && !expandedKeys.includes(parentFolderId)) {
                setExpandedKeys(prev => [...prev, parentFolderId]);
            }
            const newFolderId: string = await createFolder('New Folder', parentFolderId);
            message.success('Folder created successfully');
            
            // Note: FolderTree doesn't have inline editing, so we just create the folder
            // Users can rename it later via the context menu
        } catch (error) {
            console.error('Error creating folder:', error);
            message.error('Failed to create folder');
        }
    };

    const handleCreateNewChat = async () => {
        try {
            // Use the currently selected folder ID, or null for root level
            const targetFolderId = currentFolderId || null;
            console.log('Creating new chat in folder:', targetFolderId);

            // If creating in a folder, ensure it's expanded
            if (targetFolderId && !expandedKeys.includes(targetFolderId)) {
                setExpandedKeys(prev => [...prev, targetFolderId]);
            }

            await startNewChat(targetFolderId);
        } catch (error) {
            console.error('Error creating new chat:', error);
            message.error('Failed to create new chat');
        }
    };

    // Add effect to position buttons when Chat History tab is active
    useEffect(() => {
        if (activeTab === '2') {
            // Add the buttons to the tab bar after render
            setTimeout(() => {
                const tabBar = document.querySelector('.ant-tabs-nav');
                const chatHistoryTab = document.querySelector('[data-node-key="2"]');
                if (tabBar && chatHistoryTab && !document.getElementById('chat-history-actions')) {
                    const actionsDiv = document.createElement('div');
                    actionsDiv.id = 'chat-history-actions';
                    actionsDiv.style.cssText = `
                        position: absolute;
                        right: 8px;
                        top: 50%;
                        transform: translateY(-50%);
                        display: flex;
                        gap: 2px;
                        z-index: 10;
                    `;

                    // Create buttons
                    const folderBtn = document.createElement('button');
                    folderBtn.innerHTML = '<span class="anticon anticon-folder"><svg viewBox="64 64 896 896" focusable="false" data-icon="folder" width="1em" height="1em" fill="currentColor" aria-hidden="true"><path d="M880 298.4H521L403.7 186.2a8.15 8.15 0 00-5.5-2.2H144c-17.7 0-32 14.3-32 32v592c0 17.7 14.3 32 32 32h736c17.7 0 32-14.3 32-32V330.4c0-17.7-14.3-32-32-32z"></path></svg></span>';
                    folderBtn.className = 'ant-btn ant-btn-text ant-btn-sm';
                    folderBtn.style.cssText = 'min-width: 24px; padding: 0 4px;';
                    folderBtn.title = `New Folder${currentFolderId ? ' in current folder' : ' at root level'}`;
                    folderBtn.onclick = (e) => {
                        e.stopPropagation();
                        handleCreateNewFolder();
                    };

                    const chatBtn = document.createElement('button');
                    chatBtn.innerHTML = '<span class="anticon anticon-plus"><svg viewBox="64 64 896 896" focusable="false" data-icon="plus" width="1em" height="1em" fill="currentColor" aria-hidden="true"><path d="M482 152h60q8 0 8 8v704q0 8-8 8h-60q-8 0-8-8V160q0-8 8-8z"></path><path d="M176 474h672q8 0 8 8v60q0 8-8 8H176q-8 0-8-8v-60q0-8 8-8z"></path></svg></span>';
                    chatBtn.className = 'ant-btn ant-btn-text ant-btn-sm';
                    chatBtn.style.cssText = 'min-width: 24px; padding: 0 4px;';
                    chatBtn.title = `New Chat${currentFolderId ? ' in current folder' : ' at root level'}`;
                    chatBtn.onclick = (e) => {
                        e.stopPropagation();
                        handleCreateNewChat();
                    };

                    actionsDiv.appendChild(folderBtn);
                    actionsDiv.appendChild(chatBtn);
                    tabBar.appendChild(actionsDiv);
                }
            }, 0);
        } else {
            // Remove buttons when not on Chat History tab
            const existingActions = document.getElementById('chat-history-actions');
            if (existingActions) {
                existingActions.remove();
            }
        }

        return () => {
            const existingActions = document.getElementById('chat-history-actions');
            if (existingActions) {
                existingActions.remove();
            }
        };
    }, [activeTab, currentFolderId]);

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
                // Recursively check this child directory if folders is defined
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
        const isDirectory = nodeData.children && nodeData.children.length > 0;
        const nodePath = nodeData.key as string;
        const titleText = String(nodeData.title).split(' (')[0]; // Get clean title

        let tokenDisplay = <span style={{ fontSize: '0.8em', fontFamily: 'monospace', color: isDarkMode ? '#aaa' : '#555' }}>(0 tokens)</span>;

        if (isDirectory) {
            const { included, total } = getTokensForDisplay(nodeData);

            // Cache the calculation result
            tokenCalculationCache.set(nodePath, { included, total });

            if (total > 0) {
                const includedDisplay = included > 0 ? (
                    <strong style={{ color: isDarkMode ? '#fff' : '#000' }}>{included.toLocaleString()}</strong>
                ) : (
                    included.toLocaleString()
                );
                tokenDisplay = (
                    <span style={{ fontSize: '0.8em', fontFamily: 'monospace', color: isDarkMode ? '#aaa' : '#555' }}>
                        ({includedDisplay}/{total.toLocaleString()} tokens)
                    </span>
                );
            }
        } else { // It's a file
            const { total } = getTokensForDisplay(nodeData); // For files, included is same as total if checked
            if (total > 0) {
                const isSelectedAndHasTokens = checkedKeys.includes(nodeData.key) && total > 0;
                if (isSelectedAndHasTokens) {
                    tokenDisplay = (
                        <span style={{ fontSize: '0.8em', fontFamily: 'monospace', color: isDarkMode ? '#aaa' : '#555' }}>
                            (<strong style={{ color: isDarkMode ? '#fff' : '#000' }}>{total.toLocaleString()}</strong> tokens)
                        </span>
                    );
                } else {
                    tokenDisplay = (
                        <span style={{
                            fontSize: '0.8em',
                            fontFamily: 'monospace',
                            color: isDarkMode ? '#aaa' : '#555',
                        }}>
                            ({total.toLocaleString()} tokens)
                        </span>
                    );
                }
            }
        }

        return (
            <div style={{
                display: 'flex',
                alignItems: 'center', // Vertically align items
                width: '100%',      // Ensure this div takes full available width
                // border: '1px dashed red' // DEBUG: See the bounds of this div
            }}>
                {/* This span will contain the icon and title, and grow to take available space */}
                <span style={{
                    display: 'flex',
                    alignItems: 'center',
                    userSelect: 'text',
                    cursor: 'text',
                    color: isDarkMode ? '#ffffff' : '#000000',
                    flexGrow: 1, /* Allow this to grow */
                    overflow: 'hidden', /* Prevent long titles from breaking layout */
                    textOverflow: 'ellipsis', /* Show ... for very long titles */
                    whiteSpace: 'nowrap', /* Keep title on one line */
                }}>
                    {isDirectory ? <FolderOutlined style={{ marginRight: '8px', color: isDarkMode ? '#69c0ff' : '#1890ff' }} /> : <FileOutlined style={{ marginRight: '8px', color: isDarkMode ? '#91d5ff' : '#40a9ff' }} />}
                    {/* Bolding for file title if selected and has tokens */}
                    {checkedKeys.includes(nodeData.key) && !isDirectory && getTokensForDisplay(nodeData).total > 0 ? (
                        <strong>{titleText}</strong>
                    ) : (
                        <>{titleText}</>
                    )}
                </span>
                <span style={{
                    flexShrink: 0, /* Prevent this from shrinking */
                    marginLeft: 'auto', /* Push to the right */
                    paddingLeft: '8px' /* Add some space from the title */
                }}>{tokenDisplay}</span>
            </div >
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
                                    overflow: 'hidden'
                                }}>
                                    <Search
                                        style={{ marginBottom: 8, backgroundColor: isDarkMode ? '#1f1f1f' : undefined }}
                                        placeholder="Search folders"
                                        onChange={onSearch}
                                        value={searchValue}
                                        allowClear
                                    />
                                    <Button
                                        icon={<ReloadOutlined />}
                                        onClick={refreshFolders}
                                        loading={isRefreshing}
                                        style={{ marginBottom: 8 }}
                                    >
                                        Refresh Files
                                    </Button>
                                    <div style={{
                                        flex: 1, height: 'calc(100% - 80px)', overflow: 'auto'
                                    }}>
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
                        children: <MUIChatHistory /> // This is the MUI version being used
                    },
                ]}
            >
                {activeTab === '2' && <div style={{ display: 'none' }} id="panel-width-tracker" data-width={panelWidth}></div>}
            </Tabs>
            <div className="model-id-display" style={{
                display: 'flex',
                alignItems: 'center',
            }}>
                {modelId && <span style={{ flex: 1 }}>Model: {modelDisplayName || modelId}</span>}
                {modelId && <ModelConfigButton modelId={modelId} />}
            </div>
        </div >
    );
}, (prevProps, nextProps) => prevProps.isPanelCollapsed === nextProps.isPanelCollapsed);

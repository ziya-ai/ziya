import React, { useState, useCallback, useEffect, memo, useRef } from 'react';
import { List, Button, Input, message, Modal, Tree, Dropdown, Menu, Space, MenuProps, Typography } from 'antd';
import {
    DeleteOutlined,
    EditOutlined,
    DownloadOutlined,
    UploadOutlined,
    LoadingOutlined,
    CheckCircleOutlined,
    FolderOutlined,
    MoreOutlined,
    DownOutlined
} from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';
import { useTheme } from '../context/ThemeContext';
import { Conversation, ConversationFolder } from '../utils/types';
import { db } from '../utils/db';

import { FolderButton } from './FolderButton';

interface ChatHistoryItemProps {
    conversation: Conversation;
    isLoadingConversation: boolean;
    currentConversationId: string;
    streamingConversations: Set<string>;
    isDarkMode: boolean;
    onConversationClick: (id: string) => void;
    onEdit: (e: React.MouseEvent, id: string) => void;
    onDelete: (e: React.MouseEvent, id: string) => void;
    editingId: string | null;
    onTitleChange: (id: string, title: string) => void;
    onTitleBlur: (id: string, title: string) => void;
}

const ChatHistoryItem: React.FC<ChatHistoryItemProps> = memo(({
    conversation,
    isLoadingConversation,
    currentConversationId,
    streamingConversations,
    isDarkMode,
    onConversationClick,
    onEdit,
    onDelete,
    editingId,
    onTitleChange,
    onTitleBlur
}) => {
    // Add a ref to measure the container width
    const containerRef = useRef<HTMLDivElement>(null);
    const [containerWidth, setContainerWidth] = useState<number>(0);

    // Use effect to measure container width
    useEffect(() => {
        if (!containerRef.current) return;

        const resizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                setContainerWidth(entry.contentRect.width);
            }
        });

        resizeObserver.observe(containerRef.current);

        return () => {
            resizeObserver.disconnect();
        };
    }, []);

    // Listen for panel resize events
    useEffect(() => {
        const handlePanelResize = (e: CustomEvent) => {
            if (containerRef.current) {
                setContainerWidth(containerRef.current.offsetWidth);
            }
        };
        window.addEventListener('folderPanelResize', handlePanelResize as EventListener);
        return () => window.removeEventListener('folderPanelResize', handlePanelResize as EventListener);
    }, []);

    return (
        <List.Item
            onClick={() => conversation.id !== currentConversationId && onConversationClick(conversation.id)}
            style={{
                cursor: 'pointer',
                backgroundColor: conversation.id === currentConversationId
                    ? (isDarkMode ? '#177ddc' : '#e6f7ff')
                    : 'transparent',
                color: conversation.id === currentConversationId && isDarkMode ? '#ffffff' : undefined,
                opacity: isLoadingConversation ? 0.5 : 1,
                padding: '8px',
                borderRadius: '4px',
                display: 'flex',
                alignItems: 'flex-start',
                width: '100%',
                boxSizing: 'border-box',
                pointerEvents: isLoadingConversation ? 'none' : 'auto'
            }}
        >
            <div
                ref={containerRef}
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    width: '100%',
                    position: 'relative',
                    minWidth: 0,
                    flex: 1,
                }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    {editingId === conversation.id ? (
                        <Input
                            defaultValue={conversation.title}
                            onPressEnter={(e) => onTitleChange(conversation.id, e.currentTarget.value)}
                            onBlur={(e) => onTitleBlur(conversation.id, e.currentTarget.value)}
                            style={{ width: '100%' }}
                            onClick={(e) => e.stopPropagation()}
                        />
                    ) : (
                        <div style={{
                            position: 'relative',
                            width: '100%',
                            paddingLeft: conversation.hasUnreadResponse &&
                                conversation.id !== currentConversationId ?
                                '24px' : '0'
                        }}>
                            {conversation.hasUnreadResponse &&
                                conversation.id !== currentConversationId && (
                                    <CheckCircleOutlined
                                        style={{
                                            position: 'absolute',
                                            left: '4px',
                                            top: '50%',
                                            transform: 'translateY(-50%)',
                                            fontSize: '12px',
                                            color: isDarkMode ? '#49aa19' : '#52c41a',
                                            zIndex: 1
                                        }}
                                    />
                                )}

                            {/* Calculate title length based on container width */}
                            <div className="chat-history-title" style={{
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                                // Use container width to determine max width
                                // Subtract 80px for action buttons and padding
                                maxWidth: containerWidth > 0 ?
                                    `${Math.max(0, containerWidth - 80)}px` :
                                    'calc(100% - 80px)',
                                paddingRight: '65px'
                            }}>
                                {conversation.title}
                                {streamingConversations.has(conversation.id) && (
                                    <div style={{
                                        fontSize: '12px',
                                        color: isDarkMode ? '#177ddc' : '#1890ff',
                                        marginTop: '4px',
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '4px'
                                    }}>
                                        <LoadingOutlined />
                                        Receiving response...
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                    <div className="chat-history-actions" style={{
                        padding: '0 4px',
                        display: 'flex',
                        gap: '2px',
                        position: 'absolute',
                        right: 0,
                        top: 0,
                    }}>
                        <Button
                            type="text"
                            icon={<EditOutlined />}
                            onClick={(e) => onEdit(e, conversation.id)}
                            style={{ display: 'flex', alignItems: 'center', height: '24px', padding: '0 4px' }}
                        />
                        <Button
                            type="text"
                            icon={<DeleteOutlined />}
                            onClick={(e) => onDelete(e, conversation.id)}
                            style={{ display: 'flex', alignItems: 'center', height: '24px', padding: '0 4px' }}
                        />
                    </div>
                </div>
            </div>
        </List.Item>
    );
});

ChatHistoryItem.displayName = 'ChatHistoryItem';

export const ChatHistory: React.FC = () => {
    const {
        conversations,
        setCurrentConversationId,
        currentConversationId,
        setConversations,
        currentMessages,
        isLoadingConversation,
        streamingConversations,
        startNewChat,
        loadConversation,
        folders, setFolders, currentFolderId, setCurrentFolderId, createFolder, updateFolder, deleteFolder, moveConversationToFolder
    } = useChatContext();
    const { isDarkMode } = useTheme();
    const [isRepairing, setIsRepairing] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);

    // Preserve current conversation when component mounts
    const [folderTreeData, setFolderTreeData] = useState<any[]>([]);
    const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
    const [isInitialized, setIsInitialized] = useState(false);
    const [dragState, setDragState] = useState<{ key: string, type: 'conversation' | 'folder' } | null>(null);
    const [dropTargetKey, setDropTargetKey] = useState<string | null>(null);

    useEffect(() => {
        if (currentConversationId && currentMessages.length > 0) {
            console.debug('Preserving current conversation:', {
                id: currentConversationId,
                messageCount: currentMessages.length
            });
        }
    }, []);

    // Set initialized when conversations are loaded
    useEffect(() => {
        if (conversations.length > 0) {
            setIsInitialized(true);
        }
    }, [conversations]);

    // Function to convert folders and conversations to tree data
    const buildFolderTree = useCallback(() => {
        // First, create a map of all folders
        const folderMap = new Map();
        folders.forEach(folder => {
            // Initialize with empty children array to count conversations later
            folderMap.set(folder.id, {
                key: folder.id,
                title: folder.name,
                isLeaf: false,
                children: [],
                folder: folder,
                conversationCount: 0,
                isFolder: true
            });
        });

        // Then, build the tree structure
        const rootItems: any[] = [];
        folders.forEach(folder => {
            const node = folderMap.get(folder.id);
            if (folder.parentId && folderMap.has(folder.parentId)) {
                // This is a child folder
                folderMap.get(folder.parentId).children.push(node);
            } else {
                // This is a root folder
                rootItems.push(node);
            }
        });

        // Add conversations to their respective folders
        const activeConversations = conversations.filter(conv => conv.isActive !== false);
        activeConversations.forEach(conv => {
            // Count conversations per folder
            if (conv.folderId && folderMap.has(conv.folderId)) {
                folderMap.get(conv.folderId).conversationCount++;
            }
            const conversationNode = {
                key: `conv-${conv.id}`,
                title: conv.title,
                isLeaf: true,
                conversation: conv,
                isConversation: true
            };

            if (conv.folderId && folderMap.has(conv.folderId)) {
                // Add to specific folder
                folderMap.get(conv.folderId).children.push(conversationNode);
            } else {
                // Add to root
                rootItems.push(conversationNode);
            }
        });

        // Sort each level - folders first, then conversations
        const sortNodes = (nodes: any[]) => {
            return nodes.sort((a, b) => {
                // Folders come before conversations
                if (!a.isLeaf && b.isLeaf) return -1;
                if (a.isLeaf && !b.isLeaf) return 1;

                // Sort folders by name
                if (!a.isLeaf && !b.isLeaf) {
                    return a.title.localeCompare(b.title);
                }

                // Sort conversations by last accessed time (most recent first)
                if (a.isLeaf && b.isLeaf) {
                    const aTime = a.conversation?.lastAccessedAt ?? 0;
                    const bTime = b.conversation?.lastAccessedAt ?? 0;
                    return bTime - aTime;
                }

                return 0;
            });
        };

        // Apply sorting to all levels
        const sortRecursive = (nodes: any[]) => {
            const sorted = sortNodes(nodes);
            sorted.forEach(node => {
                if (node.children && node.children.length > 0) {
                    node.children = sortRecursive(node.children);
                }
            });
            return sorted;
        };

        return sortRecursive(rootItems);
    }, [folders, conversations]);

    // Update folder tree data when folders or conversations change
    useEffect(() => {
        if (isInitialized) {
            const treeData = buildFolderTree();
            setFolderTreeData(treeData);
        }
    }, [folders, conversations, buildFolderTree, isInitialized]);

    // Periodically check for updates when the component is mounted
    useEffect(() => {
        let isSubscribed = true;
        const checkForUpdates = async () => {
            try {
                const saved = await db.getConversations();
                // Only log and update if there's an actual change
                const hasChanged = saved.some(savedConv => {
                    const existingConv = conversations.find(conv => conv.id === savedConv.id);
                    // If conversation doesn't exist or versions don't match
                    return !existingConv ||
                        (savedConv._version || 0) > (existingConv._version || 0);
                });
                if (hasChanged) {
                    let mergedConversations = conversations.map(conv => {
                        const savedConv = saved.find(s => s.id === conv.id);
                        // Keep current conversation if it's being edited or is more recent
                        if (conv.id === currentConversationId && editingId === conv.id) {
                            return conv;
                        }
                        return savedConv || conv;
                    });

                    // Add any new conversations that don't exist locally
                    mergedConversations = [...mergedConversations, ...saved.filter(savedConv =>
                        !mergedConversations.some(conv => conv.id === savedConv.id)
                    )];
                    // Sort by last accessed time
                    mergedConversations.sort((a, b) => (b.lastAccessedAt || 0) - (a.lastAccessedAt || 0));
                    setConversations(mergedConversations);

                }
            } catch (error) {
                console.error('Error checking for conversation updates:', error);
            }
        };

        const interval = setInterval(checkForUpdates, 2000);
        return () => { isSubscribed = false; clearInterval(interval); };
    }, [conversations, setConversations]);

    // Handle tree selection
    const onSelect = (selectedKeys: React.Key[], info: any) => {
        const key = selectedKeys[0] as string;
        if (!key) return;

        if (key.startsWith('conv-')) {
            // This is a conversation
            const conversationId = key.substring(5);
            handleConversationClick(conversationId);
        } else {
            // This is a folder
            setCurrentFolderId(key);
        }
    };

    // Handle tree expansion
    const onExpand = (expandedKeys: React.Key[]) => {
        setExpandedKeys(expandedKeys);
    };

    // Handle folder context menu
    const onFolderContextMenu = (folder: ConversationFolder) => {
        const menuItems: MenuProps['items'] = [
            {
                key: 'edit',
                icon: <EditOutlined />,
                label: 'Rename',
                onClick: () => handleEditFolder(folder)
            },
            {
                key: 'delete',
                icon: <DeleteOutlined />,
                label: 'Delete',
                onClick: () => handleDeleteFolder(folder.id)
            }
        ];
        
        return <Menu items={menuItems} />;
    };
    const handleConversationClick = useCallback(async (conversationId: string) => {
        try {
            setLoadError(null);
            if (conversationId !== currentConversationId && !isLoadingConversation) {
                await loadConversation(conversationId);
                console.debug('Loaded conversation:', conversationId);
            }
        } catch (error) {
            setLoadError(error instanceof Error ? error.message : 'Failed to load conversation');
        }
    }, [currentConversationId, isLoadingConversation, loadConversation]);

    // Create a wrapper for handleEditClick that works with Menu.Item onClick
    const handleMenuEditClick = (conversationId: string) => {
        // Create a synthetic event that matches what handleEditClick expects
        const syntheticEvent = { stopPropagation: () => { } } as React.MouseEvent;
        handleEditClick(syntheticEvent, conversationId);
    };

    // Create a wrapper for handleDeleteConversation that works with Menu.Item onClick
    const handleMenuDeleteConversation = (conversationId: string) => {
        // Create a synthetic event that matches what handleDeleteConversation expects
        const syntheticEvent = { stopPropagation: () => { } } as React.MouseEvent;
        handleDeleteConversation(syntheticEvent, conversationId);
    };

    // Handle folder editing
    const handleEditFolder = (folder: ConversationFolder) => {
        Modal.confirm({
            title: 'Rename Folder',
            content: (
                <Input
                    defaultValue={folder.name}
                    onChange={(e) => (folder.name = e.target.value)}
                />
            ),
            onOk: async () => {
                try {
                    await updateFolder(folder);
                    message.success('Folder renamed successfully');
                } catch (error) {
                    message.error('Failed to rename folder');
                }
            }
        });
    };

    // Handle folder deletion
    const handleDeleteFolder = (folderId: string) => {
        Modal.confirm({
            title: 'Delete Folder',
            content: 'Are you sure you want to delete this folder? All conversations will be moved to the root level.',
            onOk: async () => {
                try {
                    await deleteFolder(folderId);
                    message.success('Folder deleted successfully');
                } catch (error) {
                    message.error('Failed to delete folder');
                }
            }
        });
    };

    // Handle moving conversation to folder
    const handleMoveConversation = async (conversationId: string, folderId: string | null) => {
        try {
            await moveConversationToFolder(conversationId, folderId);
            message.success('Conversation moved successfully');
        } catch (error) {
            message.error('Failed to move conversation');
        }
    };

    // Handle conversation context menu
    const onConversationContextMenu = (conversation: Conversation) => {
        // Create menu items for each folder as MenuProps items
        const folderMenuItems: MenuProps['items'] = folders.map(folder => ({
            key: folder.id,
            label: folder.name,
            onClick: () => handleMoveConversation(conversation.id, folder.id)
        }));

        // Add the root option
        folderMenuItems.push({ type: 'divider' });
        folderMenuItems.push({
            key: 'root',
            label: 'Root',
            onClick: () => handleMoveConversation(conversation.id, null)
        });

        // Create the menu items
        const menuItems: MenuProps['items'] = [
            {
                key: 'edit',
                icon: <EditOutlined />,
                label: 'Rename',
                onClick: () => handleMenuEditClick(conversation.id)
            },
            {
                key: 'move',
                icon: <FolderOutlined />,
                label: 'Move to',
                children: folderMenuItems
            },
            {
                key: 'delete',
                icon: <DeleteOutlined />,
                label: 'Delete',
                onClick: () => handleMenuDeleteConversation(conversation.id)
            }
        ];

        return (
            <Menu items={menuItems} />
        );
    };

    const exportConversations = async () => {
        try {
            console.debug('Starting conversation export');
            const data = await db.exportConversations();
            console.debug('Export data received:', {
                dataSize: data.length,
                conversationCount: JSON.parse(data).length,
                timestamp: new Date().toISOString()
            });
            const blob = new Blob([data], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ziya-conversations-${new Date().toISOString()}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            message.success('Conversations exported successfully');
        } catch (error) {
            console.error('Error exporting conversations:', error);
            message.error('Failed to export conversations');
        }
    };

    const importConversations = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (file) {
            console.debug('Starting import of file:', {
                name: file.name,
                size: file.size,
                type: file.type
            });

            const reader = new FileReader();
            reader.onload = async (e) => {
                try {
                    const content = e.target?.result as string;
                    console.debug('File content loaded, attempting to parse');

                    // Validate JSON format
                    const parsedContent = JSON.parse(content);
                    if (!Array.isArray(parsedContent)) {
                        throw new Error('Invalid import format - expected array of conversations');
                    }

                    console.debug('Importing conversations:', {
                        count: parsedContent.length
                    });
                    await db.importConversations(content);
                    const newConversations = await db.getConversations();
                    setConversations(newConversations);
                    message.success('Conversations imported successfully');
                } catch (error) {
                    console.error('Failed to import conversations:', error);
                    message.error(error instanceof Error ? error.message : 'Failed to import conversations');
                }
            };
            reader.readAsText(file);
        }
        // Reset the input
        event.target.value = '';
    };

    const handleEditClick = (e: React.MouseEvent, conversationId: string) => {
        e.stopPropagation();
        setEditingId(conversationId);
    };

    const handleTitleChange = async (conversationId: string, newTitle: string) => {
        try {
            // Update state first
            const updatedConversations = conversations.map(conv =>
                conv.id === conversationId ? { ...conv, title: newTitle } : conv
            );

            // Persist to IndexedDB before updating state
            await db.saveConversations(updatedConversations);

            // Update state after successful save
            setConversations(updatedConversations);
            setEditingId(null);
        } catch (error) {
            console.error('Error saving conversation title:', error);
            message.error('Failed to save conversation title');
        }
    };

    const handleTitleBlur = (conversationId: string, newTitle: string) => {
        handleTitleChange(conversationId, newTitle);
    };

    const handleDeleteConversation = async (e: React.MouseEvent, conversationId: string) => {
        e.stopPropagation();
        try {
            console.debug('Deleting conversation:', {
                id: conversationId,
                currentActive: conversations.filter(c => c.isActive).length,
                isCurrentConversation: conversationId === currentConversationId
            });
            // first persist to IndexedDB
            const updatedConversations = conversations.map(conv =>
                conv.id === conversationId
                    ? { ...conv, isActive: false }
                    : conv);
            await db.saveConversations(updatedConversations);

            console.debug('After marking conversation inactive:', {
                id: conversationId,
                newActive: updatedConversations.filter(c => c.isActive).length
            });

            // Then update React state
            setConversations(updatedConversations);

            // If we're deleting the current conversation, start a new one if no others remain
            if (conversationId === currentConversationId) {
                // Get remaining active conversations sorted by lastAccessedAt
                const remainingActiveConversations = updatedConversations
                    .filter(c => c.isActive)
                    .sort((a, b) => {
                        const aTime = a.lastAccessedAt ?? 0;
                        const bTime = b.lastAccessedAt ?? 0;
                        return bTime - aTime; // Sort in descending order (most recent first)
                    });

                if (remainingActiveConversations.length === 0) {
                    try {
                        await startNewChat();
                    } catch (error) {
                        console.error('Error creating new chat after deletion:', error);
                    }
                } else {
                    // Load the first remaining active conversation
                    await loadConversation(remainingActiveConversations[0].id);
                }
            }

            message.success('Conversation deleted');
        } catch (error) {
            // Revert any partial changes
            const saved = await db.getConversations();
            setConversations(saved);
            message.error('Failed to delete conversation');
            console.error('Error deleting conversation:', error);
        }
    };

    // Sort conversations by lastAccessedAt
    const sortedConversations = [...conversations].sort((a, b) => {
        const aTime = a.lastAccessedAt ?? 0;
        const bTime = b.lastAccessedAt ?? 0;
        return bTime - aTime;
    });

    // Add debug logging for folder tree data
    useEffect(() => {
        console.log('Folder tree data:', folderTreeData);
        console.log('Folders from context:', folders);
    }, [folderTreeData, folders]);

    const importConversationsFromFile = () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = (e) => {
            const target = e.target as HTMLInputElement;
            if (target && target.files) {
                importConversations({ target } as React.ChangeEvent<HTMLInputElement>);
            }
        };
        input.click();
    };

    // Handle drag start
    const onDragStart = (info: any) => {
        const { node } = info;
        if (node.isConversation) {
            setDragState({ key: node.key.substring(5), type: 'conversation' });
        } else if (node.isFolder) {
            setDragState({ key: node.key, type: 'folder' });
        }
    };
 
    // Handle drag end
    const onDragEnd = (info: any) => {
        setDropTargetKey(null);
        setDragState(null);
    };
 
    // Handle drag enter
    const onDragEnter = (info: any) => {
        setDropTargetKey(info.node.key);
    };

    // Handle drop
    const onDrop = async (info: any) => {
        const { node: targetNode, dragNode } = info;
        
        // Only handle conversation drops
        if (!dragState || dragState.type !== 'conversation') {
            return;
        }
        
        const conversationId = dragState.key;
        
        // Determine target folder
        let targetFolderId: string | null = null;
        
        if (targetNode.isFolder) {
            // Dropped on a folder
            targetFolderId = targetNode.key;
        } else if (targetNode.isConversation && targetNode.conversation.folderId) {
            // Dropped on a conversation that's in a folder
            targetFolderId = targetNode.conversation.folderId;
        }
        
        // If the target is the same as the source, do nothing
        const conversation = conversations.find(c => c.id === conversationId);
        if (conversation && conversation.folderId === targetFolderId) {
            return;
        }
        
        // Move the conversation to the target folder
        try {
            await moveConversationToFolder(conversationId, targetFolderId);
            message.success('Conversation moved successfully');
        } catch (error) {
            message.error('Failed to move conversation');
        }
        
        setDropTargetKey(null);
    };

    const renderTreeNode = (nodeData: any) => {
        if (nodeData.isLeaf) {
            // This is a conversation
            const conversation = nodeData.conversation;
            const isCurrentConversation = conversation.id === currentConversationId;
            const isStreaming = streamingConversations.has(conversation.id);
            const isDragTarget = dropTargetKey === nodeData.key && dragState?.type === 'conversation';

            return (
                <div
                    style={{
                        display: 'flex',
                        border: isDragTarget ? '1px dashed #1890ff' : 'none',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        backgroundColor: isCurrentConversation ? (isDarkMode ? '#177ddc' : '#e6f7ff') : 'transparent',
                        padding: '4px 8px',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        opacity: dragState && dragState.key === conversation.id.substring(5) ? 0.5 : 1,
                        width: '100%'
                    }}
                >
                    <div style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {conversation.hasUnreadResponse && !isCurrentConversation && (
                            <CheckCircleOutlined
                                style={{
                                    marginRight: '4px',
                                    fontSize: '12px',
                                    color: isDarkMode ? '#49aa19' : '#52c41a'
                                }}
                            />
                        )}
                        {nodeData.title}
                        {isStreaming && (
                            <div style={{
                                fontSize: '12px',
                                color: isDarkMode ? '#177ddc' : '#1890ff',
                                marginTop: '4px',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px'
                            }}>
                                <LoadingOutlined />
                                Receiving response...
                            </div>
                        )}
                    </div>
                    <Dropdown
                        overlay={onConversationContextMenu(conversation)}
                        trigger={['click']}
                        placement="bottomRight"
                    >
                        <Button
                            type="text"
                            icon={<MoreOutlined />}
                            size="small"
                            onClick={(e) => e.stopPropagation()}
                        />
                    </Dropdown>
                </div>
            );
        } else {
            // This is a folder
            const folder = nodeData.folder;
            const isDragTarget = dropTargetKey === nodeData.key && dragState?.type === 'conversation';

            return (
                <div
                    style={{
                        display: 'flex',
                        border: isDragTarget ? '1px dashed #1890ff' : 'none',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '4px 8px',
                        opacity: dragState && dragState.key === folder.id ? 0.5 : 1,
                        fontWeight: 'bold'
                    }}
                >
                    <div style={{ flex: 1 }}>
                        <FolderOutlined style={{ marginRight: '8px', color: isDarkMode ? '#1890ff' : '#1890ff' }} />
                        {nodeData.title}
                        <Typography.Text
                            type="secondary"
                            style={{
                                marginLeft: '8px',
                                fontSize: '12px'
                            }}
                        >
                            ({nodeData.conversationCount || 'empty'})
                        </Typography.Text>
                    </div>
                    <Dropdown
                        overlay={onFolderContextMenu(folder)}
                        trigger={['click']}
                        placement="bottomRight"
                    >
                        <Button
                            type="text"
                            icon={<MoreOutlined />}
                            size="small"
                            onClick={(e) => e.stopPropagation()}
                        />
                    </Dropdown>
                </div>
            );
        }
    };

    const renderListView = () => (
        <List
            className="chat-history-list"
            style={{ width: '100%' }}
            dataSource={sortedConversations.filter(conv => conv.isActive !== false)}
            renderItem={(conversation) => (
                <ChatHistoryItem
                    conversation={conversation}
                    isLoadingConversation={isLoadingConversation}
                    currentConversationId={currentConversationId}
                    streamingConversations={streamingConversations}
                    isDarkMode={isDarkMode}
                    onConversationClick={handleConversationClick}
                    onEdit={handleEditClick}
                    onDelete={handleDeleteConversation}
                    editingId={editingId}
                    onTitleChange={handleTitleChange}
                    onTitleBlur={handleTitleBlur}
                />
            )}
            footer={
                <>
                    <div className="chat-history-footer">
                        <Button icon={<DownloadOutlined />} onClick={exportConversations}>
                            Export
                        </Button>
                        <Button
                            icon={<UploadOutlined />}
                            onClick={() => {
                                const input = document.createElement('input');
                                input.type = 'file';
                                input.accept = '.json';
                                input.onchange = (e) => {
                                    const target = e.target as HTMLInputElement;
                                    if (target && target.files) {
                                        importConversations({ target } as React.ChangeEvent<HTMLInputElement>);
                                    }
                                };
                                input.click();
                            }}
                        >
                            Import
                        </Button>
                    </div>
                </>
            }
        />
    );

    const renderTreeView = () => (
        <div className="chat-history-container" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div className="chat-history-content" style={{ flex: 1, overflow: 'auto' }}>
                {loadError && (
                    <div style={{ padding: '8px', color: 'red' }}>
                        Error: {loadError}
                    </div>
                )}

                <Tree
                    showLine={{ showLeafIcon: false }}
                    showIcon={false}
                    switcherIcon={<DownOutlined />}
                    onSelect={onSelect}
                    onExpand={onExpand}
                    expandedKeys={expandedKeys}
                    treeData={folderTreeData}
                    draggable
                    onDragStart={onDragStart}
                    onDragEnter={onDragEnter}
                    onDrop={onDrop}
                    titleRender={renderTreeNode}
                    style={{
                        background: 'transparent',
                        color: isDarkMode ? '#ffffff' : '#000000'
                    }}
                    className={isDarkMode ? 'dark' : ''}
                />
            </div>

            <div className="chat-history-footer" style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '8px 16px',
                borderTop: `1px solid ${isDarkMode ? '#303030' : '#e8e8e8'}`
            }}>
                <Button icon={<DownloadOutlined />} onClick={exportConversations}>Export</Button>
                <Button icon={<UploadOutlined />} onClick={() => importConversationsFromFile()}>Import</Button>
            </div>
        </div>
    );

    return folders.length > 0 ? renderTreeView() : renderListView();
};

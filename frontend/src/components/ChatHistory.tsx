import React, { useState, useCallback, useEffect, memo, useRef } from 'react';
import { List, Button, Input, message, Modal, Tree, Dropdown, Menu, Space, MenuProps, Typography, Form, Switch, Radio, Divider } from 'antd';
import {
    DeleteOutlined,
    EditOutlined,
    DownloadOutlined,
    UploadOutlined,
    PlusOutlined,
    LoadingOutlined,
    CheckCircleOutlined,
    FolderOutlined,
    MoreOutlined,
    DownOutlined,
    CopyOutlined,
    CompressOutlined,
    ToolOutlined,
    PushpinOutlined
} from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';
import { useTheme } from '../context/ThemeContext';
import { Conversation, ConversationFolder, Message } from '../utils/types';
import { db } from '../utils/db';
import { v4 as uuidv4 } from 'uuid';
import { useFolderContext } from '../context/FolderContext';

import type { DataNode, TreeProps } from 'antd/es/tree';
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
        folders, setFolders, currentFolderId, setCurrentFolderId, createFolder, updateFolder, deleteFolder, moveConversationToFolder,
        folderFileSelections, setFolderFileSelections
    } = useChatContext();
    const { isDarkMode } = useTheme();
    const [isRepairing, setIsRepairing] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [editingFolderId, setEditingFolderId] = useState<string | null>(null);

    // Preserve current conversation when component mounts
    const [folderTreeData, setFolderTreeData] = useState<any[]>([]);
    const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
    const [isInitialized, setIsInitialized] = useState(false);
    const [pendingFolderId, setPendingFolderId] = useState<string | null>(null);
    const [dragState, setDragState] = useState<{ key: string, type: 'conversation' | 'folder' } | null>(null);
    const [dropTargetKey, setDropTargetKey] = useState<string | null>(null);

    const { checkedKeys, setCheckedKeys } = useFolderContext();

    // Add state for pinned folders
    const [pinnedFolders, setPinnedFolders] = useState<Set<string>>(new Set());

    // Create a form instance at the component level to avoid React Hook rules violation
    const [folderConfigForm] = Form.useForm();

    // Define custom node type that extends DataNode
    interface CustomTreeNode extends DataNode {
        isConversation?: boolean;
        isFolder?: boolean;
        conversation?: Conversation;
    }

    // Load pinned folders from localStorage on mount
    useEffect(() => {
        try {
            const savedPinnedFolders = localStorage.getItem('ZIYA_PINNED_FOLDERS');
            if (savedPinnedFolders) {
                setPinnedFolders(new Set(JSON.parse(savedPinnedFolders)));
            }
        } catch (error) {
            console.error('Error loading pinned folders:', error);
        }
    }, []);

    // Save pinned folders to localStorage when they change
    useEffect(() => {
        if (pinnedFolders.size > 0) {
            localStorage.setItem('ZIYA_PINNED_FOLDERS', JSON.stringify([...pinnedFolders]));
        }
    }, [pinnedFolders]);

    // Function to toggle pin status
    const togglePinFolder = (folderId: string) => {
        setPinnedFolders(prev => {
            const newPinned = new Set(prev);
            if (newPinned.has(folderId)) {
                newPinned.delete(folderId);
                message.info('Folder unpinned');
            } else {
                newPinned.add(folderId);
                message.success('Folder pinned to top');
            }
            return newPinned;
        });
    };

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
                isFolder: true,
                isPinned: pinnedFolders.has(folder.id),
                lastActivityTime: 0 // Will be updated with conversation times

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
            // Count conversations per folder and track last activity time
            if (conv.folderId && folderMap.has(conv.folderId)) {
                const folderNode = folderMap.get(conv.folderId);
                folderNode.conversationCount++;

                // Update folder's last activity time if this conversation is more recent
                const convTime = conv.lastAccessedAt || 0;
                if (convTime > folderNode.lastActivityTime) {
                    folderNode.lastActivityTime = convTime;
                }
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
                // Pinned folders come first
                if (a.isPinned && !b.isPinned) return -1;
                if (!a.isPinned && b.isPinned) return 1;

                // Folders come before conversations
                if (!a.isLeaf && b.isLeaf) return -1;
                if (a.isLeaf && !b.isLeaf) return 1;

                // Sort folders by last activity time (most recent first)
                if (!a.isLeaf && !b.isLeaf) {
                    return b.lastActivityTime - a.lastActivityTime;
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
    }, [folders, conversations, pinnedFolders]);

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

    // Function to open folder configuration dialog
    const openFolderConfig = (folder: ConversationFolder) => {
        // Get current global file selections to use as a starting point
        const currentGlobalSelections = [...checkedKeys].map(key => String(key));

        // Get folder-specific file selections if they exist
        const folderSelections = folderFileSelections.get(folder.id) || [...currentGlobalSelections];

        Modal.confirm({
            title: 'Folder Configuration',
            width: 500,
            icon: <ToolOutlined />,
            content: (
                <div style={{
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#ffffff',
                    color: isDarkMode ? '#ffffff' : '#000000',
                    padding: '16px',
                    borderRadius: '8px'
                }}>
                    <Form
                        form={folderConfigForm}
                        layout="vertical"
                        initialValues={{
                            name: folder.name,
                            contextMode: folder.useGlobalContext ? 'global' : 'folder',
                            modelMode: folder.useGlobalModel ? 'global' : 'folder',
                            systemInstructions: folder.systemInstructions || ''
                        }}
                    >
                        <Form.Item label={
                            <Typography.Text style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>
                                Folder Name
                            </Typography.Text>
                        } name="name">
                            <Input
                                style={{
                                    backgroundColor: isDarkMode ? '#141414' : '#ffffff',
                                    color: isDarkMode ? '#ffffff' : '#000000',
                                    borderColor: isDarkMode ? '#434343' : '#d9d9d9'
                                }}
                            />
                        </Form.Item>

                        <Divider style={{ borderColor: isDarkMode ? '#303030' : '#f0f0f0' }} />

                        <Form.Item label={
                            <Typography.Text style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>
                                File Context
                            </Typography.Text>
                        } name="contextMode">
                            <Radio.Group buttonStyle="solid">
                                <Radio.Button value="global">Global</Radio.Button>
                                <Radio.Button value="folder">Folder Only</Radio.Button>
                            </Radio.Group>
                        </Form.Item>

                        <Form.Item label={
                            <Typography.Text style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>
                                Model Configuration
                            </Typography.Text>
                        } name="modelMode">
                            <Radio.Group buttonStyle="solid">
                                <Radio.Button value="global">Global</Radio.Button>
                                <Radio.Button value="folder">Folder Only</Radio.Button>
                            </Radio.Group>
                        </Form.Item>

                        <Divider style={{ borderColor: isDarkMode ? '#303030' : '#f0f0f0' }} />

                        <Form.Item label={
                            <Typography.Text style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>
                                System Instructions
                            </Typography.Text>
                        } name="systemInstructions">
                            <Input.TextArea
                                autoSize={{ minRows: 4, maxRows: 12 }}
                                placeholder="Custom system instructions for this folder"
                                style={{
                                    width: '100%',
                                    backgroundColor: isDarkMode ? '#141414' : '#ffffff',
                                    color: isDarkMode ? '#ffffff' : '#000000',
                                    borderColor: isDarkMode ? '#434343' : '#d9d9d9'
                                }}
                            />
                        </Form.Item>
                    </Form>
                </div>
            ),
            onOk: async () => {
                try {
                    const values = folderConfigForm.getFieldsValue();

                    // Update folder properties
                    folder.name = values.name;
                    folder.useGlobalContext = values.contextMode === 'global';
                    folder.useGlobalModel = values.modelMode === 'global';
                    folder.systemInstructions = values.systemInstructions;

                    // If switching to folder-specific context, initialize with current global selections
                    if (values.contextMode === 'folder') {
                        setFolderFileSelections(prev => {
                            const next = new Map(prev);
                            if (!next.has(folder.id)) {
                                next.set(folder.id, [...currentGlobalSelections]);
                            }
                            return next;
                        });
                    }

                    await updateFolder(folder);
                    message.success('Folder configuration updated');
                } catch (error) {
                    message.error('Failed to update folder configuration');
                }
            }
        });
    };

    // Handle folder context menu
    const onFolderContextMenu = (folder: ConversationFolder) => {
        const isPinned = pinnedFolders.has(folder.id);
        const menuItems: MenuProps['items'] = [
            {
                key: 'edit',
                icon: <EditOutlined />,
                label: 'Rename',
                onClick: () => handleEditFolder(folder)
            },
            {
                key: 'config',
                icon: <ToolOutlined />,
                label: 'Configuration',
                onClick: () => openFolderConfig(folder)
            },
            {
                key: 'pin',
                icon: <PushpinOutlined />,
                label: isPinned ? 'Unpin' : 'Pin to Top',
                onClick: () => togglePinFolder(folder.id)
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

    // Create a wrapper for handleDeleteConversation that works with Menu.Item onClick
    const handleMenuDeleteConversation = (conversationId: string) => {
        // Create a synthetic event that matches what handleDeleteConversation expects
        const syntheticEvent = { stopPropagation: () => { }, preventDefault: () => { } } as React.MouseEvent;
        handleDeleteConversation(syntheticEvent, conversationId);
    };

    // Handle folder editing
    const handleEditFolder = (folder: ConversationFolder) => {
        setEditingFolderId(folder.id);
    };

    // Add a function to handle folder name changes
    const handleFolderNameChange = async (folderId: string, newName: string) => {
        try {
            // Find the folder
            const folder = folders.find(f => f.id === folderId);
            if (!folder) return;

            // Update the folder name
            const updatedFolder = {
                ...folder,
                name: newName
            };

            // Save to database
            await updateFolder(updatedFolder);

            // Clear editing state
            setEditingFolderId(null);

            message.success('Folder renamed successfully');
        } catch (error) {
            console.error('Error saving folder name:', error);
            message.error('Failed to save folder name');
        }
    };

    // Add a function to handle blur event
    const handleFolderNameBlur = (folderId: string, newName: string) => {
        handleFolderNameChange(folderId, newName);
    };

    // Original modal-based folder editing (removed)
    /* 
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
    */

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

    // Add fork conversation function
    const handleForkConversation = async (conversation: Conversation) => {
        try {
            // Create a copy of the conversation with a new ID
            const newId = uuidv4();
            const forkedConversation: Conversation = {
                ...conversation,
                id: newId,
                title: `Fork: ${conversation.title}`,
                lastAccessedAt: Date.now(),
                _version: Date.now(),
                hasUnreadResponse: false
            };

            // Add the forked conversation to the list
            const updatedConversations = [...conversations, forkedConversation];

            // Save to database
            await db.saveConversations(updatedConversations);

            // Update state
            setConversations(updatedConversations);

            // Switch to the new conversation
            await loadConversation(newId);

            message.success('Conversation forked successfully');
        } catch (error) {
            console.error('Error forking conversation:', error);
            message.error('Failed to fork conversation');
        }
    };

    // Add compress conversation function (placeholder)
    const handleCompressConversation = () => {
        message.info('Conversation compression is not yet implemented in Ziya.');
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
                onClick: () => setEditingId(conversation.id)
            },
            {
                key: 'fork',
                icon: <CopyOutlined />,
                label: 'Fork',
                onClick: () => handleForkConversation(conversation)
            },
            {
                key: 'compress',
                icon: <CompressOutlined />,
                label: 'Compress',
                onClick: handleCompressConversation
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
        e.preventDefault();
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

            console.log('Title changed for conversation:', conversationId, 'to:', newTitle);
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

    // Helper function to check if a folder is a descendant of another folder
    const isDescendantFolder = (folderId: string | null, potentialAncestorId: string): boolean => {
        // If folderId is null, it can't be a descendant
        if (folderId === null) {
            return false;
        }

        // Base case: if the folder is the potential ancestor, return true
        if (folderId === potentialAncestorId) {
            return true;
        }

        // Get the folder
        const folder = folders.find(f => f.id === folderId);
        if (!folder || !folder.parentId) {
            // If the folder doesn't exist or has no parent, it's not a descendant
            return false;
        }

        // Recursively check if the parent is a descendant of the potential ancestor
        return isDescendantFolder(folder.parentId, potentialAncestorId);
    };

    // Handle drop
    const onDrop = async (info: any) => {
        const { node: targetNode, dragNode, dropPosition, dropToGap } = info;

        if (!dragState) return;

        try {
            if (dragState.type === 'conversation') {
                // Handle conversation drop
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
                await moveConversationToFolder(conversationId, targetFolderId);
                message.success('Conversation moved successfully');
            } else if (dragState.type === 'folder') {
                // Handle folder drop
                const folderId = dragState.key;
                const folder = folders.find(f => f.id === folderId);

                if (!folder) return;

                // Determine target parent folder
                let targetParentId: string | null = null;

                // Handle different drop scenarios:
                // 1. dropPosition === 0 and dropToGap === true: Drop at the root level
                // 2. dropPosition === -1: Drop inside the node (make it a child)
                // 3. dropPosition > 0 and dropToGap === true: Drop between nodes

                console.log('Drop info:', { dropPosition, dropToGap, targetKey: targetNode.key });

                if (dropPosition === 0 || (dropToGap && !targetNode.isFolder)) {
                    // This is a drop to root level
                    targetParentId = null;
                    console.log('Setting targetParentId to null (root level)');
                }
                else if (!dropToGap && targetNode.isFolder) {
                    // Dropped on another folder - make it a child of that folder
                    targetParentId = targetNode.key;

                    // Prevent dropping a folder onto itself
                    if (targetParentId === folderId) {
                        return;
                    }
                    if (isDescendantFolder(targetParentId, folderId)) {
                        message.error("Cannot move a folder into one of its descendants");
                        return;
                    }
                } else if (targetNode.isConversation) {
                    // Dropped on a conversation - make it a sibling of that conversation
                    targetParentId = targetNode.conversation.folderId;
                    console.log('Setting targetParentId to conversation folder:', targetParentId);
                }

                // If the target is the same as the source, do nothing
                if (folder.parentId === targetParentId) {
                    return;
                }

                // Update the folder's parent
                const updatedFolder = {
                    ...folder,
                    parentId: targetParentId
                };

                console.log('Updating folder with new parentId:', targetParentId);
                await updateFolder(updatedFolder);
                message.success('Folder moved successfully');
            }
        } catch (error) {
            message.error(`Failed to move ${dragState.type}: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }

        setDropTargetKey(null);
    };

    const renderTreeNode = (nodeData: any) => {
        if (nodeData.isLeaf) {
            // This is a conversation
            const isEditing = editingId === nodeData.conversation?.id;
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
                        {isEditing ? (
                            <Input
                                defaultValue={conversation.title}
                                onPressEnter={(e) => handleTitleChange(conversation.id, e.currentTarget.value)}
                                onBlur={(e) => handleTitleBlur(conversation.id, e.currentTarget.value)}
                                style={{ width: '100%' }}
                                onClick={(e) => e.stopPropagation()}
                                autoFocus
                            />
                        ) : (
                            <div style={{
                                display: 'flex',
                                alignItems: 'center'
                            }}>
                                {conversation.hasUnreadResponse &&
                                    conversation.id !== currentConversationId && (
                                        <CheckCircleOutlined
                                            style={{
                                                marginRight: '4px',
                                                fontSize: '14px',
                                                color: isDarkMode ? '#49aa19' : '#52c41a'
                                            }}
                                        />
                                    )}
                                <span>{nodeData.title}</span>
                                {isStreaming && (
                                    <div style={{ fontSize: '12px', color: isDarkMode ? '#177ddc' : '#1890ff', display: 'flex', alignItems: 'center', marginLeft: '4px' }}>
                                        <LoadingOutlined />
                                        <span style={{ marginLeft: '4px' }}>Receiving response...</span>
                                    </div>
                                )}
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
            const isPinned = pinnedFolders.has(folder.id);
            const isEditing = editingFolderId === folder.id;

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
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
                        <FolderOutlined style={{ marginRight: '8px', color: isDarkMode ? '#1890ff' : '#1890ff' }} />
                        {isEditing ? (
                            <Input
                                defaultValue={folder.name}
                                onPressEnter={(e) => handleFolderNameChange(folder.id, e.currentTarget.value)}
                                onBlur={(e) => handleFolderNameBlur(folder.id, e.currentTarget.value)}
                                style={{ width: '60%' }}
                                onClick={(e) => e.stopPropagation()}
                                autoFocus
                            />
                        ) : (
                            <>
                                <span>{nodeData.title}</span>
                                {isPinned && (
                                    <PushpinOutlined style={{
                                        marginLeft: '8px',
                                        color: isDarkMode ? '#1890ff' : '#1890ff',
                                        fontSize: '12px'
                                    }} />
                                )}
                                <Typography.Text
                                    type="secondary"
                                    style={{
                                        marginLeft: '8px',
                                        fontSize: '12px'
                                    }}
                                >
                                    ({nodeData.conversationCount || 'empty'})
                                </Typography.Text>
                            </>
                        )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center' }}>
                        {/* Add New Chat button */}
                        <Button
                            type="text"
                            size="small"
                            icon={<PlusOutlined />}
                            onClick={(e) => {
                                e.stopPropagation();

                                // Set a pending folder ID to ensure we create in the right folder
                                setPendingFolderId(folder.id);

                                // If this folder is not expanded, add it to expanded keys
                                if (!expandedKeys.includes(folder.id)) {
                                    setExpandedKeys(prev => [...prev, folder.id]);
                                }

                                // Create a new chat with the specific folder ID
                                const createNewChatInFolder = async () => {
                                    try {
                                        // Force the current folder ID to be the one we clicked on
                                        await setCurrentFolderId(folder.id);
                                        await startNewChat(folder.id);
                                        setPendingFolderId(null);
                                    } catch (error) {
                                        console.error('Error creating new chat in folder:', error);
                                    }
                                };
                                createNewChatInFolder();

                            }}
                            title="New chat in this folder"
                            style={{ marginRight: '4px' }}
                        />
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
                    blockNode
                    showIcon={false}
                    switcherIcon={<DownOutlined />}
                    onSelect={onSelect}
                    onExpand={onExpand}
                    expandedKeys={expandedKeys}
                    treeData={folderTreeData}
                    draggable={{
                        icon: false,
                        nodeDraggable: (node: any) => {
                            // Allow dragging both conversations and folders
                            return node.isConversation === true || node.isFolder === true;
                        }
                    }}
                    allowDrop={({ dropNode, dropPosition }) => true} // Allow dropping anywhere
                    onDragStart={onDragStart}
                    onDragOver={(info: any) => {
                        // Log drag over information for debugging
                        console.log('Drag over:', info);
                    }}
                    onDragEnter={onDragEnter}
                    onDragEnd={onDragEnd}
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

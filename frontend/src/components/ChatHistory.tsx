import React, {useState, useCallback, useEffect} from 'react';
import {List, Button, Input, message, Modal} from 'antd';
import {
    DeleteOutlined,
    EditOutlined,
    DownloadOutlined,
    UploadOutlined,
    LoadingOutlined,
    CheckCircleOutlined
} from '@ant-design/icons';
import {useChatContext} from '../context/ChatContext';
import {useTheme} from '../context/ThemeContext';
import { db } from '../utils/db';

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
    } = useChatContext();
    const {isDarkMode} = useTheme();
    const [isRepairing, setIsRepairing] = useState(false);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);

    // Preserve current conversation when component mounts
    useEffect(() => {
        if (currentConversationId && currentMessages.length > 0) {
            console.debug('Preserving current conversation:', {
                id: currentConversationId,
                messageCount: currentMessages.length
            });
        }
    }, []);

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

    const exportConversations = async () => {
        try {
            const data = await db.exportConversations();
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
            const reader = new FileReader();
            reader.onload = async (e) => {
                try {
                    const content = e.target?.result as string;
                    await db.importConversations(content);
                    const newConversations = await db.getConversations();
                    setConversations(newConversations);
                    message.success('Conversations imported successfully');
                } catch (error) {
                    console.error('Import error:', error);
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
                conv.id === conversationId ? {...conv, title: newTitle} : conv
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

    return (
        <List
            className="chat-history-list"
	    style={{ 
	        width: '100%'
	    }}
            dataSource={sortedConversations.filter(conv => conv.isActive !== false)}
            renderItem={(conversation) => (
                <List.Item
                    key={conversation.id}
                    onClick={() => conversation.id !== currentConversationId && handleConversationClick(conversation.id)}
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
                    <div style={{
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
                                onPressEnter={(e) => handleTitleChange(conversation.id, e.currentTarget.value)}
                                onBlur={(e) => handleTitleBlur(conversation.id, e.currentTarget.value)}
                                style={{ width: '100%' }}
                                onClick={(e) => e.stopPropagation()}
                            />
                        ) : (
                            <div style={{
                                position: 'relative',
                                width: '100%',
				paddingLeft: conversation.hasUnreadResponse &&
                                           conversation.id !== currentConversationId ?
                                           '24px' : '0' // Only add padding when there's a checkmark
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
                                <div className="chat-history-title" style={{
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'normal',
                                    maxWidth: '100%',
                                    paddingRight: '65px' // space for action buttons
                                }}>
                                {(() => {
                                    console.debug('[ChatHistory] Rendering conversation:', {
                                        id: conversation.id,
                                        title: conversation.title,
                                        isStreaming: streamingConversations.has(conversation.id),
                                        currentStreaming: Array.from(streamingConversations),
                                        isCurrent: conversation.id === currentConversationId
                                    });
                                    return <>
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
                                    </>;
                                })()}
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
                                icon={<EditOutlined/>}
                                onClick={(e) => handleEditClick(e, conversation.id)}
				style={{ display: 'flex', alignItems: 'center', height: '24px', padding: '0 4px' }}
                            />
                            <Button
                                type="text"
                                icon={<DeleteOutlined/>}
                                onClick={(e) => handleDeleteConversation(e, conversation.id)}
				style={{ display: 'flex', alignItems: 'center', height: '24px', padding: '0 4px' }}
                            />
                        </div>
			</div>
                    </div>
                </List.Item>
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
};

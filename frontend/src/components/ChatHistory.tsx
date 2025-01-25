import React, {useState, useCallback, useEffect} from 'react';
import {List, Button, Input, message, Modal} from 'antd';
import {DeleteOutlined, EditOutlined, DownloadOutlined, UploadOutlined, LoadingOutlined, SettingOutlined} from '@ant-design/icons';
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
	isStreaming,
        streamingConversationId,
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
               const hasChanged = saved.length !== conversations.length ||
                   JSON.stringify(saved) !== JSON.stringify(conversations);

               if (hasChanged) {
                   console.debug('Chat history changed:', {
                       savedCount: saved.length,
                       currentCount: conversations.length,
                       reason: saved.length !== conversations.length ? 'length' : 'content'
                   });
               }
               if (isSubscribed) {
		   if (hasChanged && saved.length > 0) setConversations(saved);
               }
           } catch (error) {
               console.error('Error checking for conversation updates:', error);
           }
       };

       const interval = setInterval(checkForUpdates, 2000); // Check every 2 seconds
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

            // If we're deleting the current conversation, start a new one
            if (conversationId === currentConversationId) {
                startNewChat();
            }

            message.success('Conversation deleted successfully');
        } catch (error) {
            // Revert any partial changes
            const saved = await db.getConversations();
            setConversations(saved);
            message.error('Failed to delete conversation');
            console.error('Error deleting conversation:', error);
        }
    };

    const handleRepairDatabase = async () => {
        Modal.confirm({
            title: 'Repair Database',
            content: 'This will attempt to repair the conversation database by removing corrupted entries. Continue?',
            okText: 'Yes',
            cancelText: 'No',
            onOk: async () => {
                setIsRepairing(true);
                try {
                    await db.repairDatabase();
                    // Reload conversations after repair
                    const repairedConversations = await db.getConversations();
                    setConversations(repairedConversations);
                    message.success('Database repair completed successfully');
                } catch (error) {
                    message.error('Failed to repair database');
                    console.error('Database repair error:', error);
                } finally {
                    setIsRepairing(false);
                }
            }
        });
    };

    const handleClearDatabase = () => {
        Modal.confirm({
            title: 'Clear Database',
            content: 'This will permanently delete all conversations. This action cannot be undone. Continue?',
            okText: 'Yes',
            okType: 'danger',
            cancelText: 'No',
            onOk: async () => {
                await db.clearDatabase();
                setConversations([]);
                message.success('Database cleared successfully');
            }
        });
    };

    // Sort conversations by lastAccessedAt
    const sortedConversations = [...conversations].sort((a, b) => {
        const aTime = a.lastAccessedAt ?? 0;
        const bTime = b.lastAccessedAt ?? 0;
        return bTime - aTime;
    });

    console.debug('Rendering chat history:', {
        totalConversations: conversations.length,
        sortedConversations: sortedConversations.length,
        currentId: currentConversationId,
        currentMessages: currentMessages.length
    });

    return (
        <List
            className="chat-history-list"
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
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        width: '100%',
                        boxSizing: 'border-box',
                        pointerEvents: isLoadingConversation ? 'none' : 'auto'
                    }}
                >
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
                            display: 'flex',
                            alignItems: 'center',
                            flex: 1
                        }}>
                            <div style={{
                                flex: 1,
                                marginRight: '8px',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap'
                            }}>
                                {conversation.title}
                                {isStreaming && conversation.id === streamingConversationId && (
                                    <span style={{ 
                                        marginLeft: '8px', 
                                        fontSize: '12px', 
                                        color: isDarkMode ? '#177ddc' : '#1890ff' 
                                    }}>(receiving response...)</span>
                                )}
                            </div>
                        </div>
                    )}
                    {isStreaming && conversation.id === streamingConversationId && (
                        <LoadingOutlined 
                            style={{ 
                                marginLeft: '8px',
                                color: isDarkMode ? '#177ddc' : '#1890ff'
                            }} 
                        />
                    )}
                    <div style={{display: 'flex', alignItems: 'center', flexShrink: 0}}>
                        <Button
                            type="text"
                            icon={<EditOutlined/>}
                            onClick={(e) => handleEditClick(e, conversation.id)}
                            style={{marginRight: '4px'}}
                        />
                        <Button
                            type="text"
                            icon={<DeleteOutlined/>}
                            onClick={(e) => handleDeleteConversation(e, conversation.id)}
                        />
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
			    input.click();}}>
                        Import
		    </Button>
                </div>
                </>
            }
        />
    );
};

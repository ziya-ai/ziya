import React, {useState, useCallback} from 'react';
import {List, Button, Input, message} from 'antd';
import {DeleteOutlined, EditOutlined, DownloadOutlined, UploadOutlined} from '@ant-design/icons';
import {useChatContext} from '../context/ChatContext';
import {useTheme} from '../context/ThemeContext';
import { db } from '../utils/db';

export const ChatHistory: React.FC = () => {
    const {
        conversations,
        setCurrentConversationId,
        setMessages,
        currentConversationId,
        setConversations,
	isLoadingConversation,
	loadConversation,
    } = useChatContext();
    const {isDarkMode} = useTheme();
    const [editingId, setEditingId] = useState<string | null>(null);

    const handleConversationClick = useCallback(async (conversationId: string) => {
        const selectedConversation = conversations.find(conv => conv.id === conversationId);
        if (selectedConversation && conversationId !== currentConversationId) {
            setCurrentConversationId(conversationId);

            // Use requestAnimationFrame to ensure the UI updates before heavy processing
            requestAnimationFrame(() => {
                // Break up the message setting into chunks if there are many messages
                const messages = selectedConversation.messages;
                setMessages(messages);
            });
        }
    }, [conversations, currentConversationId, setCurrentConversationId, setMessages]);

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

    const handleTitleChange = (conversationId: string, newTitle: string) => {
        setConversations(prevConversations =>
            prevConversations.map(conv =>
                conv.id === conversationId ? {...conv, title: newTitle} : conv
            )
        );
        setEditingId(null);
    };

    const handleTitleBlur = (conversationId: string, newTitle: string) => handleTitleChange(conversationId, newTitle);

    const handleDeleteConversation = (e: React.MouseEvent, conversationId: string) => {
        e.stopPropagation(); // Prevent the click from bubbling up to the List.Item
        setConversations(prevConversations =>
            prevConversations.filter(conv => conv.id !== conversationId)
        );
        if (currentConversationId === conversationId) {
            setCurrentConversationId('');
            setMessages([]);
        }
    };

    // Sort conversations by lastAccessedAt
    const sortedConversations = [...conversations].sort((a, b) => {
        const aTime = a.lastAccessedAt || 0;
        const bTime = b.lastAccessedAt || 0;
        return bTime - aTime;
    });

    return (
        <List
            className="chat-history-list"
            dataSource={sortedConversations}
            renderItem={(conversation) => (
                <List.Item
                    key={conversation.id}
                    onClick={isLoadingConversation ? undefined : () => loadConversation(conversation.id)} 
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
                            flex: 1,
                            marginRight: '8px',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap'
                        }}>
                            {conversation.title}
                        </div>
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
                    }}>
                    Import
                </Button>
            </div>}
        />
    );
};

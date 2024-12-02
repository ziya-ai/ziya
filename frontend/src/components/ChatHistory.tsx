import React, {useState} from 'react';
import {List, Button, Input} from 'antd';
import {DeleteOutlined, EditOutlined} from '@ant-design/icons';
import {useChatContext} from '../context/ChatContext';
import {useTheme} from '../context/ThemeContext';

export const ChatHistory: React.FC = () => {
    const {
        conversations,
        setCurrentConversationId,
        setMessages,
        currentConversationId,
        setConversations,
    } = useChatContext();
    const [editingId, setEditingId] = useState<string | null>(null);
    const { isDarkMode } = useTheme();

    const handleConversationClick = (conversationId: string) => {
        const selectedConversation = conversations.find(conv => conv.id === conversationId);
        if (selectedConversation) {
            setCurrentConversationId(conversationId);
            setMessages(selectedConversation.messages);
        }
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

    return (
        <List
            dataSource={conversations.slice().reverse()}
            renderItem={(conversation) => (
                <List.Item
                    key={conversation.id}
                    onClick={() => handleConversationClick(conversation.id)}
                    style={{
                        cursor: 'pointer',
                        backgroundColor: conversation.id === currentConversationId ? (isDarkMode ? '#177ddc' : '#e6f7ff') : 'transparent',
                        padding: '8px',
                        borderRadius: '4px',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        flexWrap: 'nowrap',
                    }}
                >
                    {editingId === conversation.id ? (
                        <Input
                            defaultValue={conversation.title}
                            onPressEnter={(e) => handleTitleChange(conversation.id, e.currentTarget.value)}
                            onBlur={(e) => handleTitleBlur(conversation.id, e.currentTarget.value)}
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
        />
    );
};
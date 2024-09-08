import React from 'react';
import {List, Button} from 'antd';
import {DeleteOutlined} from '@ant-design/icons';
import {useChatContext} from '../context/ChatContext';

export const ChatHistory: React.FC = () => {
    const {
        conversations,
        setCurrentConversationId,
        setMessages,
        currentConversationId,
        setConversations,
    } = useChatContext();

    const handleConversationClick = (conversationId: string) => {
        const selectedConversation = conversations.find(conv => conv.id === conversationId);
        if (selectedConversation) {
            setCurrentConversationId(conversationId);
            setMessages(selectedConversation.messages);
        }
    };

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
                        backgroundColor: conversation.id === currentConversationId ? '#e6f7ff' : 'transparent',
                        padding: '8px',
                        borderRadius: '4px',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                    }}
                >
                    <div>{conversation.title}</div>
                    <Button
                        type="text"
                        icon={<DeleteOutlined/>}
                        onClick={(e) => handleDeleteConversation(e, conversation.id)}
                    />
                </List.Item>
            )}
        />
    );
};
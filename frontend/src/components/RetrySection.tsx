import React from "react";
import {useActiveChat} from '../context/ActiveChatContext';
import {Button, Tooltip, Space} from "antd";
import {RedoOutlined, LoadingOutlined} from "@ant-design/icons";
import {useSendPayload} from '../hooks/useSendPayload';

interface RetrySectionProps {
    index: number;
}

export const RetrySection: React.FC<RetrySectionProps> = ({index}) => {
    const {
        currentMessages,
        currentConversationId,
        streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
    } = useActiveChat();
    const { send } = useSendPayload();

    const handleRetry = async () => {
        const lastHumanMessage = currentMessages[index];
        addStreamingConversation(currentConversationId);

        try {
            await send({
                messages: currentMessages,
                question: lastHumanMessage.content,
            });
        } catch (error) {
            console.error('Error retrying message:', error);
        } finally {
            removeStreamingConversation(currentConversationId);
        }
    };

    return (
	<Tooltip title={streamingConversations.has(currentConversationId) ? "Waiting for response..." : "Retry"}>
            <Button
	        icon={streamingConversations.has(currentConversationId) ? <LoadingOutlined /> : <RedoOutlined />}
                onClick={handleRetry}
                size="small"
		disabled={streamingConversations.has(currentConversationId)}
                type={streamingConversations.has(currentConversationId) ? 'default' : 'primary'}
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    minWidth: '120px'
                }}
            />
        </Tooltip>
    );
};

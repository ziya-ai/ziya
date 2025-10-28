import React from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {Message} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Button, Tooltip, Space} from "antd";
import { convertKeysToStrings } from '../utils/types';
import {RedoOutlined, LoadingOutlined} from "@ant-design/icons";

interface RetrySectionProps {
    index: number;
}

export const RetrySection: React.FC<RetrySectionProps> = ({index}) => {
    const {
        currentMessages,
        currentConversationId,
        addMessageToConversation,
        setIsStreaming,
	streamedContentMap,
	setStreamedContentMap,
	removeStreamingConversation,
	streamingConversations
    } = useChatContext();
    
    const {checkedKeys} = useFolderContext();

    const handleRetry = async () => {
        const lastHumanMessage = currentMessages[index];
        setIsStreaming(true);
	setStreamedContentMap(new Map());

        try {
            const result = await sendPayload(
                currentMessages,
                lastHumanMessage.content,
                convertKeysToStrings(checkedKeys),
                currentConversationId,
                streamedContentMap,
                setStreamedContentMap,
                setIsStreaming,
                removeStreamingConversation,
                addMessageToConversation,
                streamingConversations.has(currentConversationId)
            );

            if (result) {
                const newAIMessage: Message = {
                    content: result,
                    role: 'assistant'
                };
                addMessageToConversation(newAIMessage, currentConversationId);
            }
        } catch (error) {
            console.error('Error retrying message:', error);
        } finally {
            setIsStreaming(false);
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

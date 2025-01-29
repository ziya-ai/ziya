import React from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {Message} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Button, Tooltip} from "antd";
import { convertKeysToStrings } from '../utils/types';
import {RedoOutlined} from "@ant-design/icons";

interface RetrySectionProps {
    index: number;
}

export const RetrySection: React.FC<RetrySectionProps> = ({index}) => {
    const {
        currentMessages,
        currentConversationId,
        addMessageToConversation,
        setIsStreaming,
	setStreamedContentMap,
	removeStreamingConversation
    } = useChatContext();
    
    const {checkedKeys} = useFolderContext();

    const handleRetry = async () => {
        const lastHumanMessage = currentMessages[index];
        setIsStreaming(true);
	setStreamedContentMap(new Map());

        try {
            const result = await sendPayload(
                currentConversationId,
                lastHumanMessage.content,
		currentMessages,
                setStreamedContentMap,
                setIsStreaming,
                convertKeysToStrings(checkedKeys),
		addMessageToConversation,
		removeStreamingConversation
            );

            if (result) {
                const newAIMessage: Message = {
                    content: result,
                    role: 'assistant'
                };
                addMessageToConversation(newAIMessage);
            }
        } catch (error) {
            console.error('Error retrying message:', error);
        } finally {
            setIsStreaming(false);
        }
    };

    return (
        <Tooltip title="Retry">
            <Button
                icon={<RedoOutlined />}
                onClick={handleRetry}
                size="small"
            />
        </Tooltip>
    );
};

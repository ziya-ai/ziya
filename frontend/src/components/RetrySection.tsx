import React from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {Button, Tooltip} from "antd";
import { convertKeysToStrings } from '../utils/types';
import {RedoOutlined} from "@ant-design/icons";

interface RetrySectionProps {
    index: number;
}

export const RetrySection: React.FC<RetrySectionProps> = ({index}) => {
    const {messages, setMessages, setStreamedContent, setIsStreaming} = useChatContext();
    const {checkedKeys} = useFolderContext();

    const handleRetry = async () => {
        const updatedMessages = messages.slice(0, index);
        const lastHumanMessage = updatedMessages[updatedMessages.length - 1];
	const originalMessage = messages[index];  // Get the original message we're retrying

        setMessages(updatedMessages);
        setIsStreaming(true);
        setStreamedContent('');
	await sendPayload(updatedMessages, lastHumanMessage.content, setStreamedContent, setIsStreaming, convertKeysToStrings(checkedKeys));
        setIsStreaming(false);
        setStreamedContent((content) => {
	    setMessages((prevMessages) => [
                ...prevMessages,
                {
                    content,
                    role: 'assistant',
                    // Preserve the original message's sequence and timestamp
                    sequence: originalMessage.sequence,
                    timestamp: originalMessage.timestamp
                }
            ]);
            return "";
        });
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

import React, {useState} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {Message} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Button, Tooltip, Input, Space} from "antd";
import { convertKeysToStrings } from '../utils/types';
import {EditOutlined, CheckOutlined, CloseOutlined, SaveOutlined} from "@ant-design/icons";

interface EditSectionProps {
    index: number;
}

export const EditSection: React.FC<EditSectionProps> = ({index}) => {
    const {
        currentMessages,
        currentConversationId,
        addMessageToConversation,
        setIsStreaming,
	setConversations,
        streamingConversations,
	addStreamingConversation,
	setStreamedContentMap,
	removeStreamingConversation
    } = useChatContext();
    
    const [isEditing, setIsEditing] = useState(false);
    const [editedMessage, setEditedMessage] = useState(currentMessages[index].content);
    const {checkedKeys} = useFolderContext();
    const {TextArea} = Input;

    const handleEdit = () => {
        setIsEditing(true);
    };
    
    const handleSave = () => {
        // Update the conversation in the context with the edited message
        setConversations(prev => prev.map(conv => {
            if (conv.id === currentConversationId) {
                const updatedMessages = conv.messages.map((msg, i) => {
                    if (i === index) {
                        return {
                            ...msg,
                            content: editedMessage,
                            _timestamp: Date.now()  // Update timestamp to mark as modified
                        };
                    }
                    return msg;
                });
                return { ...conv, messages: updatedMessages, _version: Date.now() };
            }
            return conv;
        }));
        setIsEditing(false);
    };

    const handleCancel = () => {
        setIsEditing(false);
        setEditedMessage(currentMessages[index].content);
    };

    const handleSubmit = async () => {
        setIsEditing(false);

	// Clear any existing streamed content
        setStreamedContentMap(new Map());

	// Create truncated message array up to and including edited message
        const truncatedMessages = currentMessages.slice(0, index + 1);

        // Update the edited message
        truncatedMessages[index] = {
            ...truncatedMessages[index],
            content: editedMessage,
            _timestamp: Date.now()
        };
        // Set conversation to just the truncated messages
        setConversations(prev => prev.map(conv =>
            conv.id === currentConversationId
                ? { ...conv, messages: truncatedMessages }
                : conv
        ));

	addStreamingConversation(currentConversationId);
        try {
            const result = await sendPayload(
                currentConversationId,
                editedMessage,
		streamingConversations.has(currentConversationId),
		currentMessages,
                setStreamedContentMap,
                setIsStreaming,
                convertKeysToStrings(checkedKeys),
		addMessageToConversation,
		removeStreamingConversation
            );

            // Get the final streamed content
            const finalContent = result;

            if (finalContent) {
                const newAIMessage: Message = {
                    content: finalContent,
                    role: 'assistant'
                };
                addMessageToConversation(newAIMessage, currentConversationId);
            }
        } catch (error) {
            console.error('Error sending message:', error);
            removeStreamingConversation(currentConversationId);
      	} finally {
            setIsStreaming(false);
        }
    };

    return (
        <div>
            {isEditing ? (
                <>
                    <TextArea
                        style={{width: '38vw', height: '100px'}}
                        value={editedMessage}
                        onChange={(e) => setEditedMessage(e.target.value)}
                    />
                    <Space style={{ marginTop: '8px' }}>
                        <Button icon={<CloseOutlined />} onClick={handleCancel} size="small">
                            Cancel
                        </Button>
                        <Button icon={<SaveOutlined />} onClick={handleSave} size="small">
                            Save
                        </Button>
                        <Button icon={<CheckOutlined />} onClick={handleSubmit} size="small" type="primary">
                            Submit
                        </Button>
                    </Space>
                </>
            ) : (
                <Tooltip title="Edit">
                    <Button icon={<EditOutlined/>} onClick={handleEdit}/>
                </Tooltip>
            )}
        </div>
    );
}


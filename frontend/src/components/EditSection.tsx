import React, { useState, useRef, useEffect } from "react";
import { useChatContext } from '../context/ChatContext';
import { sendPayload } from "../apis/chatApi";
import { Message } from "../utils/types";
import { useFolderContext } from "../context/FolderContext";
import { Button, Tooltip, Input, Space } from "antd";
import { convertKeysToStrings } from '../utils/types';
import { EditOutlined, CheckOutlined, CloseOutlined, SaveOutlined } from "@ant-design/icons";

interface EditSectionProps {
    index: number;
    isInline?: boolean;
}

export const EditSection: React.FC<EditSectionProps> = ({ index, isInline = false }) => {
    const {
        currentMessages,
        currentConversationId,
        addMessageToConversation,
        setIsStreaming,
        setConversations,
        streamingConversations,
        addStreamingConversation,
        streamedContentMap,
        setStreamedContentMap,
        removeStreamingConversation,
        editingMessageIndex,
        setEditingMessageIndex
    } = useChatContext();

    const [editedMessage, setEditedMessage] = useState(currentMessages[index].content);
    const { checkedKeys } = useFolderContext();
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const { TextArea } = Input;
    const isEditing = editingMessageIndex === index;

    // Focus the textarea when editing starts
    useEffect(() => {
        if (isEditing && textareaRef.current) {
            setTimeout(() => {
                if (textareaRef.current) {
                    textareaRef.current.focus();
                }
            }, 100);
        }
    }, [isEditing]);

    const handleEdit = () => {
        setEditingMessageIndex(index);
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
        setEditingMessageIndex(null);
    };

    const handleCancel = () => {
        setEditingMessageIndex(null);
        setEditedMessage(currentMessages[index].content);
    };

    const handleSubmit = async () => {
        setEditingMessageIndex(null);

        // Clear any existing streamed content
        setStreamedContentMap(new Map());

        // Create truncated message array up to and including edited message
        const truncatedMessages = currentMessages.slice(0, index + 1);

        // Update the edited message
        truncatedMessages[index] = {
            ...truncatedMessages[index],
            content: editedMessage,
            _timestamp: Date.now(),
            // Add a marker to indicate this message was edited and truncated
            _edited: true,
            _truncatedAfter: true
        };

        // Set conversation to just the truncated messages
        setConversations(prev => prev.map(conv =>
            conv.id === currentConversationId
                ? { ...conv, messages: truncatedMessages, _version: Date.now(), _editInProgress: true }
                : conv
        ));

        addStreamingConversation(currentConversationId);
        try {
            const result = await sendPayload(
                truncatedMessages,
                editedMessage,
                convertKeysToStrings(checkedKeys),
                currentConversationId,
                streamedContentMap,
                setStreamedContentMap,
                setIsStreaming,
                removeStreamingConversation,
                addMessageToConversation,
                streamingConversations.has(currentConversationId)
            );

            // sendPayload already adds the message to conversation, so we just need to clear the flag
            if (result) {
                // Clear the edit in progress flag after the response is complete
                // Note: The message is already added by sendPayload, so we don't add it again
                setConversations(prev => prev.map(conv =>
                    conv.id === currentConversationId
                        ? { ...conv, _editInProgress: false }
                        : conv
                ));
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
            {/* If this is the inline version and we're editing this message, don't render anything */}
            {isInline && isEditing && (
                null
            )}

            {/* If we're editing and this is NOT the inline version, show full edit interface */}
            {isEditing && !isInline && (
                <div style={{ width: '100%' }}>
                    {/* Header row with sender and buttons */}
                    <div style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '8px',
                        width: '100%'
                    }}>
                        <div className="message-sender">You:</div>
                        <Space>
                            <Tooltip title="Cancel editing">
                                <Button icon={<CloseOutlined />} onClick={handleCancel} size="small">
                                    Cancel
                                </Button>
                            </Tooltip>
                            <Tooltip title="Save changes to context">
                                <Button icon={<SaveOutlined />} onClick={handleSave} size="small">
                                    Save
                                </Button>
                            </Tooltip>
                            <Tooltip title="Send to model, remove newer responses">
                                <Button icon={<CheckOutlined />} onClick={handleSubmit} size="small" type="primary">
                                    Submit
                                </Button>
                            </Tooltip>
                        </Space>
                    </div>

                    {/* Full-width textarea */}
                    <TextArea
                        ref={textareaRef}
                        autoFocus
                        style={{
                            width: '100%',
                            minHeight: '100px',
                            resize: 'vertical'
                        }}
                        value={editedMessage}
                        onChange={(e) => setEditedMessage(e.target.value)}
                        autoSize={{
                            minRows: 3,
                            maxRows: 20
                        }}
                        placeholder="Edit your message..."
                    />
                </div>
            )}

            {/* Show edit button if not editing and this is inline, OR if not editing and not inline */}
            {!isEditing && (isInline || !isEditing) && (
                <Tooltip title="Edit">
                    <Button icon={<EditOutlined />} onClick={handleEdit} />
                </Tooltip>
            )}
        </div>
    );
};


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
        addMessageToCurrentConversation,
        setStreamedContent,
        setIsStreaming
    } = useChatContext();
    
    const [isEditing, setIsEditing] = useState(false);
    const [editedMessage, setEditedMessage] = useState(currentMessages[index].content);
    const {checkedKeys} = useFolderContext();
    const {TextArea} = Input;

    const handleEdit = () => {
        setIsEditing(true);
    };
    
    const handleSave = () => {
        // Only update the message content without regenerating response
        const updatedMessage: Message = {
            content: editedMessage,
            role: 'human'
        };
        addMessageToCurrentConversation(updatedMessage);
        setIsEditing(false);
    };

    const handleCancel = () => {
        setIsEditing(false);
        setEditedMessage(currentMessages[index].content);
    };

    const handleSubmit = async () => {
        setIsEditing(false);
        setIsStreaming(true);

        try {
            const result = await sendPayload(
                currentConversationId,
                editedMessage,
		currentMessages,
                setStreamedContent,
                setIsStreaming,
                convertKeysToStrings(checkedKeys),
		addMessageToCurrentConversation
            );

            // Get the final streamed content
            const finalContent = result;

            if (finalContent) {
                const newAIMessage: Message = {
                    content: finalContent,
                    role: 'assistant'
                };
                addMessageToCurrentConversation(newAIMessage);
            }
        } catch (error) {
            console.error('Error sending message:', error);
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
};

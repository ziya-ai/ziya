import React, {useState} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {Message} from "../utils/types";
import {Button, Tooltip, Input, Space} from "antd";
import {EditOutlined, CheckOutlined, CloseOutlined, SaveOutlined} from "@ant-design/icons";

interface EditSectionProps {
    index: number;
}

export const EditSection: React.FC<EditSectionProps> = ({index}) => {
    const {messages, setMessages, setStreamedContent, setIsStreaming} = useChatContext();
    const [isEditing, setIsEditing] = useState(false);
    const [editedMessage, setEditedMessage] = useState(messages[index].content);
    const {checkedKeys} = useFolderContext()
    const {TextArea} = Input;
    const handleEdit = () => {
        setIsEditing(true);
    };
    
    const handleSave = () => {
        // Only update the message content without regenerating response
        setMessages(prevMessages => {
            const updatedMessages = [...prevMessages];
            updatedMessages[index] = {
                content: editedMessage,
                role: 'human'
            };
            return updatedMessages;
        });
        setIsEditing(false);
    };


    const handleCancel = () => {
        setIsEditing(false);
        setEditedMessage(messages[index].content);
    };

    const handleSubmit = async () => {
        setIsEditing(false);
        const updatedMessages: Message[] = [...messages.slice(0, index), {content: editedMessage, role: 'human'}];
        setMessages(updatedMessages);
        setIsStreaming(true);
        await sendPayload(updatedMessages, editedMessage, setStreamedContent, setIsStreaming, checkedKeys);
        setIsStreaming(false);
        setStreamedContent((content) => {
            setMessages((prevMessages) => [...prevMessages, {content, role: 'assistant'}]);
            return "";
        });
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
                            Submit</Button>
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

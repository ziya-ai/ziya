import React, {useState} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {Message} from "../utils/types";
import {Button, Tooltip, Input} from "antd";
import {EditOutlined, CheckOutlined, CloseOutlined} from "@ant-design/icons";

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
                    <Button icon={<CloseOutlined />} onClick={handleCancel} size={"small"} style={{marginInline: '3px'}}>Cancel</Button>
                    <Button icon={<CheckOutlined />} onClick={handleSubmit} size={"small"} type={"primary"}>Submit</Button>
                </>
            ) : (
                <Tooltip title="Edit">
                    <Button icon={<EditOutlined/>} onClick={handleEdit}/>
                </Tooltip>
            )}
        </div>
    );
};

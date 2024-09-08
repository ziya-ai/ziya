import React, {useState} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {Message} from "../utils/types";

interface EditSectionProps {
    index: number;
}

export const EditSection: React.FC<EditSectionProps> = ({index}) => {
    const {messages, setMessages, setStreamedContent, setIsStreaming} = useChatContext();
    const [isEditing, setIsEditing] = useState(false);
    const [editedMessage, setEditedMessage] = useState(messages[index].content);
    const {checkedKeys} = useFolderContext()
    const handleEdit = () => {
        setIsEditing(true);
    };

    const handleCancel = () => {
        setIsEditing(false);
        setEditedMessage(messages[index].content);
    };

    const handleSubmit = async () => {
        setIsEditing(false);
        const updatedMessages : Message[] = [...messages.slice(0, index), {content: editedMessage, role: 'human'}];
        setMessages(updatedMessages);
        setIsStreaming(true);
        await sendPayload(updatedMessages, editedMessage, setStreamedContent, checkedKeys);
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
          <textarea
              style={{width: '40vw', height: '100px'}}
              value={editedMessage}
              onChange={(e) => setEditedMessage(e.target.value)}
          />
                    <button onClick={handleSubmit}>Submit</button>
                    <button onClick={handleCancel}>Cancel</button>
                </>
            ) : (
                <button className="edit-button" onClick={handleEdit}>Edit</button>
            )}
        </div>
    );
};
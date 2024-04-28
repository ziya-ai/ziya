import React, {useState} from "react";

export const EditSection = ({message, index, setMessages, checkedItems, handleSendPayload}) => {
    const [isEditing, setIsEditing] = useState(false);
    const [editedMessage, setEditedMessage] = useState(message.content);

    const handleEdit = () => {
        setIsEditing(true);
    };

    const handleCancel = () => {
        setIsEditing(false);
        setEditedMessage(message.content);
    };

    const handleSubmit = () => {
        setIsEditing(false);
        setMessages((prevMessages) => {
            const updatedMessages = [...prevMessages];
            updatedMessages.splice(index);
            handleSendPayload(updatedMessages, editedMessage, checkedItems);
            return updatedMessages;
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

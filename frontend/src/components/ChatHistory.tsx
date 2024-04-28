import {EditSection} from "./EditSection";
import React from "react";
import {MarkdownRenderer} from "./MarkdownRenderer";

export const ChatHistory = ({messages, setMessages, checkedItems, handleSendPayload}) => (
    <div>
        {messages.slice().reverse().map((msg, index) => (
            <div key={index} className={`message ${msg.role}`}>
                {msg.role === 'human' ? (
                    <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <div className="message-sender">You:</div>
                        <EditSection
                            message={msg}
                            index={messages.length - 1 - index}
                            setMessages={setMessages}
                            checkedItems={checkedItems}
                            handleSendPayload={handleSendPayload}
                        />
                    </div>
                ) : (
                    <div className="message-sender">AI:</div>
                )}
                <MarkdownRenderer markdown={msg.content}/>
            </div>
        ))}
    </div>
);

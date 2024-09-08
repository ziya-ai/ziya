import React from "react";
import {EditSection} from "./EditSection";
import {MarkdownRenderer} from "./MarkdownRenderer";
import {useChatContext} from '../context/ChatContext';

export const Conversation: React.FC = () => {
    const {messages} = useChatContext();

    return (
        <>
            {messages.length > 0 && (
                <div>
                    {messages.slice().reverse().map((msg, index) => (
                        <div key={index} className={`message ${msg.role}`}>
                            {msg.role === 'human' ? (
                                <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                    <div className="message-sender">You:</div>
                                    <EditSection index={messages.length - 1 - index}/>
                                </div>
                            ) : (
                                <div className="message-sender">AI:</div>
                            )}
                            <MarkdownRenderer markdown={msg.content}/>
                        </div>
                    ))}
                </div>
            )}
        </>
    );
};
import React from "react";
import {EditSection} from "./EditSection";
import {RetrySection} from "./RetrySection";
import {MarkdownRenderer} from "./MarkdownRenderer";
import {useChatContext} from '../context/ChatContext';

interface ConversationProps {
    enableCodeApply: boolean;
}

export const Conversation: React.FC<ConversationProps> = ({ enableCodeApply }) => {
    const {messages, isTopToBottom} = useChatContext();
    // In top-to-bottom mode, show messages in chronological order
    // In bottom-to-top mode, show messages in reverse chronological order
    const displayMessages = isTopToBottom ? messages : messages.slice().reverse();

    return (
        <>
            {messages.length > 0 && (
                <div>
		    {displayMessages.map((msg, index) => (
                        <div key={index} className={`message ${msg.role}`}>
                            {msg.role === 'human' ? (
                                <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                    <div className="message-sender">You:</div>
                                    <EditSection index={messages.length - 1 - index}/>
                                </div>
                            ) : (
                                <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                    <div className="message-sender">AI:</div>
                                    <div style={{alignSelf: 'flex-end'}}>
                                        <RetrySection index={messages.length - 1 - index}/>
                                    </div>
                                </div>
                            )}
                            <MarkdownRenderer markdown={msg.content} enableCodeApply={enableCodeApply}/>
                        </div>
                    ))}
                </div>
            )}
        </>
    );
};

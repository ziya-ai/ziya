import React, { Suspense, useMemo } from "react";
import { Spin } from 'antd';
import {EditSection} from "./EditSection";
import {RetrySection} from "./RetrySection";
import {useChatContext} from '../context/ChatContext';

// Lazy load the MarkdownRenderer
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

interface ConversationProps {
    enableCodeApply: boolean;
}

const Conversation: React.FC<ConversationProps> = ({ enableCodeApply }) => {
    const {messages, isTopToBottom, isLoadingConversation} = useChatContext();
    // In top-to-bottom mode, show messages in chronological order
    // In bottom-to-top mode, show messages in reverse chronological order
    const displayMessages = isTopToBottom ? messages : messages.slice().reverse();

    // Memoize the message content to prevent unnecessary re-renders
    const messageContent = useMemo(() => (
        displayMessages.map((msg, index) => (
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
                <div className="message-content">
                    <Suspense fallback={<div>Loading content...</div>}>
                        <MarkdownRenderer 
                            markdown={msg.content} 
                            enableCodeApply={enableCodeApply}
                        />
                    </Suspense>
                </div>
	    </div>
        ))
    ), [displayMessages, messages.length, enableCodeApply]);

    return (
	<div style={{ position: 'relative' }}>
            {isLoadingConversation && (
		<div style={{
                    position: 'fixed',
                    top: 'var(--header-height)',
                    left: 'var(--folder-panel-width)',
                    right: 0,
                    bottom: 0,
                    backgroundColor: 'rgba(0, 0, 0, 0.5)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
		    zIndex: 1000
                }}>
		    <Spin size="large" tip="Loading..." />
                </div>
            )}
            <div style={{ opacity: isLoadingConversation ? 0.5 : 1 }}>
                {messageContent}
            </div>
        </div>
    );
};

export default Conversation;

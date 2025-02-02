import React, {useEffect, useRef, Suspense, memo} from "react";
import {useChatContext} from '../context/ChatContext';
import {EditSection} from "./EditSection";
import {RetrySection} from "./RetrySection";
import {Space, Spin} from 'antd';
import {LoadingOutlined, RobotOutlined} from "@ant-design/icons";

// Lazy load the MarkdownRenderer
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

interface ConversationProps {
    enableCodeApply: boolean;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply }) => {
    const {currentMessages, 
	   isTopToBottom, 
	   isLoadingConversation,
           currentConversationId,
           streamingConversations
    } = useChatContext();
    
    const visibilityRef = useRef<boolean>(true);
    // Sort messages to maintain order
    const displayMessages = isTopToBottom ? currentMessages : [...currentMessages].reverse();

    // Keep track of rendered messages for performance monitoring
    const renderedCountRef = useRef(0);

    useEffect(() => {
        console.debug('Conversation messages updated:', {
            messageCount: currentMessages.length,
            previousCount: renderedCountRef.current,
            isVisible: visibilityRef.current,
            displayOrder: isTopToBottom ? 'top-down' : 'bottom-up'
        });

        if (currentMessages.length !== renderedCountRef.current) {
            renderedCountRef.current = currentMessages.length;
            console.log(`Rendered ${currentMessages.length} messages`);
        }

        // Set up visibility observer
        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach(entry => {
                    visibilityRef.current = entry.isIntersecting;
                    console.debug('Conversation visibility changed:', {
                        isVisible: entry.isIntersecting,
                        messageCount: currentMessages.length
                    });
                });
            },
            { threshold: 0.1 }
        );

        return () => observer.disconnect();
    }, [currentMessages.length]);

    // Loading indicator text based on progress
    const loadingText = isLoadingConversation 
        ? currentMessages.length > 0
            ? `Loading messages (${currentMessages.length} loaded)...`
            : 'Loading conversation...'
        : '';

    // Progressive loading indicator
    const showProgressiveLoading = isLoadingConversation && currentMessages.length > 0;

    // Track whether we're in the initial loading state
    const isInitialLoading = isLoadingConversation && currentMessages.length === 0;

    return (
        <div style={{ position: 'relative' }}>
            {isInitialLoading && (
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
                    <Spin size="large" tip={loadingText} />
                </div>
            )}
            <div 
                style={{ 
                    opacity: isLoadingConversation ? 0.5 : 1,
                    minHeight: '50px' // Ensure visibility detection
                }}
                className="conversation-messages-container"
            >
                {showProgressiveLoading && (
                    <div style={{
                        position: 'sticky',
                        top: 0,
                        backgroundColor: 'rgba(0, 0, 0, 0.7)',
                        color: '#fff',
                        padding: '8px 16px',
                        borderRadius: '4px',
                        margin: '8px 0',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        zIndex: 1000
                    }}>
                        <Spin size="small" />
                        <span>{loadingText}</span>
                    </div>
                )}
                {displayMessages.map((msg, index) => (
                    <div key={index} className={`message ${msg.role}`}>
                        {msg.role === 'human' ? (
                            <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                <div className="message-sender">You:</div>
                                <EditSection index={currentMessages.length - 1 - index}/>
                            </div>
                        ) : (
                            <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                <div className="message-sender">AI:</div>
                                <div style={{alignSelf: 'flex-end'}}>
                                    <RetrySection index={currentMessages.length - 1 - index}/>
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
                ))}

                </div>
            </div>
    );
}); 

export default Conversation;

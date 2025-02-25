import React, {useEffect, useRef, Suspense, memo} from "react";
import {useChatContext} from '../context/ChatContext';
import {EditSection} from "./EditSection";
import {Space, Spin, Button, Tooltip} from 'antd';
import {LoadingOutlined, RobotOutlined, RedoOutlined} from "@ant-design/icons";
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {convertKeysToStrings} from "../utils/types";

// Lazy load the MarkdownRenderer
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

interface ConversationProps {
    enableCodeApply: boolean;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply }) => {
    const {currentMessages, 
	   isTopToBottom, 
	   isLoadingConversation,
	   addStreamingConversation,
	   streamingConversations,
           currentConversationId,
	   setIsStreaming,
           setStreamedContentMap,
           addMessageToConversation,
           removeStreamingConversation
    } = useChatContext();
    
    const {checkedKeys} = useFolderContext();
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

    // Function to determine if we need to show the retry button
    const shouldShowRetry = (index: number) => {
	const message = currentMessages[index];
        const isLastMessage = index === currentMessages.length - 1;
        const nextIndex = index + 1;
        const nextMessage = nextIndex < currentMessages.length ? currentMessages[nextIndex] : null;
        const hasNextMessage = nextIndex < currentMessages.length;

        // Show retry if this is a human message and either:
        // 1. It's the last message, or
        // 2. The next message isn't from the assistant
        return message?.role === 'human' &&
               !streamingConversations.has(currentConversationId) &&
               (isLastMessage || 
               (hasNextMessage && nextMessage?.role !== 'assistant'));
    };

    // Render retry button with explanation
    const renderRetryButton = (index: number) => {
        if (!shouldShowRetry(index)) return null;

        return (
            <Tooltip title="The AI response may have failed. Click to retry.">
                <Button
                    icon={<RedoOutlined />}
                    type="primary"
                    size="small"
		    onClick={async () => {
                        const message = currentMessages[index];
			addStreamingConversation(currentConversationId);
			try {
                            await sendPayload(
                                currentConversationId,
                                message.content,
				streamingConversations.has(currentConversationId),
                                currentMessages,
                                setStreamedContentMap,
                                setIsStreaming,
                                convertKeysToStrings(checkedKeys),
                                addMessageToConversation,
                                removeStreamingConversation
                            );
                        } catch (error) {
			    removeStreamingConversation(currentConversationId);
                            console.error('Error retrying message:', error);
                        }
                    }}
                >
                    Retry AI Response
                </Button>
            </Tooltip>
        );
    };
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
		    {displayMessages.map((msg, index) => {
                    const isLastMessage = index === displayMessages.length - 1;
		    // Convert display index to actual index for bottom-up mode
                    const actualIndex = isTopToBottom ? index : currentMessages.length - 1 - index;
                    const nextActualIndex = actualIndex + 1;
                    const hasNextMessage = nextActualIndex < currentMessages.length;
                    const nextMessage = hasNextMessage ? currentMessages[nextActualIndex] : null;
                    const needsResponse = msg.role === 'human' &&
                                        !streamingConversations.has(currentConversationId) &&
					(actualIndex === currentMessages.length - 1 ||
                                         (hasNextMessage && nextMessage?.role !== 'assistant'));

		        return <div
                            key={index}
                            className={`message ${msg.role}${
                                needsResponse
                                    ? ' needs-response'
                                    : ''
                            }`}
                        >
                        {msg.role === 'human' ? (
                            <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                <div className="message-sender">You:</div>
				<div style={{ display: 'flex', gap: '8px' }}>
                                    {needsResponse && renderRetryButton(actualIndex)}
                                    <EditSection index={isTopToBottom ? index : currentMessages.length - 1 - index}/>
                                </div>
                            </div>
                        ) : (
                            <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                <div className="message-sender">AI:</div>
			        {renderRetryButton(actualIndex)}
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
                    </div>;
                })}
            </div>
        </div>
    );
}); 

export default Conversation;

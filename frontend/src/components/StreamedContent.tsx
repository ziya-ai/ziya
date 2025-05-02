import React, { useEffect, Suspense, useState, useRef } from 'react';
import {useChatContext} from '../context/ChatContext';
import { Space, Alert } from 'antd';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';

const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const contentRef = useRef<HTMLDivElement>(null);
    const isAutoScrollingRef = useRef<boolean>(false);
    const {
        streamedContentMap,
	    isStreaming,
        setIsStreaming,
        currentConversationId, 
        streamingConversations,
        currentMessages,
        removeStreamingConversation,
	    isTopToBottom
    } = useChatContext();

    const LoadingIndicator = () => (
	<Space>
	    <div style={{
                padding: '20px',
                textAlign: 'center',
                color: 'var(--loading-color, #1890ff)',
                width: '100%', 
		order: isTopToBottom ? 0 : -1  // Place at top if bottom-up view
            }} className="loading-indicator">
            <Space>
                <RobotOutlined style={{ fontSize: '24px', animation: 'pulse 2s infinite' }} />
                <LoadingOutlined spin />
                <span style={{
                    animation: 'fadeInOut 2s infinite',
                    display: 'inline-block',
                    fontSize: '16px',
                    marginLeft: '8px',
                    verticalAlign: 'middle'
                }}>Processing response...</span>
            </Space>
        </div>
        </Space>
    );

    const ErrorDisplay = ({ message }: { message: string }) => (
        <Alert
            message="Error"
            description={message}
            type="error"
            showIcon
            className="stream-error"
            style={{ margin: '20px 0' }}
        />
    );

    // Function to check if user is viewing the bottom of the content
    const isViewingBottom = () => {
        if (!contentRef.current) return false;
        
        const container = contentRef.current.closest('.chat-container');
        if (!container) return false;
        
        const containerRect = container.getBoundingClientRect();
        const contentRect = contentRef.current.getBoundingClientRect();
        
        // In bottom-up mode, we care about the top of the content being visible
        if (!isTopToBottom) {
            // If the top of the content is visible in the viewport
            return contentRect.top >= containerRect.top - 20; // 20px tolerance
        }
        
        // In top-down mode, we care about the bottom being visible
        return contentRect.bottom <= containerRect.bottom + 20; // 20px tolerance
    };

    // Function to smoothly scroll to keep the streaming content in view
    const scrollToKeepInView = () => {
        if (!contentRef.current || !isAutoScrollingRef.current) return;
        
        const container = contentRef.current.closest('.chat-container');
        if (!container) return;
        
        if (!isTopToBottom) {
            // In bottom-up mode, scroll to keep the top of the content visible
            const contentRect = contentRef.current.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            
            if (contentRect.top < containerRect.top) {
                container.scrollBy({
                    top: contentRect.top - containerRect.top,
                    behavior: 'smooth'
                });
            }
        } else {
            // In top-down mode, scroll to bottom
            container.scrollTo({
                top: container.scrollHeight,
                behavior: 'smooth'
            });
        }
    };

    // Reset error when new content starts streaming
    useEffect(() => {
        if (isStreaming) {
            setError(null);
            setIsLoading(true);
        }

        // Listen for network errors during streaming
        const handleStreamError = (event: ErrorEvent) => {
            if (streamingConversations.has(currentConversationId)) {
                if (event.message.includes('network error') || 
                    event.message.includes('ERR_INCOMPLETE_CHUNKED_ENCODING')) {
                    setError('Connection interrupted. Please try again.');
                    removeStreamingConversation(currentConversationId);
                    setIsStreaming(false);
                    setIsLoading(false);
                }
            }
        };
 
        window.addEventListener('error', handleStreamError);
 
        return () => {
            window.removeEventListener('error', handleStreamError);
        };
    }, [isStreaming, currentConversationId, streamingConversations]);
    
    // Set up observer to detect when user is viewing the bottom of content
    useEffect(() => {
        if (!contentRef.current) return;
        
        const observer = new IntersectionObserver(
            (entries) => {
                for (const entry of entries) {
                    // If the content is visible and streaming is happening
                    if (entry.isIntersecting && streamingConversations.has(currentConversationId)) {
                        isAutoScrollingRef.current = true;
                    } else {
                        isAutoScrollingRef.current = false;
                    }
                }
            },
            { threshold: 0.1 }
        );
        
        observer.observe(contentRef.current);
        
        return () => {
            if (contentRef.current) {
                observer.unobserve(contentRef.current);
            }
        };
    }, [currentConversationId, streamingConversations]);

    // Add effect to handle conversation switches
    useEffect(() => {
        // Force scroll event to trigger re-render
        const triggerScroll = () => {
            window.requestAnimationFrame(() => {
                window.dispatchEvent(new CustomEvent('scroll'));
                // Force another scroll after a short delay to ensure content is visible
                setTimeout(() => window.dispatchEvent(new CustomEvent('scroll')), 100);
            });
        };
        triggerScroll();
    }, [currentConversationId, streamedContentMap]);

    // Effect to handle auto-scrolling during streaming
    useEffect(() => {
        if (!streamingConversations.has(currentConversationId)) return;
        
        // Check if we should start auto-scrolling
        if (contentRef.current && isViewingBottom()) {
            isAutoScrollingRef.current = true;
        }
        
        // Set up interval to keep scrolling if needed
        const scrollInterval = setInterval(scrollToKeepInView, 100);
        
        return () => clearInterval(scrollInterval);
    }, [currentConversationId, streamingConversations, streamedContentMap]);

    // Update loading state based on streaming status
    useEffect(() => {
        if (!isStreaming) {
            setIsLoading(false);
        }
    }, [isStreaming]);

    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <div style={{
            display: 'flex',
            // In bottom-up view, reverse the order of elements
            flexDirection: isTopToBottom ? 'column' : 'column-reverse'
        }}>
        {streamingConversations.has(currentConversationId) &&
            !currentMessages.some(msg => msg.role === 'assistant' && 
            msg.content === streamedContentMap.get(currentConversationId)) && (
            <div className="message assistant">
                <div className="message-sender">AI:</div>
                <Suspense fallback={<div>Loading content...</div>}>
                    <>
                        {console.log('StreamedContent rendering:', {
                            content: streamedContentMap.get(currentConversationId),
                            isDiff: streamedContentMap.get(currentConversationId)?.match(/^(---|\+\+\+|@@)/m),
                            firstLines: streamedContentMap.get(currentConversationId)
                                ?.split('\n')
                                .slice(0, 3)
                        })}
                        {error && <ErrorDisplay message={error} />}
                        {!error && (
                            <MarkdownRenderer
                                markdown={streamedContentMap.get(currentConversationId) || ''} 
                                enableCodeApply={enableCodeApply}
                            />
                        )}
                    </>
                </Suspense>
            </div>
        )}

        <div ref={contentRef} style={{ minHeight: '10px' }}></div>
	    {/* Loading indicator - shown at bottom in top-down mode, top in bottom-up mode */}
	    {streamingConversations.has(currentConversationId) &&
              !error && isLoading &&// don't show loading if theres an error
              // Only show loading indicator if we don't have any streamed content yet
              (!streamedContentMap.has(currentConversationId) ||
               streamedContentMap.get(currentConversationId) === '') && (
                <LoadingIndicator />
            )}
        </div>
    );
};

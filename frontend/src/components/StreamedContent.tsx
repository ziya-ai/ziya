import React, { useEffect, Suspense, useState, useRef, useCallback, useLayoutEffect, useMemo } from 'react';
import { useChatContext } from '../context/ChatContext';
import { Space, Alert, Typography } from 'antd';
import StopStreamButton from './StopStreamButton';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';

const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const [connectionLost, setConnectionLost] = useState<boolean>(false);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const contentRef = useRef<HTMLDivElement>(null);
    const lastQuestionRef = useRef<string>('');
    const isAutoScrollingRef = useRef<boolean>(false);
    const lastScrollPositionRef = useRef<number>(0);
    const {
        streamedContentMap,
        isStreaming,
        setIsStreaming,
        currentConversationId,
        streamingConversations,
        currentMessages,
        removeStreamingConversation,
        isTopToBottom,
        question,
    } = useChatContext();

    const streamedContent = useMemo(() => streamedContentMap.get(currentConversationId) || '', [streamedContentMap, currentConversationId]);
    // Track if we have any streamed content to show
    const hasStreamedContent = streamedContentMap.has(currentConversationId) &&
        streamedContentMap.get(currentConversationId) !== '';

    // Store the last question when streaming starts
    useEffect(() => {
        if (streamingConversations.has(currentConversationId)) {
            lastQuestionRef.current = question;
        }
    }, [streamingConversations, currentConversationId, question]);

    // Add direct method to stop streaming
    const stopStreaming = useCallback(() => {
        if (streamingConversations.has(currentConversationId)) {
            console.log('StreamedContent: Stopping streaming for conversation:', currentConversationId, 'Current streaming conversations:', Array.from(streamingConversations));

            // 1. Dispatch custom event to abort the stream (for the fetch request)
            document.dispatchEvent(new CustomEvent('abortStream', {
                detail: { conversationId: currentConversationId }
            }));

            // 2. Explicitly notify the server about the abort
            fetch('/api/abort-stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conversation_id: currentConversationId }),
            })
                .then(response => console.log('Abort API response:', response.status))
                .catch(e => console.warn('Error sending abort notification to server:', e));

            removeStreamingConversation(currentConversationId);
            setIsStreaming(false);
        }
    }, [currentConversationId, removeStreamingConversation, setIsStreaming, streamingConversations]);

    // Listen for streaming stopped events
    useEffect(() => {
        const handleStreamingStopped = (event: CustomEvent) => {
            if (event.detail.conversationId === currentConversationId) {
                stopStreaming();
            }
        };

        document.addEventListener('streamingStopped', handleStreamingStopped as EventListener);
        return () => document.removeEventListener('streamingStopped', handleStreamingStopped as EventListener);
    }, [currentConversationId, stopStreaming]);

    // Monitor connection state
    useEffect(() => {
        const checkConnection = () => {
            if (streamingConversations.has(currentConversationId) && navigator.onLine === false) {
                console.log('Connection lost while streaming');
                setConnectionLost(true);
                // Automatically stop streaming when connection is lost
                stopStreaming();
            } else {
                setConnectionLost(false);
            }
        };

        // Check connection when streaming starts
        if (streamingConversations.has(currentConversationId)) {
            checkConnection();
        }

        // Set up event listeners for online/offline events
        window.addEventListener('online', checkConnection);
        window.addEventListener('offline', checkConnection);

        // Clean up
        return () => {
            window.removeEventListener('online', checkConnection);
            window.removeEventListener('offline', checkConnection);
        };
    }, [currentConversationId, streamingConversations, stopStreaming]);

    const LoadingIndicator = () => (
        <Space>
            <div style={{
                padding: '20px',
                textAlign: 'left',
                color: 'var(--loading-color, #1890ff)',
                width: '100%',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
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

                {/* Only show stop button here if we don't have content yet */}
                {!hasStreamedContent && (
                    <div style={{ marginLeft: 'auto' }}>
                        <StopStreamButton
                            conversationId={currentConversationId}
                            // Pass direct stop function as a prop
                            onStop={stopStreaming}
                        />
                    </div>
                )}
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

    const ConnectionLostAlert = () => (
        <Alert
            message="Connection Lost"
            description="Your internet connection was lost while receiving the response. The stream has been stopped."
            type="warning"
            showIcon
            className="connection-lost"
            style={{ margin: '20px 0' }}
        />
    );
    // Function to check if user is viewing the bottom of the content
    const isViewingBottom = () => {
        if (!contentRef.current) return false;

        const container = contentRef.current.closest('.chat-container');
        if (!container) return true; // Default to true if we can't determine

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
        // Debug scroll events
        const now = Date.now();
        console.log(`scrollToKeepInView called at ${now % 10000}`, {
            isAutoScrolling: isAutoScrollingRef.current,
            isTopToBottom
        });

        if (!contentRef.current || !isAutoScrollingRef.current) return;

        const container = contentRef.current.closest('.chat-container');
        if (!container) return;

        // Store current scroll position
        lastScrollPositionRef.current = container.scrollTop;

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
            // In top-down mode, check if we were already at the bottom
            const isAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 20;

            // Only auto-scroll if we were already at the bottom
            if (isAtBottom) {
                // Use requestAnimationFrame to ensure scroll happens after render
                requestAnimationFrame(() => {
                    container.scrollTo({
                        top: container.scrollHeight,
                        behavior: 'auto' // Use 'auto' to prevent jank during streaming
                    });
                });
            }
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
    }, [isTopToBottom, isStreaming, streamingConversations, currentConversationId]);

    // Set up observer to detect when user is viewing the bottom of content
    const observerRef = useRef<IntersectionObserver>();

    useEffect(() => {
        if (!contentRef.current) return;

        if (observerRef.current) {
            observerRef.current.disconnect();
        }

        observerRef.current = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    isAutoScrollingRef.current = true;
                } else {
                    isAutoScrollingRef.current = false;
                }
            });
        }, { threshold: 0.1, rootMargin: '0px' });

        observerRef.current.observe(contentRef.current);

        return () => {
            observerRef.current?.disconnect();
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
        const scrollInterval = setInterval(scrollToKeepInView, 500); // Reduced frequency

        return () => clearInterval(scrollInterval);
    }, [currentConversationId, streamingConversations, streamedContentMap]);

    // Add a separate effect to handle scroll position restoration
    useEffect(() => {
        if (!streamingConversations.has(currentConversationId)) return;

        const container = contentRef.current?.closest('.chat-container');
        if (!container) return;

        // Store current scroll position before any content changes
        const storeScrollPosition = () => {
            lastScrollPositionRef.current = container.scrollTop;
        };

        // Add event listener to store position before any updates
        container.addEventListener('scroll', storeScrollPosition, { passive: true });

        return () => {
            container.removeEventListener('scroll', storeScrollPosition);
        };
    }, [currentConversationId, streamingConversations]);

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
                        {connectionLost && (
                            <ConnectionLostAlert />
                        )}
                        {/* Show the human message immediately when streaming starts */}
                        {streamingConversations.has(currentConversationId) && (
                            <div className="message human" style={{ marginBottom: '16px' }}>
                                <div className="message-sender">You:</div>
                                <div className="message-content">
                                    <Typography.Paragraph>{lastQuestionRef.current}</Typography.Paragraph>
                                </div>
                            </div>
                        )}
                        <div className="message-sender" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <span>AI:</span>
                            {/* Only show stop button here once we have content */}
                            {streamingConversations.has(currentConversationId) && hasStreamedContent && (
                                <StopStreamButton
                                    conversationId={currentConversationId}
                                    // Pass direct stop function as a prop
                                    onStop={stopStreaming}
                                    style={{ marginLeft: 'auto' }}
                                />
                            )}
                        </div>
                        <Suspense fallback={<div>Loading content...</div>}>
                            <>
                                {error && <ErrorDisplay message={error} />}
                                {!error && (
                                    <MarkdownRenderer
                                        markdown={streamedContent}
                                        isStreaming={streamingConversations.has(currentConversationId)}
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

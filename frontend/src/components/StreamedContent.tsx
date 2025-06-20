import React, { useEffect, useRef, useState, useCallback, useMemo, useTransition, useId, Suspense } from 'react';
import { useChatContext } from '../context/ChatContext';
import { Space, Alert, Typography } from 'antd';
import StopStreamButton from './StopStreamButton';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';
import { useQuestionContext } from '../context/QuestionContext';

const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const [connectionLost, setConnectionLost] = useState<boolean>(false);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const contentRef = useRef<HTMLDivElement>(null);
    const isAutoScrollingRef = useRef<boolean>(false);
    const [isPendingResponse, setIsPendingResponse] = useState<boolean>(false);
    const [hasShownContent, setHasShownContent] = useState<boolean>(false);
    const lastScrollPositionRef = useRef<number>(0);
    const {
        streamedContentMap,
        isStreaming,
        processingState,
        setIsStreaming,
        currentConversationId,
        streamingConversations,
        currentMessages,
        userHasScrolled,
        isTopToBottom,
        removeStreamingConversation,
    } = useChatContext();

    const { question } = useQuestionContext();

    // Use a ref to track the last rendered content to avoid unnecessary re-renders
    const streamedContent = useMemo(() => streamedContentMap.get(currentConversationId) ?? '', [streamedContentMap, currentConversationId]);
    const streamedContentRef = useRef<string>(streamedContent);
    const currentConversationRef = useRef<string>(currentConversationId);
    // Track if we have any streamed content to show
    const hasStreamedContent = streamedContentMap.has(currentConversationId) &&
        streamedContentMap.get(currentConversationId) !== '';

    // Track if we're waiting for a response in this conversation
    useEffect(() => {
        const isWaitingForResponse = streamingConversations.has(currentConversationId);
        setIsPendingResponse(isWaitingForResponse);

        // If we're waiting for a response, ensure isStreaming is true for this conversation
        if (isWaitingForResponse && !isStreaming) {
            setIsStreaming(true);
        }
        streamedContentRef.current = streamedContent;
    }, [currentConversationId, streamingConversations, isStreaming, setIsStreaming, streamedContent]);

    // Track conversation changes and reset content visibility state
    useEffect(() => {
        if (currentConversationRef.current !== currentConversationId) {
            currentConversationRef.current = currentConversationId;
            // Reset the content shown flag when switching conversations
            setHasShownContent(false);
        }
    }, [currentConversationId]);

    // Track when we've shown content for this conversation
    useEffect(() => {
        if (hasStreamedContent && !hasShownContent) {
            setHasShownContent(true);
        }
    }, [hasStreamedContent, hasShownContent]);


    // Update the ref whenever streamed content changes
    useEffect(() => {
        streamedContentRef.current = streamedContent;
        console.log('Streamed content updated:', streamedContent.substring(0, 100));
    }, [streamedContent]);

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
                visibility: processingState === 'awaiting_model_response' ||
                    (!hasStreamedContent && streamingConversations.has(currentConversationId))
                    ? 'visible' : 'hidden',
                opacity: processingState === 'awaiting_model_response' ? 1 : 0.8,
                transition: 'opacity 0.3s ease',
                padding: '10px 20px',
                textAlign: 'left',
                color: 'var(--loading-color, #1890ff)',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                order: isTopToBottom ? 0 : -1  // Place at top if bottom-up view
            }} className="loading-indicator">
                <Space align="center">
                    <div className="ressage-sender" style={{ marginRight: '8px' }}>AI:</div>
                    <RobotOutlined style={{ fontSize: '20px', animation: 'pulse 2s infinite' }} />
                    <LoadingOutlined
                        spin
                        style={{
                            color: processingState === 'awaiting_model_response'
                                ? '#faad14' : '#1890ff' // Orange for tool processing, blue for initial
                        }}
                    />
                    <span style={{
                        color: processingState === 'awaiting_model_response' ? '#faad14' : '#1890ff',
                        animation: 'fadeInOut 2s infinite',
                        verticalAlign: 'middle',
                        marginLeft: '4px',
                        display: 'inline-block'
                    }}>
                        {processingState === 'awaiting_model_response'
                            ? 'Processing tool results...' : 'Processing response...'}
                    </span>

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
    // Function to check if user is viewing the "active end" of content (bottom in top-down, top in bottom-up)
    const isViewingActiveEnd = () => {
        if (!contentRef.current) return false;

        const container = contentRef.current.closest('.chat-container');
        if (!container) return true;

        // In bottom-up mode, check if we're viewing the top (where new content appears)
        if (!isTopToBottom) {
            // Check if we're near the top of the scroll area
            return container.scrollTop <= 50; // 50px tolerance from top
        }

        // In top-down mode, check if we're near the bottom
        const { scrollTop, scrollHeight, clientHeight } = container;
        return Math.abs(scrollHeight - scrollTop - clientHeight) <= 50; // 50px tolerance from bottom
    };


    // Function to smoothly scroll to keep the streaming content in view
    const scrollToKeepInView = () => {
        // Only auto-scroll during active streaming and if user hasn't manually scrolled away
        if (!contentRef.current || !isAutoScrollingRef.current || userHasScrolled) return;

        // Only auto-scroll if we're currently streaming to this conversation
        if (!streamingConversations.has(currentConversationId)) return;

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

            // Only auto-scroll if we were already at the bottom AND we're actively streaming
            if (isAtBottom && streamingConversations.has(currentConversationId)) {
                container.scrollTo({
                    top: container.scrollHeight,
                    behavior: 'auto'
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
                    isAutoScrollingRef.current = !userHasScrolled;
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

        // Only enable auto-scrolling if user hasn't manually scrolled
        isAutoScrollingRef.current = !userHasScrolled;
    }, [currentConversationId, streamingConversations, streamedContentMap, userHasScrolled]);

    // Add a separate effect to handle scroll position restoration
    useEffect(() => {
        if (!streamingConversations.has(currentConversationId)) return;
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
            flexDirection: isTopToBottom ? 'column' : 'column-reverse',
        }}>
            {streamingConversations.has(currentConversationId) &&
                !currentMessages.some(msg => msg.role === 'assistant' &&
                    msg.content === streamedContentMap.get(currentConversationId)) &&
                (hasStreamedContent || !hasShownContent) && (
                    <div className="message assistant">
                        {connectionLost && (
                            <ConnectionLostAlert />
                        )}
                        {/* Show the human message immediately when streaming starts */}
                        {streamingConversations.has(currentConversationId) && question && (
                            <div className="message human" style={{ marginBottom: '16px' }}>
                                <div className="message-sender">You:</div>
                                <div className="message-content">
                                    <Typography.Paragraph>{question}</Typography.Paragraph>
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
                                {/* Only render if we have actual content */}
                                {error && <><ErrorDisplay message={error} /><br /></>}
                                {!error && streamedContent && streamedContent.trim() && (
                                    <MarkdownRenderer
                                        key={`stream-${currentConversationId}`}
                                        markdown={streamedContent}
                                        forceRender={streamingConversations.has(currentConversationId)}
                                        isStreaming={streamingConversations.has(currentConversationId)}
                                        enableCodeApply={enableCodeApply}
                                    />
                                )}
                                {/* Show content even when there's an error */}
                                {error && streamedContent && streamedContent.trim() && (
                                    <div style={{ opacity: 0.8 }}>
                                        <MarkdownRenderer
                                            key={`stream-${currentConversationId}-with-error`}
                                            markdown={streamedContent}
                                            forceRender={streamingConversations.has(currentConversationId)}
                                            isStreaming={streamingConversations.has(currentConversationId)}
                                            enableCodeApply={enableCodeApply}
                                        />
                                    </div>
                                )}

                            </>
                        </Suspense>
                    </div>
                )}

            <div ref={contentRef} style={{ minHeight: '10px' }}></div>
            {/* Loading indicator - shown at bottom in top-down mode, top in bottom-up mode */}
            {streamingConversations.has(currentConversationId) &&
                !error && (isLoading || isPendingResponse) && // don't show loading if there's an error
                // Only show loading indicator if we don't have any streamed content yet and haven't started rendering
                (!streamedContentMap.has(currentConversationId) ||
                    streamedContentMap.get(currentConversationId) === '') && (
                    <LoadingIndicator />
                )}
        </div>
    );
};

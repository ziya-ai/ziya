import React, { useEffect, useRef, Suspense, memo, useCallback, useMemo } from "react";
import { useChatContext } from '../context/ChatContext';
import { EditSection } from "./EditSection";
import { Space, Spin, Button, Tooltip } from 'antd';
import { LoadingOutlined, RobotOutlined, RedoOutlined } from "@ant-design/icons";
import { sendPayload } from "../apis/chatApi";
import { useFolderContext } from "../context/FolderContext";
import ModelChangeNotification from './ModelChangeNotification';
import { convertKeysToStrings, Message } from "../utils/types";

// Lazy load the MarkdownRenderer
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

// Update the Message type to include system role and modelChange property
type MessageRole = 'human' | 'assistant' | 'system';

interface ConversationProps {
    enableCodeApply: boolean;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply }) => {
    const { currentMessages,
        isTopToBottom,
        isLoadingConversation,
        addStreamingConversation,
        streamingConversations,
        currentConversationId,
        setIsStreaming,
        isStreamingAny,
        setStreamedContentMap,
        isStreaming,
        addMessageToConversation,
        userHasScrolled,
        removeStreamingConversation,
        streamedContentMap
    } = useChatContext();

    const { checkedKeys } = useFolderContext();
    const visibilityRef = useRef<boolean>(true);
    // Sort messages to maintain order
    const messageIds = useMemo(() => currentMessages.map(m => m.id), [currentMessages]);
    const displayMessages = isTopToBottom ? currentMessages : [...currentMessages].reverse();

    // Keep track of rendered messages for performance monitoring
    const modelChangeHandlerRef = useRef<((event: CustomEvent) => void) | null>(null);
    const renderedCountRef = useRef(0);
    const renderedSystemMessagesRef = useRef<Set<string>>(new Set());
    const activeStreamingRef = useRef<Set<string>>(new Set());
    const processedModelChangesRef = useRef<Set<string>>(new Set());

    // Track which conversations have received streaming content
    const conversationHasStreamedContent = useCallback((conversationId: string) => {
        return streamedContentMap.has(conversationId) &&
            streamedContentMap.get(conversationId) !== '';
    }, [streamedContentMap]);

    // Effect to handle scrolling when messages change
    useEffect(() => {
        // Only scroll if we're not streaming or user hasn't manually scrolled
        if (!isStreaming && !userHasScrolled) {
            const chatContainer = document.querySelector('.chat-container');
            if (chatContainer && isTopToBottom) {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }
        }
    }, [currentMessages.length, isStreaming, userHasScrolled, isTopToBottom]);


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

    // Update active streaming conversations reference
    useEffect(() => {
        // Create the handler function
        const handleModelChange = (event: CustomEvent) => {
            console.log('Conversation received model change event:', event.detail);
            const { previousModel, newModel } = event.detail;

            // Create a unique key for this model change to prevent duplicates
            const changeKey = `${previousModel}->${newModel}`;

            // Skip if we've already processed this exact change
            if (processedModelChangesRef.current.has(changeKey)) {
                console.log('Skipping duplicate model change:', changeKey);
                return;
            }
            processedModelChangesRef.current.add(changeKey);

            // Add system message about model change
            if (previousModel && newModel) {
                addMessageToConversation({
                    role: 'system',
                    content: `Model changed from ${previousModel} to ${newModel}`,
                    modelChange: {
                        from: previousModel,
                        to: newModel,
                        changeKey: changeKey
                    }
                }, currentConversationId);
            }
        };

        // Add and remove event listener
        window.addEventListener('modelChanged', handleModelChange as EventListener);
        return () => {
            // Reset processed changes when component unmounts
            processedModelChangesRef.current.clear();
            renderedSystemMessagesRef.current.clear();
            window.removeEventListener('modelChanged', handleModelChange as EventListener);
        };
    }, [currentConversationId, addMessageToConversation]);

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
        const isCurrentlyStreaming = streamingConversations.has(currentConversationId);
        const hasStreamingContent = conversationHasStreamedContent(currentConversationId);

        // Show retry if this is a human message and either:
        // 1. It's the last message, or
        // 2. The next message isn't from the assistant
        // But don't show if we're currently streaming or have streaming content for this conversation
        return message?.role === 'human' &&
            !isCurrentlyStreaming &&
            !hasStreamingContent &&
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
                                currentMessages,
                                message.content,
                                convertKeysToStrings(checkedKeys),
                                currentConversationId,
                                setStreamedContentMap,
                                setIsStreaming,
                                removeStreamingConversation,
                                addMessageToConversation,
                                streamingConversations.has(currentConversationId),
                            );
                        } catch (error) {
                            setIsStreaming(false);
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
                    const isCurrentlyStreaming = streamingConversations.has(currentConversationId);
                    const hasStreamingContent = conversationHasStreamedContent(currentConversationId);

                    const needsResponse = msg.role === 'human' &&
                        !isCurrentlyStreaming &&
                        !hasStreamingContent &&
                        (actualIndex === currentMessages.length - 1 ||
                            (hasNextMessage && nextMessage?.role !== 'assistant'));

                    // Create a unique key for system messages to prevent duplicate logging
                    const systemMessageKey = msg.role === 'system' && msg.modelChange ?
                        `${msg.modelChange.from}->${msg.modelChange.to}` :
                        msg.content;

                    // Only log system messages once
                    if (msg.role === 'system' && !renderedSystemMessagesRef.current.has(systemMessageKey)) {
                        renderedSystemMessagesRef.current.add(systemMessageKey);
                        console.log('Rendering system message:', {
                            content: msg.content,
                            hasModelChange: Boolean(msg.modelChange),
                            modelChangeFrom: msg.modelChange?.from,
                            modelChangeTo: msg.modelChange?.to,
                            messageIndex: index,
                            totalMessages: displayMessages?.length || 0
                        });
                    }

                    return <div
                        // Use message ID as key instead of index
                        key={`message-${msg.id || index}`}
                        className={`message ${msg.role || ''}${needsResponse
                            ? ' needs-response'
                            : ''
                            }`}
                    >
                        {/* Handle system messages with model changes first */}
                        {msg.role === 'system' && msg.modelChange ? (
                            <ModelChangeNotification
                                previousModel={msg.modelChange.from}
                                changeKey={msg.modelChange.changeKey}
                                newModel={msg.modelChange.to}
                            />
                        ) : (
                            // Skip rendering empty messages entirely
                            msg.content ? (
                                // Regular message rendering for messages with content
                                <>
                                    {msg.role === 'human' && (
                                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                            <div className="message-sender">You:</div>
                                            <div style={{ display: 'flex', gap: '8px' }}>
                                                {needsResponse && renderRetryButton(actualIndex)}
                                                <EditSection index={isTopToBottom ? index : currentMessages.length - 1 - index} />
                                            </div>
                                        </div>
                                    )}

                                    {msg.role === 'assistant' && msg.content && (
                                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                            <div className="message-sender">AI:</div>
                                            {renderRetryButton(actualIndex)}
                                        </div>
                                    )}

                                    <div className="message-content">
                                        <Suspense fallback={<div>Loading content...</div>}>
                                            <MarkdownRenderer
                                                markdown={msg.content}
                                                enableCodeApply={enableCodeApply}
                                                isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
                                            />
                                        </Suspense>
                                    </div>
                                </>
                            ) : null
                        )}
                    </div>;
                })}
            </div>
        </div>
    );
});

export default Conversation;

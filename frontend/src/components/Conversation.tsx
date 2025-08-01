import React, { useEffect, useRef, Suspense, memo, useCallback, useMemo } from "react";
import { useChatContext } from '../context/ChatContext';
import { EditSection } from "./EditSection";
import { Spin, Button, Tooltip } from 'antd';
import { RedoOutlined, SoundOutlined, MutedOutlined } from "@ant-design/icons";
import { sendPayload } from "../apis/chatApi";
import { useFolderContext } from "../context/FolderContext";
import ModelChangeNotification from './ModelChangeNotification';
import { convertKeysToStrings } from "../utils/types";
import { useQuestionContext } from '../context/QuestionContext';
import { isDebugLoggingEnabled, debugLog } from '../utils/logUtils';

// Lazy load the MarkdownRenderer
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

interface ConversationProps {
    enableCodeApply: boolean;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply }) => {
    const { currentMessages,
        editingMessageIndex,
        isTopToBottom,
        isLoadingConversation,
        addStreamingConversation,
        streamingConversations,
        currentConversationId,
        setIsStreaming,
        setStreamedContentMap,
        isStreaming,
        addMessageToConversation,
        removeStreamingConversation,
        streamedContentMap,
        userHasScrolled,
        updateProcessingState,
        setConversations,
        toggleMessageMute,
    } = useChatContext();

    // Don't block conversation rendering on folder context
    const folderContext = useFolderContext();
    const checkedKeys = folderContext?.checkedKeys || [];
    const { setQuestion } = useQuestionContext();
    const visibilityRef = useRef<boolean>(true);
    // Sort messages to maintain order
    const displayMessages = isTopToBottom ? currentMessages : [...currentMessages].reverse();

    // Keep track of rendered messages for performance monitoring
    const renderedCountRef = useRef(0);
    const renderedSystemMessagesRef = useRef<Set<string>>(new Set());
    const processedModelChangesRef = useRef<Set<string>>(new Set());

    // Track which conversations have received streaming content
    const conversationHasStreamedContent = useCallback((conversationId: string) => {
        return streamedContentMap.has(conversationId) &&
            streamedContentMap.get(conversationId) !== '';
    }, [streamedContentMap]);

    // Effect to handle scrolling when messages change
    useEffect(() => {
        // Only scroll if we're not streaming or user hasn't manually scrolled
        // Removed auto-scrolling from Conversation component to prevent conflicts
        // StreamedContent handles scrolling during streaming
    }, [currentMessages.length, isStreaming, userHasScrolled, isTopToBottom]);


    useEffect(() => {
        // Only log when message count changes significantly, not on every render
        if (Math.abs(currentMessages.length - renderedCountRef.current) > 2) {
            if (isDebugLoggingEnabled()) {
                debugLog('Conversation messages updated:', {
                    messageCount: currentMessages.length,
                    previousCount: renderedCountRef.current,
                    isVisible: visibilityRef.current,
                    displayOrder: isTopToBottom ? 'top-down' : 'bottom-up'
                });
            }
            renderedCountRef.current = currentMessages.length;
        }

        // Set up visibility observer
        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach(entry => {
                    visibilityRef.current = entry.isIntersecting;
                });
            },
            { threshold: 0.1 }
        );

        return () => observer.disconnect();
    }, [isTopToBottom, currentMessages.length]);

    // Update active streaming conversations reference
    useEffect(() => {
        // Create the handler function
        const handleModelChange = (event: CustomEvent) => {
            const { previousModel, newModel } = event.detail;

            // Create a unique key for this model change to prevent duplicates
            const changeKey = `${previousModel}->${newModel}`;

            // Skip if we've already processed this exact change
            if (processedModelChangesRef.current.has(changeKey)) {
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
                            // Filter out muted messages before retrying - explicitly exclude muted messages
                            const messagesToSend = currentMessages.filter(msg => !msg.muted);
                            await sendPayload(
                                messagesToSend,
                                message.content,
                                convertKeysToStrings(checkedKeys || []),
                                currentConversationId,
                                setStreamedContentMap,
                                setIsStreaming,
                                removeStreamingConversation,
                                addMessageToConversation,
                                streamingConversations.has(currentConversationId),
                                (state: 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error') => updateProcessingState(currentConversationId, state)
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

    // Render mute button
    const renderMuteButton = (index: number) => {
        // Don't show mute button if this message is being edited
        if (editingMessageIndex === index) {
            return null;
        }

        const message = currentMessages[index];

        // Don't show mute button if there's an error state (retry button is showing)
        if (shouldShowRetry(index)) {
            return null;
        }

        if (!message || message.role === 'system') return null;

        return (
            <Tooltip title={message.muted ? "Unmute (include in context)" : "Mute (exclude from context)"}>
                <Button
                    icon={message.muted ? <MutedOutlined /> : <SoundOutlined />}
                    type="default"
                    size="small"
                    style={{
                        padding: '0 8px',
                        minWidth: '32px',
                        height: '32px'
                    }}
                    onClick={() => {
                        toggleMessageMute(currentConversationId, index);
                    }}
                />
            </Tooltip>
        );
    };

    // Render resubmit button for human messages
    const renderResubmitButton = (index: number) => {
        // Don't show resubmit button if this message is being edited
        if (editingMessageIndex === index) {
            return null;
        }

        const message = currentMessages[index];

        // Don't show resubmit button if there's an error state (retry button is showing)
        if (shouldShowRetry(index)) {
            return null;
        }

        if (!message || message.role !== 'human') return null;

        // Don't show resubmit button if we're currently streaming
        const isCurrentlyStreaming = streamingConversations.has(currentConversationId);
        if (isCurrentlyStreaming) return null;

        return (
            <Tooltip title="Resubmit this question">
                <Button
                    icon={<RedoOutlined />}
                    type="default"
                    size="small"
                    style={{
                        padding: '0 8px',
                        minWidth: '32px',
                        height: '32px'
                    }}
                    onClick={() => {
                        // Clear any existing streamed content
                        setStreamedContentMap(new Map());

                        // Create truncated message array up to and including this message
                        const truncatedMessages = currentMessages.slice(0, index + 1);

                        // Filter out muted messages from truncated messages - explicitly exclude muted messages
                        const messagesToSend = truncatedMessages.filter(msg => !msg.muted);
                        // Set conversation to just the truncated messages
                        setConversations(prev => prev.map(conv =>
                            conv.id === currentConversationId
                                ? { ...conv, messages: truncatedMessages, _version: Date.now() }
                                : conv
                        ));

                        // Start streaming immediately
                        addStreamingConversation(currentConversationId);

                        // Send the payload
                        (async () => {
                            try {
                                // messagesToSend is already filtered above
                                await sendPayload(
                                    messagesToSend, // Already filtered for muted messages
                                    message.content,
                                    convertKeysToStrings(checkedKeys || []),
                                    currentConversationId,
                                    setStreamedContentMap,
                                    setIsStreaming,
                                    removeStreamingConversation,
                                    addMessageToConversation,
                                    streamingConversations.has(currentConversationId),
                                    (state: 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error') => updateProcessingState(currentConversationId, state)
                                );
                            } catch (error) {
                                setIsStreaming(false);
                                removeStreamingConversation(currentConversationId);
                                console.error('Error resubmitting message:', error);
                            }
                        })();
                        // Clear the question field since we're submitting directly
                        setQuestion('');
                    }}
                />
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

                    // Only log system messages once and only in development mode
                    if (process.env.NODE_ENV === 'development' && 
                        msg.role === 'system' && 
                        !renderedSystemMessagesRef.current.has(systemMessageKey)) {
                        renderedSystemMessagesRef.current.add(systemMessageKey);
                    }

                    return <div
                        // Use message ID as key instead of index
                        key={`message-${msg.id || index}`}
                        className={`message ${msg.role || ''}${msg.muted ? ' muted' : ''}${needsResponse
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
                                        <div style={{ display: editingMessageIndex === actualIndex ? 'none' : 'flex', justifyContent: 'space-between' }}>
                                            <div className="message-sender">You:</div>
                                            <div style={{
                                                display: 'flex',
                                                gap: '8px',
                                                alignItems: 'center',
                                                marginRight: '8px'
                                            }}>
                                                {renderMuteButton(actualIndex)}
                                                {renderResubmitButton(actualIndex)}
                                                {needsResponse && renderRetryButton(actualIndex)}
                                                <EditSection index={actualIndex} isInline={true} />
                                            </div>
                                        </div>
                                    )}

                                    {/* Only show edit section when editing, otherwise show message content */}
                                    {msg.role === 'human' && editingMessageIndex === actualIndex ? (
                                        <EditSection index={actualIndex} isInline={false} />
                                    ) : msg.role === 'human' && msg.content ? (
                                        <div className="message-content">
                                            <Suspense fallback={<div>Loading content...</div>}>
                                                <MarkdownRenderer
                                                    markdown={msg.content}
                                                    enableCodeApply={enableCodeApply}
                                                    isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
                                                />
                                            </Suspense>
                                        </div>
                                    ) : msg.role === 'assistant' && msg.content && (
                                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                            <div className="message-sender">AI:</div>
                                            <div style={{
                                                display: 'flex',
                                                gap: '8px',
                                                alignItems: 'center',
                                                marginRight: '8px'
                                            }}>
                                                {renderMuteButton(actualIndex)}
                                            </div>
                                            {renderRetryButton(actualIndex)}
                                        </div>
                                    )}

                                    {/* Only show message content for assistant messages or non-editing human messages */}
                                    {msg.role === 'assistant' && msg.content && (
                                        <div className="message-content">
                                            <Suspense fallback={<div>Loading content...</div>}>
                                                <MarkdownRenderer
                                                    markdown={msg.content}
                                                    enableCodeApply={enableCodeApply}
                                                    isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
                                                />
                                            </Suspense>
                                        </div>
                                    )}
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

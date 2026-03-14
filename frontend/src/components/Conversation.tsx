import React, { useEffect, useRef, memo, useCallback, useMemo, useState } from "react";
import { useChatContext } from '../context/ChatContext';
import { EditSection } from "./EditSection";
import { Spin, Button, Tooltip, Image as AntImage } from 'antd';
import { RedoOutlined, SoundOutlined, MutedOutlined, PictureOutlined } from "@ant-design/icons";
import { sendPayload } from "../apis/chatApi";

import ModelChangeNotification from './ModelChangeNotification';
import { convertKeysToStrings } from "../utils/types";
import { useSetQuestion } from '../context/QuestionContext';
import { useFolderContext } from '../context/FolderContext';
import { isDebugLoggingEnabled, debugLog } from '../utils/logUtils';
import { useProject } from '../context/ProjectContext';

// Lazy load the MarkdownRenderer
import { MarkdownRenderer } from "./MarkdownRenderer";
interface ConversationProps {
    enableCodeApply: boolean;
    onOpenShellConfig?: () => void;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply, onOpenShellConfig }) => {
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
        recordManualScroll,
    } = useChatContext();

    const { checkedKeys } = useFolderContext();
    const { currentProject, activeSkillPrompts } = useProject();
    const setQuestion = useSetQuestion();
    const visibilityRef = useRef<boolean>(true);
    // Memoize conversation-specific streaming state to prevent unnecessary re-renders
    const conversationStreamingState = useMemo(() => ({
        isCurrentlyStreaming: streamingConversations.has(currentConversationId),
        hasStreamedContent: streamedContentMap.has(currentConversationId) &&
            streamedContentMap.get(currentConversationId) !== '',
        streamedContent: streamedContentMap.get(currentConversationId) || ''
    }), [streamingConversations, streamedContentMap, currentConversationId]);

    // Extract for use in component
    const { isCurrentlyStreaming, hasStreamedContent } = conversationStreamingState;

    // Conversation switch overlay: show a spinner immediately when the user
    // switches conversations.  The heavy MarkdownRenderer work blocks the
    // main thread so the browser never gets a chance to paint a spinner set
    // via React state.  We use direct DOM manipulation to guarantee the
    // overlay is visible before React starts its synchronous render pass.
    const switchOverlayRef = useRef<HTMLDivElement>(null);
    const prevConversationRef = useRef(currentConversationId);

    // Show overlay synchronously via DOM when conversation changes
    useEffect(() => {
        if (prevConversationRef.current !== currentConversationId) {
            prevConversationRef.current = currentConversationId;
            // Show overlay immediately via DOM (bypasses React render batching)
            if (switchOverlayRef.current) {
                switchOverlayRef.current.style.display = 'flex';
            }
        }
    }, [currentConversationId]);

    // Hide overlay after messages have rendered
    useEffect(() => {
        if (switchOverlayRef.current && currentMessages.length > 0) {
            // Use rAF to ensure at least one paint occurred with the new content
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    if (switchOverlayRef.current) {
                        switchOverlayRef.current.style.display = 'none';
                    }
                });
            });
        }
    }, [currentMessages]);

    const previousStreamingStateRef = useRef<boolean>(false);

    // Check if we should show retry button for a message
    const shouldShowRetry = useCallback((index: number) => {
        const message = currentMessages[index];
        if (!message || message.role !== 'human') return false;

        const nextIndex = index + 1;
        const hasNextMessage = nextIndex < currentMessages.length;
        const nextMessage = hasNextMessage ? currentMessages[nextIndex] : null;

        // Show retry if this human message doesn't have an assistant response following it
        return !hasNextMessage || nextMessage?.role !== 'assistant';
    }, [currentMessages]);

    // Render retry button with explanation
    const renderRetryButton = useCallback((index: number) => {
        if (!shouldShowRetry(index)) return null;

        return (
            <Tooltip title="The AI response may have failed. Click to retry.">
                <Button
                    icon={<RedoOutlined />}
                    type="primary"
                    size="small"
                    onClick={async () => {
                        const message = currentMessages[index];

                        const chatContainer = document.querySelector('.chat-container');
                        if (chatContainer) {
                            const isAtEnd = isTopToBottom ?
                                (chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight) < 50 :
                                chatContainer.scrollTop < 50;

                            if (!isAtEnd) {
                                recordManualScroll();
                                console.log('📜 Retry while scrolled away - position locked');
                            }
                        }

                        addStreamingConversation(currentConversationId);
                        try {
                            const messagesToSend = currentMessages.filter(msg => !msg.muted);
                            await sendPayload(
                                messagesToSend,
                                message.content,
                                convertKeysToStrings(checkedKeys || []),
                                currentConversationId,
                                activeSkillPrompts || undefined,
                                message.images, // Include original images in retry
                                streamedContentMap,
                                setStreamedContentMap,
                                setIsStreaming,
                                removeStreamingConversation,
                                addMessageToConversation,
                                streamingConversations.has(currentConversationId),
                                (state: 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error') => updateProcessingState(currentConversationId, state),
                                undefined, // setReasoningContentMap
                                undefined, // throttlingRecoveryDataRef
                                currentProject
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
    }, [shouldShowRetry, currentMessages, isTopToBottom, recordManualScroll, addStreamingConversation, currentConversationId, checkedKeys, streamedContentMap, setStreamedContentMap, setIsStreaming, removeStreamingConversation, addMessageToConversation, streamingConversations, updateProcessingState]);

    // Render mute button
    const renderMuteButton = useCallback((index: number) => {
        if (editingMessageIndex === index) {
            return null;
        }

        const message = currentMessages[index];

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
    }, [editingMessageIndex, currentMessages, shouldShowRetry, toggleMessageMute, currentConversationId]);

    // Render resubmit button for human messages
    const renderResubmitButton = useCallback((index: number) => {
        if (editingMessageIndex === index) {
            return null;
        }

        const message = currentMessages[index];

        if (shouldShowRetry(index)) {
            return null;
        }

        if (!message || message.role !== 'human') return null;

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
                        setStreamedContentMap(new Map());

                        const truncatedMessages = currentMessages.slice(0, index + 1);
                        const messagesToSend = truncatedMessages.filter(msg => !msg.muted);

                        setConversations(prev => prev.map(conv =>
                            conv.id === currentConversationId
                                ? { ...conv, messages: truncatedMessages, _version: Date.now() }
                                : conv
                        ));

                        addStreamingConversation(currentConversationId);

                        (async () => {
                            try {
                                await sendPayload(
                                    messagesToSend,
                                    message.content,
                                    convertKeysToStrings(checkedKeys || []),
                                    currentConversationId,
                                    activeSkillPrompts || undefined,
                                    message.images, // Include original images
                                    streamedContentMap,
                                    setStreamedContentMap,
                                    setIsStreaming,
                                    removeStreamingConversation,
                                    addMessageToConversation,
                                    streamingConversations.has(currentConversationId),
                                (state: 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error') => updateProcessingState(currentConversationId, state),
                                undefined, // setReasoningContentMap
                                undefined, // throttlingRecoveryDataRef
                                currentProject
                                );
                            } catch (error) {
                                setIsStreaming(false);
                                removeStreamingConversation(currentConversationId);
                                console.error('Error resubmitting message:', error);
                            }
                        })();
                        setQuestion('');
                    }}
                />
            </Tooltip>
        );
    }, [editingMessageIndex, currentMessages, shouldShowRetry, isCurrentlyStreaming, setStreamedContentMap, setConversations, currentConversationId, addStreamingConversation, checkedKeys, streamedContentMap, setIsStreaming, removeStreamingConversation, addMessageToConversation, streamingConversations, updateProcessingState, setQuestion]);

    const displayMessages = isTopToBottom ? currentMessages : [...currentMessages].reverse();

    // Keep track of rendered messages for performance monitoring  
    const renderedCountRef = useRef(0);
    const renderedSystemMessagesRef = useRef<Set<string>>(new Set());

    // Track which conversations have received streaming content
    const conversationHasStreamedContent = useCallback((conversationId: string) => {
        return streamedContentMap.has(conversationId) &&
            streamedContentMap.get(conversationId) !== '';
    }, [streamedContentMap]);

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

    // Detect when OTHER conversations complete streaming
    useEffect(() => {
        // Track which conversations just stopped streaming
        const previousSet = previousStreamingStateRef.current ? new Set([currentConversationId]) : new Set();
        const currentSet = new Set(Array.from(streamingConversations));

        const wasStreaming = previousStreamingStateRef.current;
        const isNowStreaming = isCurrentlyStreaming;

        // Update ref
        previousStreamingStateRef.current = isNowStreaming;

        // Detect if ANY conversation finished (including background ones)
        const streamingEnded = streamingConversations.size < previousSet.size ||
            (wasStreaming && !isNowStreaming);

        if (streamingEnded) {
            // Check if it was the current conversation or a background one
            if (wasStreaming && !isNowStreaming) {
                console.log('✅ Current conversation finished streaming');
                // Scroll behavior handled by scrollToBottom in ChatContext
            } else if (streamingConversations.size < previousSet.size) {
                // A background conversation finished
                console.log('📌 Background conversation finished - locking scroll position');
                // CRITICAL: Lock scroll position to prevent any movement
                recordManualScroll();
            }
        }
    }, [isCurrentlyStreaming, streamingConversations, currentConversationId, recordManualScroll]);


    return (
        <div style={{ position: 'relative' }}>
            {/* Always-mounted overlay — shown/hidden via direct DOM manipulation
                to guarantee visibility before React's synchronous render blocks the thread */}
            <div
                ref={switchOverlayRef}
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    backgroundColor: 'rgba(0, 0, 0, 0.5)',
                    display: 'none',
                    justifyContent: 'center',
                    alignItems: 'center',
                    zIndex: 1000
                }}>
                <Spin size="large" tip="Loading conversation..." />
            </div>
            <div
                style={{
                    opacity: isLoadingConversation ? 0.5 : 1,
                    minHeight: '50px' // Ensure visibility detection
                }}
                className="conversation-messages-container"
            >
                {displayMessages?.map((msg, index) => {
                    // Convert display index to actual index for bottom-up mode
                    const actualIndex = isTopToBottom ? index : currentMessages.length - 1 - index;
                    const nextActualIndex = actualIndex + 1;
                    const hasNextMessage = nextActualIndex < currentMessages.length;
                    const nextMessage = hasNextMessage ? currentMessages[nextActualIndex] : null;
                    const needsResponse = msg.role === 'human' &&
                        !isCurrentlyStreaming &&
                        !hasStreamedContent &&
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
                            ? ' needs-response' : ''
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
                                            <div className="message-sender">
                                                You{msg._isFeedback && msg._feedbackStatus === 'pending' && (
                                                    <span style={{
                                                        color: '#faad14',
                                                        fontSize: '12px',
                                                        marginLeft: '4px',
                                                        fontStyle: 'italic'
                                                    }}>(pending feedback)</span>
                                                )}:
                                            </div>
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
                                    ) : msg.role === 'human' && (msg.content || (msg.images && msg.images.length > 0)) ? (
                                        <>
                                            {/* Display attached images if present */}
                                            {msg.images && msg.images.length > 0 && (
                                                <div style={{
                                                    marginBottom: '12px',
                                                    display: 'flex',
                                                    gap: '8px',
                                                    flexWrap: 'wrap'
                                                }}>
                                                    {msg.images.map((img, imgIndex) => (
                                                        <div key={imgIndex} style={{
                                                            position: 'relative',
                                                            display: 'inline-block'
                                                        }}>
                                                            <AntImage
                                                                src={`data:${img.mediaType};base64,${img.data}`}
                                                                alt={img.filename || 'Attached image'}
                                                                width={120}
                                                                height={120}
                                                                style={{ objectFit: 'cover', borderRadius: '4px', border: '1px solid #d9d9d9' }}
                                                                preview={{ mask: <PictureOutlined /> }}
                                                            />
                                                            {img.filename && <div style={{ fontSize: '11px', textAlign: 'center', marginTop: '4px', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }}>{img.filename}</div>}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            {/* Only render message content if there's actual text content */}
                                            {msg.content && <div className="message-content">
                                                <MarkdownRenderer
                                                    markdown={msg.content}
                                                    enableCodeApply={enableCodeApply}
                                                    onOpenShellConfig={onOpenShellConfig}
                                                        isStreaming={false}
                                                />
                                            </div>}
                                        </>
                                    ) : msg.role === 'assistant' && msg.content ? (
                                        <>
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
                                            <div className="message-content">
                                                <MarkdownRenderer
                                                    markdown={msg.content}
                                                    enableCodeApply={enableCodeApply}
                                                    onOpenShellConfig={onOpenShellConfig}
                                                        isStreaming={false}
                                                />
                                            </div>
                                        </>
                                    ) : null}
                                </>
                            ) : null
                        )}
                    </div>;
                })}

                {/* Fallback for when no messages to display */}
                {(!displayMessages || displayMessages.length === 0) && (
                    <div style={{
                        textAlign: 'center',
                        padding: '2rem',
                        color: '#999'
                    }}>
                        No messages in this conversation yet.
                    </div>
                )}
            </div>
        </div>
    );
}, (prevProps, nextProps) => {
    // Custom comparison to prevent re-renders on unrelated changes
    return prevProps.enableCodeApply === nextProps.enableCodeApply;
});

// Set display name for debugging
Conversation.displayName = 'Conversation';

export default Conversation;

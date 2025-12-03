import React, { useEffect, useRef, Suspense, memo, useCallback, useMemo, useState } from "react";
import { VariableSizeList as List } from 'react-window';
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
    
    const previousStreamingStateRef = useRef<boolean>(false);
    
    
    // Virtualized rendering for large conversations to improve performance
    // DISABLED: Virtualization causes rendering corruption with dynamic markdown content
    // TODO: Implement proper virtualization with accurate height measurement
    const shouldVirtualize = false;
    
    // Refs for virtualization
    
    // Refs for virtualization
    const listRef = useRef<List>(null);
    const rowHeightsRef = useRef<{ [index: number]: number }>({});
    
    // Reset height cache when messages change significantly
    useEffect(() => {
        rowHeightsRef.current = {};
        listRef.current?.resetAfterIndex(0);
    }, [currentMessages.length]);
    
    // Function to get estimated/measured item size
    const getItemSize = useCallback((index: number): number => {
        // Return cached height if available
        if (rowHeightsRef.current[index]) {
            return rowHeightsRef.current[index];
        }
        
        // Estimate based on message type and content length
        const actualIndex = isTopToBottom ? index : currentMessages.length - 1 - index;
        const msg = currentMessages[actualIndex];
        
        if (!msg) return 100;
        
        const contentLength = msg.content?.length || 0;
        
        // Estimate height based on content characteristics
        if (msg.role === 'system') return 60;
        
        // More accurate estimates for different content types
        if (msg.content?.includes('```diff') || msg.content?.includes('diff --git')) {
            // Diffs: larger base size + more per line
            const lines = msg.content.split('\n').length;
            return Math.min(5000, 200 + lines * 22);
        }
        
        if (msg.content?.includes('```')) {
            // Code blocks: more generous estimate
            const lines = msg.content.split('\n').length;
            return Math.min(3000, 180 + lines * 20);
        }
        
        if (msg.content?.includes('<!-- TOOL_BLOCK')) {
            // Tool blocks: moderate size
            return Math.min(2000, 150 + Math.floor(contentLength / 100) * 15);
        }
        
        // Regular text: estimate 20px per 100 chars + 80px base
        // Increase estimates for safety
        return Math.min(2000, 100 + Math.floor(contentLength / 80) * 22);
    }, [currentMessages, isTopToBottom]);
    
    // Set measured height after render
    const setItemHeight = useCallback((index: number, size: number) => {
        if (rowHeightsRef.current[index] !== size) {
            rowHeightsRef.current[index] = size;
            listRef.current?.resetAfterIndex(index);
        }
    }, []);
    
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
                                streamedContentMap,
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
                                    streamedContentMap,
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
                        setQuestion('');
                    }}
                />
            </Tooltip>
        );
    }, [editingMessageIndex, currentMessages, shouldShowRetry, isCurrentlyStreaming, setStreamedContentMap, setConversations, currentConversationId, addStreamingConversation, checkedKeys, streamedContentMap, setStreamedContentMap, setIsStreaming, removeStreamingConversation, addMessageToConversation, streamingConversations, updateProcessingState, setQuestion]);
    
    // Render message for both virtualized and non-virtualized views
    // Measure height after render for virtualization
    const measuredRefs = useRef<Map<number, HTMLDivElement>>(new Map());
    
    useEffect(() => {
        measuredRefs.current.forEach((el, index) => {
            if (el) {
                const height = el.getBoundingClientRect().height;
                if (height > 0) setItemHeight(index, height);
            }
        });
    });
    
    const renderMessage = useCallback(({ index, style }: { index: number; style: React.CSSProperties }) => {
        const actualIndex = isTopToBottom ? index : currentMessages.length - 1 - index;
        const msg = currentMessages[actualIndex];
        const nextActualIndex = actualIndex + 1;
        const hasNextMessage = nextActualIndex < currentMessages.length;
        const nextMessage = hasNextMessage ? currentMessages[nextActualIndex] : null;
        
        if (!msg) return <div style={style} />;
        
        const needsResponse = msg.role === 'human' &&
            !isCurrentlyStreaming &&
            !hasStreamedContent &&
            (actualIndex === currentMessages.length - 1 ||
                (hasNextMessage && nextMessage?.role !== 'assistant'));
        
        const systemMessageKey = msg.role === 'system' && msg.modelChange ?
            `${msg.modelChange.from}->${msg.modelChange.to}` :
            msg.content;
        
        return (
            <div 
                ref={(el) => {
                    if (el) measuredRefs.current.set(index, el);
                }}
                style={{ ...style, overflow: 'visible' }}
                key={`message-${msg.id || actualIndex}`}
                className={`message ${msg.role || ''}${msg.muted ? ' muted' : ''}${needsResponse ? ' needs-response' : ''}`}
            >
                {msg.role === 'system' && msg.modelChange ? (
                    <ModelChangeNotification
                        previousModel={msg.modelChange.from}
                        changeKey={msg.modelChange.changeKey}
                        newModel={msg.modelChange.to}
                    />
                ) : (
                    msg.content ? (
                        <>
                            {msg.role === 'human' && (
                                <div style={{ display: editingMessageIndex === actualIndex ? 'none' : 'flex', justifyContent: 'space-between' }}>
                                    <div className="message-sender">You:</div>
                                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginRight: '8px' }}>
                                        {renderMuteButton(actualIndex)}
                                        {renderResubmitButton(actualIndex)}
                                        {needsResponse && renderRetryButton(actualIndex)}
                                        <EditSection index={actualIndex} isInline={true} />
                                    </div>
                                </div>
                            )}
                            
                            {msg.role === 'human' && editingMessageIndex === actualIndex ? (
                                <EditSection index={actualIndex} isInline={false} />
                            ) : msg.role === 'human' && msg.content ? (
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
                                <div className="message-content">
                                <MarkdownRenderer
                                    markdown={msg.content}
                                    enableCodeApply={enableCodeApply}
                                    onOpenShellConfig={onOpenShellConfig}
                                    isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
                                />
                            </div>
                                </>
                            ) : msg.role === 'assistant' && msg.content ? (
                                <>
                                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                        <div className="message-sender">AI:</div>
                                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginRight: '8px' }}>
                                            {renderMuteButton(actualIndex)}
                                        </div>
                                        {renderRetryButton(actualIndex)}
                                    </div>
                                    <div className="message-content">
                                    <MarkdownRenderer
                                        markdown={msg.content}
                                        enableCodeApply={enableCodeApply}
                                        onOpenShellConfig={onOpenShellConfig}
                                        isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
                                    />
                                </div>
                                </>
                            ) : null}
                        </>
                    ) : null
                )}
            </div>
        );
    }, [currentMessages, isTopToBottom, isCurrentlyStreaming, hasStreamedContent, editingMessageIndex, 
        renderMuteButton, renderResubmitButton, renderRetryButton, enableCodeApply, isStreaming,
        streamingConversations, currentConversationId, setItemHeight]);
    
    const displayMessages = shouldVirtualize ? null : (isTopToBottom ? currentMessages : [...currentMessages].reverse());

    // Keep track of rendered messages for performance monitoring
    const renderedCountRef = useRef(0);
    const renderedSystemMessagesRef = useRef<Set<string>>(new Set());
    const processedModelChangesRef = useRef<Set<string>>(new Set());

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
                                                isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
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
                                                    isStreaming={isStreaming || streamingConversations.has(currentConversationId)}
                                                />
                                            </div>
                                        </>
                                    ) : null}
                                </>
                            ) : null
                        )}
                    </div>;
                }) || (shouldVirtualize ? (
                    <div style={{ height: '100%', width: '100%' }}>
                        <List
                            ref={listRef}
                            height={window.innerHeight - 200}
                            itemCount={currentMessages.length}
                            itemSize={getItemSize}
                            estimatedItemSize={200}
                            width="100%"
                            overscanCount={5}
                        >
                            {renderMessage}
                        </List>
                    </div>
                ) : null)}
                
                {/* Fallback for when no messages to display */}
                {(!displayMessages || displayMessages.length === 0) && !shouldVirtualize && (
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

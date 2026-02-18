import React, { useEffect, useRef, useState, useCallback, useMemo, useTransition, useId, Suspense } from 'react';
import { useChatContext, ProcessingState } from '../context/ChatContext';
import { useFolderContext } from '../context/FolderContext';
import { Space, Alert, Typography } from 'antd';
import { v4 as uuidv4 } from 'uuid';
import StopStreamButton from './StopStreamButton';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';
import { useQuestionContext } from '../context/QuestionContext';
import { isDebugLoggingEnabled, debugLog } from '../utils/logUtils';
import ReasoningDisplay from './ReasoningDisplay';
import { useProject } from '../context/ProjectContext';
import { sendPayload } from '../apis/chatApi';
import { convertKeysToStrings } from '../utils/types';
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC<{}> = () => {
    const [error, setError] = useState<string | null>(null);
    const [connectionLost, setConnectionLost] = useState<boolean>(false);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [isRetrying, setIsRetrying] = useState<boolean>(false);
    const contentRef = useRef<HTMLDivElement>(null);
    const isAutoScrollingRef = useRef<boolean>(false);
    const [showThinkingIndicator, setShowThinkingIndicator] = useState<boolean>(false);
    const lastContentUpdateRef = useRef<number>(Date.now());
    const [isPendingResponse, setIsPendingResponse] = useState<boolean>(false);
    const [hasShownContent, setHasShownContent] = useState<boolean>(false);
    const lastScrollPositionRef = useRef<number>(0);
    const processedPreservedEvents = useRef<Set<string>>(new Set());
    const {
        streamedContentMap,
        addMessageToConversation,
        addStreamingConversation,
        isStreaming,
        setIsStreaming,
        currentConversationId,
        streamingConversations,
        currentMessages,
        userHasScrolled,
        isTopToBottom,
        setUserHasScrolled,
        removeStreamingConversation,
        setStreamedContentMap,
        setReasoningContentMap,
        getProcessingState,
        updateProcessingState,
        conversations
    } = useChatContext();

    const { checkedKeys } = useFolderContext();
    const { activeSkillPrompts, currentProject } = useProject();

    // Get the latest streamed content directly without memoization to avoid stale content
    const streamedContent = streamedContentMap.get(currentConversationId) ?? '';
    const streamedContentRef = useRef<string>(streamedContent);
    const currentConversationRef = useRef<string>(currentConversationId);
    const thinkingTimeoutRef = useRef<NodeJS.Timeout>();

    // Get processing state for current conversation
    const processingState = getProcessingState(currentConversationId);

    // Track if we have any streamed content to show
    const hasStreamedContent = streamedContentMap.has(currentConversationId) &&
        streamedContentMap.get(currentConversationId) !== '';

    // Enhanced thinking indicator logic
    useEffect(() => {
        const isCurrentlyStreaming = streamingConversations.has(currentConversationId);

        if (thinkingTimeoutRef.current) {
            clearTimeout(thinkingTimeoutRef.current);
            thinkingTimeoutRef.current = undefined;
        }

        if (isCurrentlyStreaming) {
            lastContentUpdateRef.current = Date.now();
            setShowThinkingIndicator(false);

            thinkingTimeoutRef.current = setTimeout(() => {
                const timeSinceLastUpdate = Date.now() - lastContentUpdateRef.current;
                if (timeSinceLastUpdate >= 1000 && streamingConversations.has(currentConversationId)) {
                    setShowThinkingIndicator(true);
                }
            }, 1000);
        } else {
            setShowThinkingIndicator(false);
        }

        return () => {
            if (thinkingTimeoutRef.current) {
                clearTimeout(thinkingTimeoutRef.current);
            }
        };
    }, [streamedContent, currentConversationId, streamingConversations]);

    // Reset thinking indicator when content updates
    useEffect(() => {
        if (streamedContent !== streamedContentRef.current) {
            lastContentUpdateRef.current = Date.now();
            setShowThinkingIndicator(false);

            if (thinkingTimeoutRef.current) {
                clearTimeout(thinkingTimeoutRef.current);
            }

        }
        streamedContentRef.current = streamedContent;
    }, [streamedContent, isTopToBottom, userHasScrolled, streamingConversations, currentConversationId]);
    // Function to detect processing state from content
    const detectProcessingState = useCallback((content: string): ProcessingState => {
        // Check for throttling notification
        if (content.includes('⚠️ **Rate Limit Reached**') || content.includes('throttle-retry-button')) {
            return 'error'; // Mark as error state so UI shows appropriate feedback
        }

        if (content.includes('🔧 **Executing Tool**:') || content.includes('tool_display') || content.includes('tool_result')) {
            return 'awaiting_tool_response';
        }

        if (content.includes('⏳ **Throttling Delay**:')) {
            return 'tool_throttling';
        }

        if (content.includes('⚠️ **Tool Execution Limit Reached**:')) {
            return 'tool_limit_reached';
        }
        if (content.includes('Processing tool results') || content.includes('MCP Tool') || content.includes('tool_display')) {
            return 'processing_tools';
        }

        return 'idle';

        return 'idle';
    }, []);

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

            // Prevent jarring scroll jumps when streaming ends
            // Preserve current scroll position
            const container = document.querySelector('.chat-container') as HTMLElement;
            if (container) {
                const currentScrollTop = container.scrollTop;
                setTimeout(() => {
                    // Restore scroll position if it changed unexpectedly
                    if (Math.abs(container.scrollTop - currentScrollTop) > 10) {
                        container.scrollTop = currentScrollTop;
                    }
                }, 100);
            }
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
            {/* Enhanced loading states */}
            {processingState === 'awaiting_tool_response' && (
                <div style={{ color: '#faad14', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <LoadingOutlined spin />
                    <span>Executing tool...</span>
                </div>
            )}
            {processingState === 'tool_throttling' && (
                <div style={{ color: '#ff7a00', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <LoadingOutlined spin />
                    <span>Waiting for rate limit (preventing throttling)...</span>
                </div>
            )}
            <div style={{
                visibility: ['processing_tools', 'awaiting_tool_response', 'tool_throttling', 'tool_limit_reached'].includes(processingState) ||
                    (!hasStreamedContent && streamingConversations.has(currentConversationId))
                    ? 'visible' : 'hidden',
                opacity: ['processing_tools', 'awaiting_tool_response', 'tool_throttling', 'tool_limit_reached'].includes(processingState) ? 1 : 0.8,
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
                    <RobotOutlined style={{ fontSize: '20px', animation: 'pulse 2s infinite' }} />
                    <LoadingOutlined
                        spin
                        style={{
                            color: processingState === 'processing_tools' ? '#faad14' :
                                processingState === 'awaiting_tool_response' ? '#faad14' :
                                    processingState === 'tool_throttling' ? '#ff7a00' :
                                        processingState === 'tool_limit_reached' ? '#ff4d4f' :
                                            '#1890ff'
                        }}
                    />
                    <span style={{
                        color: processingState === 'processing_tools' ? '#faad14' :
                            processingState === 'awaiting_tool_response' ? '#faad14' :
                                processingState === 'tool_throttling' ? '#ff7a00' :
                                    processingState === 'tool_limit_reached' ? '#ff4d4f' :
                                        '#1890ff',
                        animation: 'fadeInOut 2s infinite',
                        verticalAlign: 'middle',
                        marginLeft: '4px',
                        display: 'inline-block'
                    }}>
                        {processingState === 'processing_tools' ? 'Processing tool results...' :
                            processingState === 'awaiting_tool_response' ? 'Executing tool command...' :
                                processingState === 'tool_throttling' ? 'Waiting to prevent rate limiting...' :
                                    processingState === 'tool_limit_reached' ? 'Tool execution limit reached' :
                                        'Processing response...'}
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
            style={{
                margin: '20px 0',
                maxWidth: '100%',
                wordBreak: 'break-word',
                overflow: 'hidden'
            }}
            closable
        />
    );

    const ConnectionLostAlert = () => (
        <Alert
            message="Connection Lost"
            description="Your internet connection was lost while receiving the response. The stream has been stopped."
            type="warning"
            showIcon
            className="connection-lost"
            style={{
                margin: '20px 0',
                maxWidth: '100%',
                wordBreak: 'break-word',
                overflow: 'hidden'
            }}
            closable
        />
    );

    // Listen for preserved content events from streaming errors
    useEffect(() => {
        const handlePreservedContent = (event: CustomEvent) => {
            // Create a unique key for this event to prevent duplicates
            const eventKey = `${event.detail.error_detail || 'unknown'}_${event.detail.conversation_id || 'unknown'}_${event.detail.preservation_timestamp || Date.now()}`;
            if (processedPreservedEvents.current.has(eventKey)) {
                console.log('Skipping duplicate preserved content event:', eventKey);
                return;
            }
            processedPreservedEvents.current.add(eventKey);

            // Clean up old events (keep only last 10)
            if (processedPreservedEvents.current.size > 10) {
                const entries = Array.from(processedPreservedEvents.current);
                processedPreservedEvents.current = new Set(entries.slice(-10));
            }

            const {
                preserved_content,
                pre_streaming_work,
                existing_streamed_content,
                processing_context,
                successful_tool_results,
                tool_execution_summary,
                error_detail
            } = event.detail;

            console.log('Received preserved content event:', {
                eventType: 'preservedContent',
                preservedContentLength: preserved_content?.length || 0,
                successfulTools: successful_tool_results?.length || 0,
                preStreamingWork: pre_streaming_work?.length || 0,
                existingStreamedContent: existing_streamed_content?.length || 0,
                processingContext: processing_context,
                executionSummary: tool_execution_summary
            });

            // Debug: log the full event detail
            console.log('Full preserved content event detail:', event.detail);
            // Only create preserved message if we have content or successful tools
            if (preserved_content || existing_streamed_content || (successful_tool_results && successful_tool_results.length > 0) || (pre_streaming_work && pre_streaming_work.length > 0)) {
                let preservedContent = preserved_content || '';
                // Use the existing streamed content from the error data, or fall back to what's in the map
                const actualExistingContent = existing_streamed_content || streamedContentMap.get(currentConversationId) || '';

                if (actualExistingContent && actualExistingContent.trim()) {
                    // If we have existing streamed content, preserve it at the top
                    preservedContent = actualExistingContent + '\n\n---\n\n**⚠️ Response was interrupted by an error, but content above was successfully generated.**\n\n' + preservedContent;
                    console.log('Preserving existing streamed content:', actualExistingContent.length, 'characters');
                }

                // Add pre-streaming work if available and meaningful
                if (pre_streaming_work && pre_streaming_work.length > 0) {
                    // Filter out generic steps, keep only meaningful ones  
                    const meaningfulWork = pre_streaming_work.filter(work =>
                        work.includes('💾 Cache') ||
                        work.includes('📝 Prepared') ||
                        work.includes('✅ Validated') ||
                        work.includes('tokens')
                    );

                    if (meaningfulWork.length > 0) {
                        const workSection = '\n\n---\n**🔄 Processing Completed Before Error:**\n\n' +
                            meaningfulWork.map((work, index) => `• ${work}`).join('\n');
                        preservedContent += workSection;
                    } else if (processing_context?.cache_benefit) {
                        // If no meaningful work but we have cache info, show that
                        preservedContent += `\n\n---\n**💾 Cache Status:** ${processing_context.cache_benefit}`;
                    }
                }

                // If we have successful tool results, format them nicely
                if (successful_tool_results && successful_tool_results.length > 0) {
                    const toolResultsSection = '\n\n---\n**✅ Successful Tool Executions Before Error:**\n\n' +
                        successful_tool_results.map((result, index) => {
                            const content = typeof result === 'string' ? result : (result.content || JSON.stringify(result));
                            return `**Tool ${index + 1}:**\n${content}`;
                        }).join('\n\n');
                    preservedContent += toolResultsSection;
                }

                // Add error context
                const actualError = error_detail || 'Too many requests to AWS Bedrock. Please wait a moment before trying again.';
                const errorContext = `\n\n---\n**❌ Error Occurred:** ${actualError}\n` +
                    (tool_execution_summary ?
                        `**📊 Execution Summary:** ${tool_execution_summary.successful_executions}/${tool_execution_summary.total_attempts} tools completed successfully`
                        : '');
                preservedContent += errorContext;

                // Add retry button handler
                const handleContinue = async () => {
                    if (isRetrying) return;

                    setIsRetrying(true);

                    try {
                        // Get current conversation messages
                        const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];

                        // Send a continuation prompt
                        await sendPayload(
                            messages,
                            "Please continue your previous response.",
                            convertKeysToStrings(checkedKeys),
                            currentConversationId,
                            activeSkillPrompts || undefined,
                            undefined, // images
                            streamedContentMap,
                            setStreamedContentMap,
                            setIsStreaming,
                            removeStreamingConversation,
                            addMessageToConversation,
                            streamingConversations.has(currentConversationId),
                            (state) => updateProcessingState(currentConversationId, state),
                            setReasoningContentMap,
                            undefined, // throttlingRecoveryDataRef
                            currentProject
            );
                    } catch (error) {
                        console.error('Continue failed:', error);
                    } finally {
                        setIsRetrying(false);
                    }
                };

                // Add retry button HTML to preserved content
preservedContent += '\n\n<div style="margin-top: 16px;"><button class="continue-button" data-continue-handler="true">↗️ Continue Response</button></div>';

console.log('Creating preserved message with content length:', preservedContent.length);
console.log('First 200 chars:', preservedContent.substring(0, 200));
console.log('Contains existing streamed content:', !!existing_streamed_content);

const preservedMessage = {
    id: uuidv4(),
    role: 'assistant' as const,
    content: preservedContent,
    _timestamp: Date.now(),
    preservedContent: {
        successful_tools: successful_tool_results || [],
        pre_streaming_work: pre_streaming_work || [],
        processing_context: processing_context || {},
        execution_summary: tool_execution_summary,
        error_detail: actualError,
        was_preserved: true
    }
};

addMessageToConversation(preservedMessage, currentConversationId);
console.log('Added preserved message with successful tool results');

// Attach click handler to the continue button after React renders it
setTimeout(() => {
    const continueButton = document.querySelector('[data-continue-handler="true"]') as HTMLButtonElement;
    if (continueButton && !continueButton.dataset.handlerAttached) {
        continueButton.dataset.handlerAttached = 'true';
        continueButton.addEventListener('click', handleContinue);

        // Style the button
        continueButton.style.padding = '6px 15px';
        continueButton.style.fontSize = '14px';
        continueButton.style.fontWeight = '500';
        continueButton.style.borderRadius = '6px';
        continueButton.style.border = '1px solid #1890ff';
        continueButton.style.backgroundColor = '#f0f8ff';
        continueButton.style.color = '#1890ff';
        continueButton.style.cursor = 'pointer';
    }
}, 100);

// Now remove the streaming conversation since we've preserved the content
removeStreamingConversation(currentConversationId);
                }
            };

document.addEventListener('preservedContent', handlePreservedContent as EventListener);

// Handle authentication error retry events
const handleRetryAuthError = async (event: CustomEvent) => {
    console.log('🔄 RETRY_AUTH: Handler invoked with detail:', event.detail);
    const { conversationId: retryConversationId } = event.detail;

    if (retryConversationId !== currentConversationId) {
        console.log('Retry auth error for different conversation, ignoring');
        return;
    }

    console.log('Retrying request after auth error for conversation:', retryConversationId);

    try {
        // Get the last human message to resend
        const lastHumanMessage = currentMessages
            .filter(msg => msg.role === 'human' && !msg.muted)
            .pop();

        if (!lastHumanMessage) {
            console.error('No human message found to retry');
            return;
        }

        // Clear the error message and retry
        const messagesToSend = currentMessages.filter(msg => !msg.muted);

        // Mark the conversation as streaming so the UI reflects the retry
        addStreamingConversation(retryConversationId);

        await sendPayload(
            messagesToSend,
            lastHumanMessage.content,
            convertKeysToStrings(checkedKeys),
            currentConversationId,
            activeSkillPrompts || undefined,
            undefined, // images - not re-sending images on retry
            streamedContentMap,
            setStreamedContentMap,
            setIsStreaming,
            removeStreamingConversation,
            addMessageToConversation,
            true,
            (state) => updateProcessingState(currentConversationId, state),
            setReasoningContentMap,
            undefined, // throttlingRecoveryDataRef
            currentProject
        );
    } catch (error) {
        console.error('Retry after auth error failed:', error);
    }
};

window.addEventListener('retryAuthError', handleRetryAuthError as EventListener);

return () => {
    document.removeEventListener('preservedContent', handlePreservedContent as EventListener);
    window.removeEventListener('retryAuthError', handleRetryAuthError as EventListener);
};
        }, [
            currentConversationId,
            addMessageToConversation,
            addStreamingConversation,
            currentMessages,
            checkedKeys,
            activeSkillPrompts,
            streamedContentMap,
            setStreamedContentMap,
            setIsStreaming,
            removeStreamingConversation,
            updateProcessingState,
            setReasoningContentMap,
            currentProject
        ]);

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

// Add effect to handle conversation switches
useEffect(() => {
    // Remove scroll event dispatching that was causing layout changes
}, [currentConversationId, streamedContentMap]);

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

        {hasStreamedContent && (
            <div className="message assistant">
                {connectionLost && (
                    <ConnectionLostAlert />
                )}
                {streamedContent && streamedContent.trim() && (
                    <div className="message-sender" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span>AI:</span>
                        {/* Only show stop button here once we have content */}
                        {streamingConversations.has(currentConversationId) && (
                            <StopStreamButton
                                conversationId={currentConversationId}
                                onStop={stopStreaming}
                                style={{ marginLeft: 'auto' }}
                            />
                        )}
                    </div>
                )}
                <Suspense fallback={<div>Loading content...</div>}>
                    <>
                        {/* Show reasoning content for OpenAI models */}
                        <ReasoningDisplay conversationId={currentConversationId} />

                        {/* Only render if we have actual content */}
                        {error && <div><ErrorDisplay message={error} /><br /></div>}
                        {!error && streamedContent && streamedContent.trim() && (
                            <div className="message-content">
                                {/* Show preservation notice if this content was preserved */}
                                {streamedContent.includes('Successful Tool Executions Before Error:') && (
                                    <Alert
                                        message="⚠️ Partial Response Preserved"
                                        description="Some tool executions completed successfully before an error occurred. Results are shown below."
                                        type="warning"
                                        showIcon
                                        style={{ marginBottom: '16px' }}
                                    />
                                )}
                                <MarkdownRenderer
                                    key={`stream-${currentConversationId}`}
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

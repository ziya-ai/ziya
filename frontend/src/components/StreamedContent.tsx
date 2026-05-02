import React, { useEffect, useRef, useState, useCallback, useMemo, useTransition, useId, Suspense } from 'react';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import { useScrollContext } from '../context/ScrollContext';
import type { ProcessingState } from '../context/ChatContext';
import { Space, Alert, Typography, Button } from 'antd';
import { v4 as uuidv4 } from 'uuid';
import StopStreamButton from './StopStreamButton';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';
import { useQuestionContext } from '../context/QuestionContext';
import { isDebugLoggingEnabled, debugLog } from '../utils/logUtils';
import { useSendPayload } from '../hooks/useSendPayload';
import SwarmRecoveryPanel from './SwarmRecoveryPanel';
import SwarmFlowGraph from './SwarmFlowGraph';
import type { SwarmNode } from './SwarmFlowGraph';
import ReasoningDisplay from './ReasoningDisplay';
import { lazyWithRetry } from '../utils/lazyWithRetry';
const MarkdownRenderer = lazyWithRetry(() => import("./MarkdownRenderer"));

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
    const [showPreservedContinue, setShowPreservedContinue] = useState(false);
    const {
        streamedContentMap,
        currentConversationId,
        setStreamedContentMap,
        removeStreamingConversation,
        addMessageToConversation,
        addStreamingConversation,
        streamingConversations,
        isStreaming,
        setIsStreaming,
        updateProcessingState,
        currentMessages,
    } = useActiveChat();
    const {
        folders,
        conversations,
        setConversations,
    } = useConversationList();
    const {
        userHasScrolled,
        isTopToBottom,
        setUserHasScrolled,
    } = useScrollContext();

    const { getProcessingState, setReasoningContentMap } = useActiveChat();
    const { send } = useSendPayload();

    // Get the latest streamed content directly without memoization to avoid stale content
    const streamedContent = streamedContentMap.get(currentConversationId) ?? '';
    const streamedContentRef = useRef<string>(streamedContent);
    const streamedContentMapRef = useRef(streamedContentMap);
    const currentConversationRef = useRef<string>(currentConversationId);
    const thinkingTimeoutRef = useRef<NodeJS.Timeout>();

    // Get processing state for current conversation
    const processingState = getProcessingState(currentConversationId);

    // Ref to conversations — allows activeSwarmInfo to read current data
    // without depending on the array reference (which changes on every
    // setConversations call: polling, read-marking, background fetches).
    const conversationsRef = useRef(conversations);
    conversationsRef.current = conversations;

    // Stable key that changes ONLY when a delegate's status changes.
    // This decouples swarm display from unrelated conversation mutations
    // (message changes, read status, timestamps, etc.)
    const delegateStatusKey = useMemo(() => {
        return conversations
            .filter(c => c.delegateMeta)
            .map(c => `${c.id}:${(c.delegateMeta as any).status}`)
            .join(',');
    }, [conversations]);

    // Detect active swarms spawned from the current conversation
    const activeSwarmInfo = useMemo(() => {
        if (!folders || !currentConversationId) return null;
        if (folders.length === 0) return null;
        const TERMINAL = new Set(['completed', 'completed_partial', 'cancelled']);
        for (const folder of folders) {
            const tp = folder.taskPlan;
            if (!tp) continue;
            if (tp.source_conversation_id !== currentConversationId) continue;
            if (TERMINAL.has(tp.status)) continue;
            // Found an active swarm originating from this conversation
            const total = tp.delegate_specs?.length ?? 0;
            const crystalCount = (tp.crystals?.length) ?? 0;
            // Derive running count from delegate_specs statuses if available
            // (useDelegatePolling patches delegateMeta on conversations, but
            // folder.taskPlan.delegate_specs are the canonical list)
            let runningCount = 0;
            if (tp.delegate_specs) {
                // Count delegates that are running based on conversation delegateMeta
                // (set by useDelegatePolling).  Delegate conversations stream
                // server-side and are never in the frontend streamingConversations set.
                for (const spec of tp.delegate_specs) {
                    const convId = (spec as any).conversation_id;
                    if (convId) {
                        const conv = conversationsRef.current.find(c => c.id === convId);
                        const status = (conv?.delegateMeta as any)?.status;
                        if (status === 'running' || status === 'compacting') {
                            runningCount++;
                        }
                    }
                }
            }
            return {
                name: tp.name,
                total,
                delegateSpecs: tp.delegate_specs || [],
                folderId: folder.id,
                crystalCount,
                runningCount,
                status: tp.status,
            };
        }
        return null;
    }, [folders, currentConversationId, delegateStatusKey]);

    // Memoize the delegates list for SwarmRecoveryPanel so it doesn't get
    // a fresh array reference on every unrelated conversations change.
    const swarmDelegates = useMemo(() => {
        if (!activeSwarmInfo) return [];
        const convDelegates = conversationsRef.current
            .filter(c => c.folderId === activeSwarmInfo.folderId && (c.delegateMeta as any)?.role === 'delegate')
            .map(c => ({
                id: (c.delegateMeta as any)!.delegate_id || c.id,
                name: c.title.replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]\s*/u, ''),
                emoji: (() => {
                    const match = c.title.match(/^([\p{Emoji_Presentation}\p{Extended_Pictographic}])/u);
                    return match ? match[1] : '🔵';
                })(),
                status: (c.delegateMeta as any)!.status,
                hasCrystal: (c.delegateMeta as any)!.status === 'crystal',
            }));
        // Build SwarmNode[] with dependency info from delegate_specs
        const specMap = new Map(
            (activeSwarmInfo.delegateSpecs || []).map((s: any) => [s.delegate_id, s])
        );
        return convDelegates.map(d => ({
            ...d,
            dependencies: (specMap.get(d.id) as any)?.dependencies || [],
        }));
    }, [activeSwarmInfo, delegateStatusKey]);

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
    }, []);

    // Track if we're waiting for a response in this conversation
    useEffect(() => {
        const isWaitingForResponse = streamingConversations.has(currentConversationId);
        setIsPendingResponse(isWaitingForResponse);
        if (isWaitingForResponse) setShowPreservedContinue(false);

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
            setShowPreservedContinue(false);
        }
    }, [currentConversationId]);

    // Track when we've shown content for this conversation
    useEffect(() => {
        if (hasStreamedContent && !hasShownContent) {
            setHasShownContent(true);
        }
    }, [hasStreamedContent, hasShownContent]);


    // Keep map ref current without triggering effect re-runs
    useEffect(() => {
        streamedContentMapRef.current = streamedContentMap;
    }, [streamedContentMap]);

    // Update the ref whenever streamed content changes
    useEffect(() => {
        streamedContentRef.current = streamedContent;
    }, [streamedContent]);

    // Add direct method to stop streaming
    const stopStreaming = useCallback(() => {
        if (streamingConversations.has(currentConversationId)) {
            // Capture content before any cleanup that might clear the map
            const contentToPreserve = streamedContentMapRef.current.get(currentConversationId) || '';
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

            // Save accumulated content as a conversation message BEFORE removing
            // streaming state.  removeStreamingConversation deletes the entry from
            // streamedContentMap, which unmounts the streamed-content div and causes
            // a scroll jump.  Persisting first keeps the content in the DOM via
            // Conversation's message list.
            if (contentToPreserve.trim()) {
                addMessageToConversation(
                    { role: 'assistant', content: contentToPreserve, _timestamp: Date.now() },
                    currentConversationId
                );
            }

            removeStreamingConversation(currentConversationId);
            setIsStreaming(false);

            // Handle scroll position when streaming ends:
            // - If user was at the active end, scroll to the new end after DOM settles
            // - If user was scrolled away, restore their position if it shifted
            const container = document.querySelector('.chat-container') as HTMLElement;
            if (container) {
                const scrollHeight = container.scrollHeight;
                const clientHeight = container.clientHeight;
                const currentScrollTop = container.scrollTop;
                const wasAtEnd = isTopToBottom
                    ? (scrollHeight - currentScrollTop - clientHeight) < 50
                    : currentScrollTop < 50;

                setTimeout(() => {
                    if (wasAtEnd) {
                        // User was following — keep them at the end
                        container.scrollTop = isTopToBottom
                            ? container.scrollHeight - container.clientHeight
                            : 0;
                    } else if (Math.abs(container.scrollTop - currentScrollTop) > 10) {
                        container.scrollTop = currentScrollTop;
                    }
                }, 100);
            }
        }
    }, [currentConversationId, removeStreamingConversation, setIsStreaming, streamingConversations, addMessageToConversation]);

    // "Continue Response" handler for preserved-content error recovery.
    // Top-level useCallback avoids the stale-closure problem that the
    // previous in-effect definition had; conversationsRef ensures we
    // always read current message history at invocation time.
    const handlePreservedContinue = useCallback(async () => {
        if (isRetrying) return;
        setIsRetrying(true);
        setShowPreservedContinue(false);
        try {
            const messages = conversationsRef.current
                .find(c => c.id === currentConversationId)?.messages || [];
            addStreamingConversation(currentConversationId);
            await send({
                messages,
                question: "Please continue your previous response.",
                includeReasoning: true,
            });
        } catch (error) {
            console.error('Continue failed:', error);
        } finally {
            setIsRetrying(false);
        }
    }, [currentConversationId, isRetrying, addStreamingConversation, send]);

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
        <Space direction="vertical" style={{ width: '100%' }}>
            <div style={{
                visibility: ['processing_tools', 'awaiting_tool_response', 'tool_throttling', 'tool_limit_reached', 'model_thinking', 'awaiting_model_response'].includes(processingState) ||
                    (!hasStreamedContent && streamingConversations.has(currentConversationId)) ||

                    (streamingConversations.has(currentConversationId) && processingState !== 'idle')
                    ? 'visible' : 'hidden',
                opacity: ['processing_tools', 'awaiting_tool_response', 'tool_throttling', 'tool_limit_reached', 'model_thinking', 'awaiting_model_response'].includes(processingState) ? 1 : 0.8,
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
                            color: processingState === 'model_thinking' ? '#722ed1' :
                                processingState === 'processing_tools' ? '#faad14' :
                                    processingState === 'awaiting_tool_response' ? '#faad14' :
                                        processingState === 'tool_throttling' ? '#ff7a00' :
                                            processingState === 'tool_limit_reached' ? '#ff4d4f' :
                                                processingState === 'awaiting_model_response' ? '#1890ff' :
                                                    '#1890ff'
                        }}
                    />
                    <span style={{
                        color: processingState === 'processing_tools' ? '#faad14' :
                            processingState === 'awaiting_tool_response' ? '#faad14' :
                                processingState === 'tool_throttling' ? '#ff7a00' :
                                    processingState === 'tool_limit_reached' ? '#ff4d4f' :
                                        processingState === 'model_thinking' ? '#722ed1' :
                                            processingState === 'awaiting_model_response' ? '#1890ff' :
                                                '#1890ff',
                        animation: 'fadeInOut 2s infinite',
                        verticalAlign: 'middle',
                        marginLeft: '4px',
                        display: 'inline-block'
                    }}>
                        {processingState === 'model_thinking' ? '🧠 Deep thinking…' :
                            processingState === 'awaiting_model_response' ? '⏳ Waiting for model response…' :
                                processingState === 'processing_tools' ? 'Running tools…' :
                                    processingState === 'awaiting_tool_response' ? 'Executing tool…' :
                                        processingState === 'tool_throttling' ? 'Waiting to prevent rate limiting…' :
                                            processingState === 'tool_limit_reached' ? 'Tool execution limit reached' :
                                                processingState === 'sending' ? 'Sending request…' :
                                                    'Processing request…'}
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
            const eventKey = `${event.detail.error_detail || 'unknown'}_${event.detail.conversation_id || 'unknown'}`;
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
                const actualExistingContent = existing_streamed_content || streamedContentMapRef.current.get(currentConversationId) || '';

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

                // Signal React to render a proper continue button component
                setShowPreservedContinue(true);

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

            // Remove the error message (and the retry button it contains) from
            // the conversation so the user doesn't see a stale error block once
            // the retry succeeds.  The error message is identifiable by the
            // auth-error-retry-button class embedded in its HTML content.
            setConversations(prev => prev.map(conv => {
                if (conv.id !== retryConversationId) return conv;
                // Walk backwards to find and remove the error message
                const msgs = [...conv.messages];
                for (let i = msgs.length - 1; i >= 0; i--) {
                    if (msgs[i].role === 'assistant' &&
                        typeof msgs[i].content === 'string' &&
                        msgs[i].content.includes('auth-error-retry-button')) {
                        msgs.splice(i, 1);
                        break;
                    }
                }
                return { ...conv, messages: msgs, _version: Date.now() };
            }));

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
                // Filter out the error message as well — currentMessages may
                // not yet reflect the state update above due to React batching.
                const messagesToSend = currentMessages
                    .filter(msg => !msg.muted && !(msg.role === 'assistant' && typeof msg.content === 'string' && msg.content.includes('auth-error-retry-button')));

                // Mark the conversation as streaming so the UI reflects the retry
                addStreamingConversation(retryConversationId);

                await send({
                    messages: messagesToSend,
                    question: lastHumanMessage.content,
                    isStreamingToCurrentConversation: true,
                    includeReasoning: true,
                });
            } catch (error) {
                console.error('Retry after auth error failed:', error);
            }
        };

        window.addEventListener('retryAuthError', handleRetryAuthError as EventListener);

        // Handle context-error retry events.  Mirror of handleRetryAuthError:
        // the user has (hopefully) reduced context or switched models, now resend
        // the last human message and strip the error banner.
        const handleRetryContextError = async (event: CustomEvent) => {
            console.log('🔄 RETRY_CONTEXT: Handler invoked with detail:', event.detail);
            const { conversationId: retryConversationId } = event.detail;

            if (retryConversationId !== currentConversationId) {
                console.log('Retry context error for different conversation, ignoring');
                return;
            }

            console.log('Retrying request after context error for conversation:', retryConversationId);

            // Remove the error banner message so the user doesn't see a stale
            // error block once the retry succeeds.
            setConversations(prev => prev.map(conv => {
                if (conv.id !== retryConversationId) return conv;
                const msgs = [...conv.messages];
                for (let i = msgs.length - 1; i >= 0; i--) {
                    if (msgs[i].role === 'assistant' &&
                        typeof msgs[i].content === 'string' &&
                        msgs[i].content.includes('context-error-retry-button')) {
                        msgs.splice(i, 1);
                        break;
                    }
                }
                return { ...conv, messages: msgs, _version: Date.now() };
            }));

            try {
                const lastHumanMessage = currentMessages
                    .filter(msg => msg.role === 'human' && !msg.muted)
                    .pop();

                if (!lastHumanMessage) {
                    console.error('No human message found to retry');
                    return;
                }

                // Filter out the error message — currentMessages may not yet
                // reflect the state update above due to React batching.
                const messagesToSend = currentMessages
                    .filter(msg => !msg.muted && !(msg.role === 'assistant' && typeof msg.content === 'string' && msg.content.includes('context-error-retry-button')));

                addStreamingConversation(retryConversationId);

                await send({
                    messages: messagesToSend,
                    question: lastHumanMessage.content,
                    isStreamingToCurrentConversation: true,
                    includeReasoning: true,
                });
            } catch (error) {
                console.error('Retry after context error failed:', error);
            }
        };

        window.addEventListener('retryContextError', handleRetryContextError as EventListener);

        return () => {
            document.removeEventListener('preservedContent', handlePreservedContent as EventListener);
            window.removeEventListener('retryAuthError', handleRetryAuthError as EventListener);
            window.removeEventListener('retryContextError', handleRetryContextError as EventListener);
        };
    }, [
        currentConversationId,
        addMessageToConversation,
        removeStreamingConversation,
        addStreamingConversation,
        currentMessages,
        send,
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
    }, [isTopToBottom, isStreaming, streamingConversations, currentConversationId, removeStreamingConversation, setIsStreaming]);

    // Update loading state based on streaming status
    useEffect(() => {
        if (!isStreaming) {
            setIsLoading(false);
        }
    }, [isStreaming]);

    const enableCodeApply = window.enableCodeApply === 'true';
    const isRawMode = React.useMemo(() => {
        const conv = conversations.find((c: any) => c.id === currentConversationId);
        return conv?.displayMode === 'raw';
    }, [conversations, currentConversationId]);
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
                                    {isRawMode ? (
                                        <pre className="raw-markdown-view">{streamedContent}</pre>
                                    ) : (
                                        <MarkdownRenderer
                                            key={`stream-${currentConversationId}`}
                                            markdown={streamedContent}
                                            forceRender={streamingConversations.has(currentConversationId)}
                                            isStreaming={streamingConversations.has(currentConversationId)}
                                            enableCodeApply={enableCodeApply}
                                        />
                                    )}
                                </div>
                            )}

                        </>
                    </Suspense>
                </div>
            )}
            {/* Loading indicator - shown during active processing states
            Visible in two scenarios:
            1. Before first content arrives (initial loading)
            2. AFTER content exists, when in an active processing state
               (tool execution, model thinking, waiting for response)
            This ensures users always see activity feedback, not just
            a static stop sign during long tool chains. */}
            {streamingConversations.has(currentConversationId) &&
                !error && (isLoading || isPendingResponse) &&
                (
                    // Scenario 1: No content yet (initial loading)
                    (!streamedContentMap.has(currentConversationId) ||
                        streamedContentMap.get(currentConversationId) === '') ||
                    // Scenario 2: Content exists but we're in an active processing state
                    (hasStreamedContent && processingState !== 'idle' && processingState !== 'sending')
                ) && (
                    <LoadingIndicator />
                )}
            {/* Continue button after preserved-content error recovery.
                Rendered as a proper React component instead of imperative DOM
                manipulation so onClick is never stale and cleanup is automatic. */}
            {showPreservedContinue && !streamingConversations.has(currentConversationId) && (
                <div style={{ margin: '12px 20px', textAlign: 'center' }}>
                    <Button
                        onClick={handlePreservedContinue}
                        loading={isRetrying}
                        type="default"
                        style={{ borderColor: '#1890ff', backgroundColor: '#f0f8ff', color: '#1890ff' }}
                    >
                        ↗️ Continue Response
                    </Button>
                </div>
            )}
            {/* Active swarm indicator — shown when this conversation spawned delegates */}
            {activeSwarmInfo && (
                <><SwarmFlowGraph
                    nodes={swarmDelegates as SwarmNode[]}
                    planName={activeSwarmInfo.name}
                />
                    {/* Compact recovery controls when swarm has failures */}
                    {(activeSwarmInfo.status === 'completed_partial' || activeSwarmInfo.status === 'running') && (
                        <div style={{ margin: '0 20px 12px', paddingLeft: 46 }}>
                            <SwarmRecoveryPanel
                                compact
                                groupId={activeSwarmInfo.folderId}
                                planStatus={activeSwarmInfo.status}
                                planName={activeSwarmInfo.name}
                                delegates={swarmDelegates}
                                onActionComplete={() => {
                                    // Polling will pick up the change within 3 seconds
                                }}
                            />
                        </div>
                    )}
                </>)}
        </div>
    );
};

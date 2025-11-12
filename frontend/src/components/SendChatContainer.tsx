import React, { useEffect, useRef, memo, useState, useCallback, useMemo, useLayoutEffect } from "react";
import { useChatContext } from '../context/ChatContext';
import { detectIncompleteResponse } from '../utils/responseUtils';
import { sendPayload } from "../apis/chatApi";
import { Message } from "../utils/types";
import { convertKeysToStrings } from "../utils/types";
import { useFolderContext } from "../context/FolderContext";
import { Button, Input, message } from 'antd';
import { SendOutlined, StopOutlined } from "@ant-design/icons";
import StopStreamButton from './StopStreamButton';
import { useQuestionContext } from '../context/QuestionContext';
import { ThrottlingErrorDisplay } from './ThrottlingErrorDisplay';
import { useTheme } from '../context/ThemeContext';

const { TextArea } = Input;

const isQuestionEmpty = (input: string) => input.trim().length === 0;

interface SendChatContainerProps {
    fixed?: boolean;
    empty?: boolean;
}

interface FeedbackReadyEvent {
    toolId: string;
    toolName: string;
    conversationId: string;
}

export const SendChatContainer: React.FC<SendChatContainerProps> = memo(({ fixed = false, empty = false }) => {
    const [showContinueButton, setShowContinueButton] = useState(false);
    const { isDarkMode } = useTheme();
    const {
        addMessageToConversation,
        setIsStreaming,
        streamedContentMap,
        setStreamedContentMap,
        setReasoningContentMap,
        currentMessages,
        currentConversationId,
        streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
        updateProcessingState,
        setUserHasScrolled,
        getProcessingState
    } = useChatContext();

    const { checkedKeys } = useFolderContext();
    const textareaRef = useRef<any>(null);
    const inputChangeTimeoutRef = useRef<NodeJS.Timeout>();
    const [isProcessing, setIsProcessing] = useState(false);
    const [currentToolId, setCurrentToolId] = useState<string | null>(null);
    const [currentToolName, setCurrentToolName] = useState<string | null>(null);
    const [isSendingFeedback, setIsSendingFeedback] = useState(false);
    const [throttlingError, setThrottlingError] = useState<any>(null);

    const { question, setQuestion } = useQuestionContext();

    // Track if we've received any content yet to distinguish "Sending" vs "Processing"
    const [hasReceivedContent, setHasReceivedContent] = useState(false);

    // Reset content tracking when streaming starts
    useEffect(() => {
        if (streamingConversations.has(currentConversationId)) {
            setHasReceivedContent(false);
        }
    }, [streamingConversations, currentConversationId]);

    // Monitor streamed content to detect when first content arrives
    useEffect(() => {
        const currentStreamedContent = streamedContentMap.get(currentConversationId);
        if (currentStreamedContent && currentStreamedContent.trim().length > 0 && !hasReceivedContent) {
            setHasReceivedContent(true);
        }
    }, [streamedContentMap, currentConversationId, hasReceivedContent]);

    const isCurrentlyStreaming = streamingConversations.has(currentConversationId);

    // Check if the last message suggests continuation is needed
    useEffect(() => {
        const lastMessage = currentMessages[currentMessages.length - 1];
        if (lastMessage?.role === 'assistant' && lastMessage.content) {
            const isIncomplete = detectIncompleteResponse(lastMessage.content);
            setShowContinueButton(isIncomplete && !streamingConversations.has(currentConversationId));
        }
    }, [currentMessages, streamingConversations, currentConversationId]);

    // Listen for tool feedback ready events
    useEffect(() => {
        const handleFeedbackReady = (event: CustomEvent<FeedbackReadyEvent>) => {
            const { toolId, toolName, conversationId: eventConversationId } = event.detail;

            // Only handle feedback events for the current conversation
            if (eventConversationId === currentConversationId) {
                setCurrentToolId(toolId);
                setCurrentToolName(toolName);

                // Focus the text area when a new tool becomes active
                setTimeout(() => {
                    textareaRef.current?.focus();
                }, 100);
            }
        };

        document.addEventListener('feedbackReady', handleFeedbackReady as EventListener);

        return () => {
            document.removeEventListener('feedbackReady', handleFeedbackReady as EventListener);
        };
    }, [currentConversationId]);

    // Clear tool state when streaming ends
    useEffect(() => {
        if (!isCurrentlyStreaming) {
            setCurrentToolId(null);
            setCurrentToolName(null);
        }
    }, [isCurrentlyStreaming]);

    // Focus management
    useLayoutEffect(() => {
        if (question === '' && textareaRef.current) {
            textareaRef.current.focus();
        }
    }, [question]);

    // Optimized input handler with debouncing for performance monitoring
    const handleQuestionChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const inputStart = performance.now();
        const newValue = e.target.value;

        // Update immediately for responsive UI
        setQuestion(newValue);

        // Clear any existing timeout for debounced operations
        if (inputChangeTimeoutRef.current) {
            clearTimeout(inputChangeTimeoutRef.current);
        }

        // Debounce expensive operations (like token counting)
        inputChangeTimeoutRef.current = setTimeout(() => {
            // Any expensive operations that don't need to happen on every keystroke
            // can be moved here
        }, 300);

        // Monitor input performance
        const inputTime = performance.now() - inputStart;
        if (inputTime > 5) {
            console.warn(`🐌 Input change slow: ${inputTime.toFixed(2)}ms for ${newValue.length} chars`);
        }
    }, [setQuestion]);

    // Cleanup timeout on unmount
    useEffect(() => {
        return () => {
            if (inputChangeTimeoutRef.current) {
                clearTimeout(inputChangeTimeoutRef.current);
            }
        };
    }, []);

    // Clear error states when conversation changes
    useEffect(() => {
        setThrottlingError(null);
    }, [currentConversationId]);

    // Clear error states when successfully starting to send
    useEffect(() => {
        if (streamingConversations.has(currentConversationId)) {
            setThrottlingError(null);
        }
    }, [streamingConversations, currentConversationId]);

    // Listen for throttling errors from chatApi
    useEffect(() => {
        const handleThrottlingError = (event: CustomEvent) => {
            // Only handle errors for the current conversation
            if (event.detail.conversation_id && event.detail.conversation_id !== currentConversationId) {
                return;
            }
            console.log('Throttling error received:', event.detail);
            setThrottlingError(event.detail);
        };

        document.addEventListener('throttlingError', handleThrottlingError as EventListener);
        return () => {
            document.removeEventListener('throttlingError', handleThrottlingError as EventListener);
        };
    }, [currentConversationId]); // Keep dependency to recreate listener with current conversation ID

    const isDisabled = useMemo(() =>
        isQuestionEmpty(question) || streamingConversations.has(currentConversationId),
        [question, streamingConversations, currentConversationId]
    );

    // Allow textarea input during streaming for real-time feedback
    const isTextAreaDisabled = useMemo(() =>
        false, // Never disable textarea - allow typing during streaming
        [question, streamingConversations, currentConversationId]
    );

    // Allow feedback anytime during streaming (tools are running)
    const shouldSendAsFeedback = isCurrentlyStreaming && question.trim().length > 0;

    const sendToolFeedback = async () => {
        if (!question.trim() || isSendingFeedback) return;

        const feedbackText = question.trim();

        setIsSendingFeedback(true);

        try {
            // Use the global WebSocket if available
            const feedbackWebSocket = (window as any).feedbackWebSocket;
            if (feedbackWebSocket && (window as any).feedbackWebSocketReady) {
                // Use currentToolId if available, otherwise use a generic identifier
                const toolId = currentToolId || 'streaming_tool';
                feedbackWebSocket.sendFeedback(toolId, feedbackText);
                console.log('🔄 FEEDBACK:', feedbackText);

                // Clear the input after sending feedback
                setQuestion('');

                // Show confirmation that feedback was sent
                message.success({
                    content: 'Feedback sent to running tools',
                    duration: 2,
                    key: 'feedback-sent'
                });
            } else {
                console.error('🔄 FEEDBACK: WebSocket not ready or not available');
                // Show warning that feedback couldn't be sent
                message.warning({
                    content: 'Feedback system unavailable - tools will continue without feedback',
                    duration: 3,
                    key: 'feedback-unavailable'
                });
            }
        } catch (error) {
            console.error('🔄 FEEDBACK: Error sending feedback:', error);
            message.error('Failed to send feedback');
        } finally {
            setIsSendingFeedback(false);
        }
    };

    const buttonTitle = useMemo(() =>
        isCurrentlyStreaming
            ? "Waiting for AI response..."
            : currentMessages[currentMessages.length - 1]?.role === 'human'
                ? "AI response may have failed - click Send to retry"
                : "Send message",
        [isCurrentlyStreaming, currentMessages]
    );

    const handleSendPayload = async (isRetry: boolean = false, retryContent?: string) => {

        // If we have a tool waiting for feedback and we're streaming, send as feedback instead
        if (shouldSendAsFeedback && !isRetry && !retryContent) {
            await sendToolFeedback();
            return;
        }

        // Don't allow sending regular messages while streaming (tools are running)
        if (isCurrentlyStreaming && !isRetry && !retryContent) return;

        // Don't allow sending if we're already streaming in this conversation
        if (streamingConversations.has(currentConversationId)) {
            console.warn('Attempted to send while streaming');
            return;
        }

        // Check if the last message was from a human and we're still streaming
        const lastMessage = currentMessages[currentMessages.length - 1];
        if (lastMessage?.role === 'human' && streamingConversations.has(currentConversationId)) {
            console.warn('Cannot send another human message before AI response');
            return;
        }

        // Store the question before clearing it
        const currentQuestion = retryContent || question;

        // Clear any existing error states when starting a new request
        setThrottlingError(null);

        setQuestion('');
        setStreamedContentMap(new Map());

        // Reset user scroll state when sending a new message
        setUserHasScrolled(false);

        // Debug log the selected files state
        console.log('Current file selection state:', {
            checkedKeys,
            selectedFiles: convertKeysToStrings(checkedKeys)
        });
        setIsProcessing(true);

        // Create new human message
        const newHumanMessage: Message = {
            content: currentQuestion,
            role: 'human'
        };

        // Add the human message immediately
        addMessageToConversation(newHumanMessage, currentConversationId);

        // Clear streamed content and add the human message immediately
        setStreamedContentMap(new Map());

        console.log('Added human message:', {
            id: currentConversationId,
            content: newHumanMessage.content,
            currentMessages: currentMessages.length + 1 // +1 because we just added a message
        });

        // Include the new message in messages for the API
        const baseMessages = isRetry ? currentMessages : [...currentMessages, newHumanMessage!];
        // Filter out muted messages before sending to API - explicitly exclude muted messages
        const messagesToSend = baseMessages.filter(msg => !msg.muted);

        addStreamingConversation(currentConversationId);
        const targetConversationId = currentConversationId;

        try {
            // Get latest messages after state update
            const selectedFiles = convertKeysToStrings(checkedKeys);
            const result = await sendPayload(
                messagesToSend,
                currentQuestion,
                selectedFiles,
                targetConversationId,
                streamedContentMap,
                setStreamedContentMap,
                setIsStreaming,
                removeStreamingConversation,
                addMessageToConversation,
                streamingConversations.has(currentConversationId),
                (state: 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error') => updateProcessingState(currentConversationId, state),
                setReasoningContentMap
            );
            // Check if result is an error response
            if (typeof result === 'string' && result.includes('"error":"validation_error"')) {
                try {
                    const errorData = JSON.parse(result);
                    if (errorData.error === 'validation_error') {
                        message.error({
                            content: errorData.detail,
                            duration: 5,
                            key: 'validation-error'
                        });
                        return;
                    }
                } catch (e) {
                    console.error('Error parsing error response:', e);
                }
            }
            // Get the final streamed content
            const finalContent = streamedContentMap.get(currentConversationId) || result;
            if (finalContent) {
                // Check if result is an error response
                try {
                    const errorData = JSON.parse(finalContent);
                    if (errorData.error === 'validation_error') {
                        message.error(errorData.detail || 'Selected content is too large. Please reduce the number of files.');
                        return;
                    }
                } catch (e) { } // Not JSON or not an error response

                // Message already added by sendPayload, just clean up streaming state
                removeStreamingConversation(currentConversationId);
            }
        } catch (error) {
            console.error('Error sending message:', error);
            removeStreamingConversation(currentConversationId);
            // Only show generic error if not handled by streaming error system
            if (!(error instanceof Error &&
                (error.message.includes('validation_error') ||
                    error.message.includes('credential')))) {
                message.error({
                    content: 'Failed to send message. Please try again.',
                    key: 'send-error',
                    duration: 5
                });
            }
        } finally {
            setIsProcessing(false);
            setIsStreaming(false);
            removeStreamingConversation(currentConversationId);
        }
    };

    const handleContinue = () => {
        // Reset user scroll state when continuing (same as new message)
        setUserHasScrolled(false);

        const continuePrompt = "Please continue your previous response.";
        setQuestion(continuePrompt);
        handleSendPayload(false, continuePrompt);
        setShowContinueButton(false);

        // Scroll to bottom immediately for continue action (same as new message)
        setTimeout(() => {
            const chatContainer = document.querySelector('.chat-container') as HTMLElement;
            if (chatContainer) {
                const { scrollHeight, clientHeight } = chatContainer;
                const targetScrollTop = scrollHeight - clientHeight;
                chatContainer.scrollTo({
                    top: Math.max(0, targetScrollTop),
                    behavior: 'auto'
                });
            }
        }, 50);
    };

    return (
        <div>
            {showContinueButton && (
                <div style={{ marginBottom: '10px', textAlign: 'center' }}>
                    <Button type="default" onClick={handleContinue} style={{ background: '#f0f8ff', borderColor: '#1890ff', color: '#1890ff' }} disabled={streamingConversations.has(currentConversationId)}>
                        ↗️ Continue Response
                    </Button>
                </div>
            )}
            <div className={`input-container ${empty ? 'empty-state' : ''} ${isProcessing || streamingConversations.has(currentConversationId) ? 'sending' : ''}`}>
                {/* Display throttling error */}
                {throttlingError && (
                    <ThrottlingErrorDisplay
                        error={throttlingError}
                        onDismiss={() => setThrottlingError(null)}
                    />
                )}

                <TextArea
                    ref={textareaRef}
                    value={question}
                    onChange={handleQuestionChange}
                    id="chat-question-textarea"
                    placeholder={
                        isCurrentlyStreaming
                            ? "Provide feedback for running tools... (Enter to send)"
                            : "Enter your question.."
                    }
                    autoComplete="off"
                    autoSize={{ minRows: 1 }}
                    className={`input-textarea ${isCurrentlyStreaming ? 'streaming-input' : ''
                        } ${isCurrentlyStreaming ? 'feedback-mode' : ''}`}
                    style={{
                        borderColor: isCurrentlyStreaming ? '#52c41a' : undefined
                    }}
                    disabled={isTextAreaDisabled}
                    onPressEnter={(event) => {
                        if (!event.shiftKey && !isQuestionEmpty(question)) {
                            event.preventDefault();
                            if (shouldSendAsFeedback) {
                                sendToolFeedback();
                            } else {
                                handleSendPayload();
                            }
                        }
                    }}
                />
                <div style={{ marginLeft: '10px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                    {/* Always show stop button when streaming */}
                    {isCurrentlyStreaming && (
                        <StopStreamButton
                            conversationId={currentConversationId}
                            size="middle"
                            style={{
                                height: '32px',
                                display: 'flex',
                                alignItems: 'center'
                            }}
                        />
                    )}

                    {/* Show feedback button when streaming AND there's input */}
                    {shouldSendAsFeedback ? (
                        <Button
                            type="default"
                            onClick={sendToolFeedback}
                            disabled={isQuestionEmpty(question) || isSendingFeedback}
                            icon={<SendOutlined />}
                            title="Send feedback to running tools"
                            loading={isSendingFeedback}
                            style={{
                                backgroundColor: isDarkMode ? '#162312' : '#f6ffed',
                                borderColor: isDarkMode ? '#49aa19' : '#52c41a',
                                color: isDarkMode ? '#95de64' : '#52c41a'
                            }}
                        >
                            Send Feedback
                        </Button>
                    ) : null}

                    {/* Show regular send button when not streaming */}
                    {!isCurrentlyStreaming && (
                        <Button
                            type="primary"
                            onClick={() => handleSendPayload()}
                            disabled={isDisabled}
                            icon={<SendOutlined />}
                            title={buttonTitle}
                        >
                            Send
                        </Button>
                    )}
                </div>
            </div>
        </div>
    );
});

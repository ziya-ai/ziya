import { SetStateAction, Dispatch } from 'react';
import { message } from 'antd';
import { Message } from '../utils/types';
import { AppConfig, DEFAULT_CONFIG } from '../types/config';
import { formatMCPOutput, enhanceToolDisplayHeader } from '../utils/mcpFormatter';
import { handleToolStart, handleToolDisplay, ToolEventContext } from '../utils/mcpToolHandlers';

// WebSocket for real-time feedback
class FeedbackWebSocket {
    private ws: WebSocket | null = null;
    private conversationId: string | null = null;
    private connectionPromise: Promise<void> | null = null;
    private isConnected: boolean = false;
    private isConnecting: boolean = false;

    connect(conversationId: string): Promise<void> {
        // Prevent duplicate connections for the same conversation
        if (this.conversationId === conversationId && (this.isConnected || this.isConnecting)) {
            console.log('🔄 FEEDBACK: Already connected/connecting to:', conversationId);
            return this.connectionPromise || Promise.resolve();
        }

        // Disconnect any existing connection before creating new one
        if (this.ws && this.isConnected) {
            console.log('🔄 FEEDBACK: Disconnecting previous WebSocket before new connection');
            this.disconnect();
        }

        this.isConnecting = true;
        this.connectionPromise = new Promise((resolve, reject) => {
            this.conversationId = conversationId;

            // Use the same protocol as the current page (ws:// for http://, wss:// for https://)
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/feedback/${conversationId}`;

            console.log('🔄 FEEDBACK: Attempting WebSocket connection to:', wsUrl);
            this.ws = new WebSocket(wsUrl);

            // Set a connection timeout
            const connectionTimeout = setTimeout(() => {
                reject(new Error('WebSocket connection timeout'));
            }, 5000);

            this.ws.onopen = () => {
                clearTimeout(connectionTimeout);
                this.isConnected = true;
                this.isConnecting = false;
                console.log('🔄 FEEDBACK: WebSocket connected');
                resolve();
            };

            this.ws.onerror = (error) => {
                clearTimeout(connectionTimeout);
                this.isConnected = false;
                this.isConnecting = false;
                console.error('🔄 FEEDBACK: WebSocket error:', error);
                console.error('🔄 FEEDBACK: WebSocket URL was:', wsUrl);
                console.error('🔄 FEEDBACK: WebSocket readyState:', this.ws?.readyState);
                reject(error);
            };

            this.ws.onclose = () => {
                clearTimeout(connectionTimeout);
                this.isConnected = false;
                this.isConnecting = false;
                console.log('🔄 FEEDBACK: WebSocket closed');
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('🔄 FEEDBACK: Received message from server:', data);

                    // Handle feedback acknowledgment
                    if (data.type === 'feedback_acknowledged') {
                        console.log('🔄 FEEDBACK: Acknowledgment received for:', data.feedback_id);
                        document.dispatchEvent(new CustomEvent('feedbackAcknowledged', {
                            detail: {
                                feedbackId: data.feedback_id,
                                conversationId: this.conversationId
                            }
                        }));
                    }
                } catch (error) {
                    console.warn('🔄 FEEDBACK: Error parsing WebSocket message:', error);
                }
            };
        });

        return this.connectionPromise;
    }

    sendFeedback(toolId: string, feedback: string) {
        // Guard against sending on closed/closing WebSocket
        if (!this.ws || !this.isConnected || this.ws.readyState !== WebSocket.OPEN) {
            console.warn('🔄 FEEDBACK: Cannot send - WebSocket not ready:', { isConnected: this.isConnected, readyState: this.ws?.readyState });
            return;
        }

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'tool_feedback',
                tool_id: toolId,
                message: feedback,
                feedback_id: (window as any).__lastFeedbackId || Date.now().toString()
            }));
            console.log('🔄 FEEDBACK: Sent feedback:', feedback);
        } else {
            console.error('🔄 FEEDBACK: Cannot send feedback - WebSocket not ready. State:', this.ws?.readyState);
            // Fallback: Log that feedback would have been sent
            console.log('🔄 FEEDBACK: Would have sent feedback (WebSocket unavailable):', feedback);
        }
    }

    disconnect() {
        // Prevent duplicate disconnect calls
        if (!this.ws || (!this.isConnected && !this.isConnecting)) {
            console.log('🔄 FEEDBACK: Already disconnected or never connected');
            return;
        }

        console.log('🔄 FEEDBACK: Disconnecting WebSocket for:', this.conversationId);

        // Mark as disconnecting immediately to prevent race conditions
        this.isConnected = false;
        this.isConnecting = false;

        // Close the WebSocket connection
        try {
            this.ws?.close();
        } catch (error) {
            console.error('🔄 FEEDBACK: Error closing WebSocket:', error);
        }

        this.ws = null;
        this.conversationId = null;
        this.connectionPromise = null;
    }
}

const feedbackWebSocket = new FeedbackWebSocket();

// Make WebSocket available globally for components
(window as any).feedbackWebSocket = feedbackWebSocket;

type ProcessingState = 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error';

interface ErrorResponse {
    error: string;
    detail: string;
    conversation_id?: string;
    event?: string;
    status_code?: number;
    retry_after?: string;
    throttle_info?: {
        auto_attempts_exhausted?: boolean;
        total_auto_attempts?: number;
        can_user_retry?: boolean;
        backoff_used?: number[];
    };
    ui_action?: string;
    user_message?: string;
    preserved_content?: string;
    originalRequestData?: any;
}

const isValidMessage = (message: any) => {
    if (!message || typeof message !== 'object') return false;
    if (!message.content || typeof message.content !== 'string') return false;
    return message.content.trim().length > 0;
};

/**
 * Extract error information from SSE message content
 */
function extractErrorFromSSE(content: string): ErrorResponse | null {
    if (!content) return null;

    // Only check for errors in properly formatted SSE data that starts with "data: "
    // This prevents false positives from tool execution results or code examples
    if (!content.startsWith('data: ')) {
        return null;
    }

    try {
        // Try to parse as JSON first (most reliable error format)
        const dataContent = content.substring(6); // Remove "data: " prefix

        if (dataContent.trim() === '[DONE]') {
            return null;
        }

        try {
            const parsed = JSON.parse(dataContent);

            // Skip tool results - they're not errors even if they contain error text
            // This includes all tool-related message types that might contain error keywords in legitimate content
            if (parsed.tool_result || parsed.type === 'tool_start' || parsed.type === 'tool_display' ||
                parsed.type === 'tool_execution' || parsed.tool_name || parsed.tool_id ||
                (parsed.type && parsed.type.startsWith('tool_')) ||
                // Check for JSON structures containing tool data
                JSON.stringify(parsed).includes('"tool_result"')) {
                return null;
            }


            // Only treat as error if it's actually structured as an error response
            // This prevents false positives from content that mentions error keywords
            if (!parsed.error && !parsed.detail && !parsed.status_code) {
                return null; // Not an error structure, just content
            }

            // Check for explicit error objects
            if (parsed.error && parsed.error.type && parsed.error.detail) {
                return {
                    error: parsed.error.type,
                    detail: parsed.error.detail,
                    status_code: parsed.error.status_code
                };
            }

            // CRITICAL: Check for authentication/credential errors BEFORE requiring status_code
            // Server sends {error: 'message', error_type: 'authentication_error'}
            if (parsed.error_type === 'authentication_error' ||
                (parsed.error && typeof parsed.error === 'string' && (parsed.error.includes('credential') ||
                    parsed.error.includes('authentication') ||
                    parsed.error.includes('AWS credentials') ||
                    parsed.error.includes('mwinit')))) {
                return {
                    error: 'authentication_error',
                    detail: parsed.retry_message || parsed.error || 'Authentication error',
                    status_code: 401
                };
            }

            // Also check retry_message field for credential errors
            if (parsed.retry_message && typeof parsed.retry_message === 'string' &&
                (parsed.retry_message.includes('credential') || parsed.retry_message.includes('mwinit'))) {
                return {
                    error: 'authentication_error',
                    detail: parsed.retry_message,
                    status_code: 401
                };
            }

            // Check for direct error format
            if (parsed.error || parsed.detail) {
                return {
                    error: parsed.error || 'unknown_error',
                    detail: parsed.detail || parsed.error || 'An unknown error occurred',
                    status_code: parsed.status_code
                };
            }

            // Check for error content in JSON format (like the tool names error)
            if (parsed.type === 'error' && parsed.content) {
                // Check if it's a ValidationException
                if (parsed.content.includes('ValidationException')) {
                    if (parsed.content.includes('Input is too long')) {
                        return {
                            error: 'context_size_error',
                            detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.',
                            status_code: 413
                        };
                    } else {
                        return {
                            error: 'validation_error',
                            detail: parsed.content,
                            status_code: 400
                        };
                    }
                }
                // Handle other error types
                return {
                    error: 'unknown_error',
                    detail: parsed.content,
                    status_code: 500
                };
            }

            // Not an error JSON object
            return null;

        } catch (e) {
            // Not valid JSON, check for specific error patterns in plain text
            // But only if it looks like an actual error message, not tool output

            // Check for validation errors - but only in error-formatted messages
            if (dataContent.includes('ValidationException')) {
                // Make sure this isn't part of tool execution output
                if (!dataContent.includes('tool_execution') &&
                    !dataContent.includes('⟩') && !dataContent.includes('⟨') &&
                    !dataContent.includes('```') && // Not in code block
                    dataContent.includes('error')) { // Must have error context

                    if (dataContent.includes('Input is too long')) {
                        return {
                            error: 'context_size_error',
                            detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.',
                            status_code: 413
                        };
                    } else {
                        // Handle other ValidationExceptions with a generic message
                        return {
                            error: 'validation_error',
                            detail: 'Model parameter error: ' + dataContent.replace(/^data:\s*/, '').trim(),
                            status_code: 400
                        };
                    }
                }
            }

            // Check for authentication errors in plain text
            if (dataContent.includes('AWS credentials have expired') || dataContent.includes('authentication')) {
                return {
                    error: 'auth_error',
                    detail: 'AWS credentials have expired. Please refresh your credentials and try again.',
                    status_code: 401
                };
            }
            if ((dataContent.includes('ThrottlingException') || dataContent.includes('Too many requests')) &&
                !dataContent.includes('reached max retries') &&
                !dataContent.includes('tool_execution') && !dataContent.includes('⟩') && !dataContent.includes('⟨') &&
                !dataContent.includes('```') && // Not in code block
                !dataContent.includes('diff --git') && // Not in diff
                !dataContent.includes('MATH_INLINE') && // Not in tool output
                !dataContent.includes('Shell Command') && // Not in shell command output
                !dataContent.includes('result') && // Not in tool result
                !dataContent.includes('"type": "text"') && // Not regular text content
                !dataContent.includes('"content":') && // Not content field
                (dataContent.includes('"error"') && dataContent.includes('"detail"'))) { // Must be structured error
                return {
                    error: 'throttling_error',
                    detail: 'Too many requests to AWS Bedrock. Please wait a moment before trying again.',
                    status_code: 429
                };
            }

            // Check for exhausted retry throttling errors
            if ((dataContent.includes('ThrottlingException') || dataContent.includes('Too many requests')) &&
                (dataContent.includes('reached max retries') || dataContent.includes('exhausted')) &&
                !dataContent.includes('tool_execution') && !dataContent.includes('```')) {
                return {
                    error: 'throttling_error_exhausted',
                    detail: 'AWS Bedrock rate limit exceeded. All automatic retries have been exhausted.',
                    status_code: 429
                };
            }

            // Catch-all for any other error-like content that wasn't handled above
            if (dataContent.includes('Exception') && dataContent.includes('error') &&
                !dataContent.includes('tool_execution') &&
                !dataContent.includes('⟩') && !dataContent.includes('⟨') &&
                !dataContent.includes('```')) {
                return {
                    error: 'unknown_error',
                    detail: dataContent.replace(/^data:\s*/, '').trim(),
                    status_code: 500
                };
            }

            return null;
        }
    } catch (error) {
        console.warn('Error in extractErrorFromSSE:', error);
        return null;
    }

}

/**
 * Extract error from nested ops structure in LangChain output
 */
function extractErrorFromNestedOps(chunk: string): ErrorResponse | null {
    try {
        // Try to find JSON objects in the chunk

        // First check for validation errors in plain text
        if (chunk.includes('ValidationException')) {
            if (chunk.includes('Input is too long')) {
                return {
                    error: 'context_size_error',
                    detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.',
                    status_code: 413
                };
            } else {
                // Handle other ValidationExceptions with a generic message
                return {
                    error: 'validation_error',
                    detail: 'Model parameter error: ' + chunk.replace(/^data:\s*/, '').trim(),
                    status_code: 400
                };
            }
        }

        const jsonMatches = chunk.match(/(\{.*?\})/g);
        if (!jsonMatches) return null;

        for (const jsonStr of jsonMatches) {
            try {
                const data = JSON.parse(jsonStr);

                // Skip tool-related data structures completely
                if (data.tool_result || data.type === 'tool_start' || data.type === 'tool_display' ||
                    data.type === 'tool_execution' || data.tool_name || data.tool_id ||
                    (data.type && data.type.startsWith('tool_'))) {
                    continue;
                }
                // Check for authentication errors (these may not have status_code)
                if (data.error_type === 'authentication_error' ||
                    data.error === 'authentication_error' ||
                    (data.error && typeof data.error === 'string' &&
                        (data.error.includes('credential') ||
                            data.error.includes('Authentication failed') ||
                            data.error.includes('AWS credentials') ||
                            data.error.includes('mwinit'))) ||
                    (data.retry_message && typeof data.retry_message === 'string' &&
                        (data.retry_message.includes('credential') ||
                            data.retry_message.includes('mwinit')))) {
                    return {
                        error: 'authentication_error',
                        detail: data.retry_message ||
                            data.error ||
                            data.content ||
                            data.detail ||
                            'Authentication failed',
                        status_code: 401
                    };
                }

                // Only treat as error if it has error AND status_code (actual error response structure)
                // This prevents false positives when model discusses errors in tool output
                if ((data.error || data.detail) && data.status_code) {
                    return {
                        error: data.error || 'unknown_error',
                        detail: data.detail || data.error || 'An unknown error occurred',
                        status_code: data.status_code
                    };
                }

                // Check for ops array with errors
                if (data.ops && Array.isArray(data.ops)) {
                    for (const op of data.ops) {
                        if (op.value && typeof op.value === 'object') {
                            // Only treat as error if it has error AND status_code
                            if ((op.value.error || op.value.detail) && op.value.status_code) {
                                return {
                                    error: op.value.error || 'unknown_error',
                                    detail: op.value.detail || op.value.error || 'An unknown error occurred',
                                    status_code: op.value.status_code
                                };
                            }

                            // Check for error in messages array - but be careful not to match code examples
                            if (op.value.messages && Array.isArray(op.value.messages)) {
                                for (const msg of op.value.messages) {
                                    // Check for validation errors in message content
                                    if (msg.content && typeof msg.content === 'string' &&
                                        msg.content.includes('ValidationException')) {
                                        if (msg.content.includes('Input is too long')) {
                                            return { error: 'context_size_error', detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.', status_code: 413 };
                                        } else {
                                            // Handle other ValidationExceptions with a generic message
                                            return { error: 'validation_error', detail: 'Model parameter error: ' + msg.content.replace(/^data:\s*/, '').trim(), status_code: 400 };
                                        }
                                    }

                                    if (msg.content && typeof msg.content === 'string') {
                                        // Only check for errors outside of code blocks
                                        const isInCodeBlock = /```[\s\S]*?(?:Error:|error:)[\s\S]*?```/m.test(msg.content);
                                        if (!isInCodeBlock) {
                                            const errorResponse = extractErrorFromSSE(msg.content);
                                            if (errorResponse) return errorResponse;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (e) {
                // Not valid JSON, try next match
            }
        }

        // Catch-all for actual Exception patterns (not discussions about exceptions)
        // Only trigger if it looks like an actual error traceback or exception message
        if (chunk.includes('Exception:') &&
            !chunk.includes('tool_execution') &&
            !chunk.includes('```') &&
            (chunk.includes('Traceback') || chunk.includes('at line') || chunk.match(/Exception:\s*[A-Z]/))) {
            return {
                error: 'unknown_error',
                detail: chunk.replace(/^data:\s*/, '').trim(),
                status_code: 500
            };
        }

        return null;
    } catch (error) {
        console.warn('Error in extractErrorFromNestedOps:', error);
        return null;
    }
}

/**
 * Handle stream error response
 */
async function handleStreamError(response: Response): Promise<Error> {
    try {
        const text = await response.text();
        console.log("Error response text:", text);

        try {
            const data = JSON.parse(text);
            if (data.detail || data.error) {
                return new Error(data.detail || data.error);
            }
        } catch (e) {
            // Not JSON, use text directly
        }

        return new Error(text || `HTTP error ${response.status}`);
    } catch (error) {
        return new Error(`HTTP error ${response.status}`);
    }
}

/**
     * Show error message inline if it's long, otherwise as popup
     */
function showError(errorDetail: string, conversationId: string, addMessageToConversation: (message: Message, conversationId: string, isNonCurrentConversation?: boolean) => void, messageType: 'error' | 'warning' = 'error') {
    console.log('🔍 SHOW_ERROR_CALLED:', {
        errorLength: errorDetail.length,
        messageType,
        conversationId,
        errorPreview: errorDetail.substring(0, 100)
    });

    if (errorDetail.length > 100) {
        // Show inline as a collapsible message
        const errorMessage: Message = {
            role: 'assistant',  // CRITICAL: Use 'assistant' role so message renders (system messages are filtered in Conversation.tsx:206)
            content: `<details style="margin: 16px 0; padding: 12px; background: ${messageType === 'error' ? '#fff2f0' : '#fffbe6'}; border: 1px solid ${messageType === 'error' ? '#ffccc7' : '#ffe58f'}; border-radius: 6px;">
<summary style="cursor: pointer; font-weight: bold; color: ${messageType === 'error' ? '#cf1322' : '#d46b08'}; display: flex; align-items: center; gap: 8px;">
<span>${messageType === 'error' ? '❌' : '⚠️'}</span>
<span>${messageType === 'error' ? 'Error' : 'Warning'} Details</span>
<span style="font-weight: normal; opacity: 0.7;">(Click to expand)</span>
</summary>
<div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid ${messageType === 'error' ? '#ffd6cc' : '#fff1b8'}; white-space: pre-wrap; font-family: monospace; font-size: 13px; color: ${messageType === 'error' ? '#8c1f1f' : '#8c5f00'};">
${errorDetail}
</div>
</details>`,
            _timestamp: Date.now()
        };
        addMessageToConversation(errorMessage, conversationId);
        console.log('✅ SHOW_ERROR: Added long error message to conversation');
    } else {
        // Show as popup for short messages
        if (messageType === 'error') {
            message.error(errorDetail);
            console.log('✅ SHOW_ERROR: Displayed short error as popup');
        } else {
            message.warning(errorDetail);
            console.log('✅ SHOW_ERROR: Displayed short warning as popup');
        }
    }
}

export const sendPayload = async (
    messages: Message[],
    question: string,
    checkedItems: string[],
    conversationId: string,
    streamedContentMap: Map<string, string>,
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>,
    setIsStreaming: Dispatch<SetStateAction<boolean>>,
    removeStreamingConversation: (id: string) => void,
    addMessageToConversation: (message: Message, conversationId: string, isNonCurrentConversation?: boolean) => void,
    isStreamingToCurrentConversation: boolean = true,
    setProcessingState?: (state: ProcessingState) => void,
    setReasoningContentMap?: Dispatch<SetStateAction<Map<string, string>>>,
    throttlingRecoveryDataRef?: { toolResults?: any[]; partialContent?: string }
): Promise<string> => {
    let eventSource: any = null;
    let currentContent = '';
    let containsDiff = false;
    let errorOccurred = false;
    let errorAlreadyDisplayed = false;  // Track if we've shown an error to prevent duplicates
    let toolInputsMap = new Map<string, any>(); // Store tool inputs by tool ID

    // Store original params but also track accumulated content for retry
    let originalRequestParams = {
        messages,
        question,
        checkedItems,
        conversationId
    };

    // Connect feedback WebSocket
    let feedbackConnected = false;
    try {
        // Check if already connected before attempting connection
        if ((window as any).feedbackWebSocketReady && feedbackWebSocket['conversationId'] === conversationId) {
            console.log('🔄 FEEDBACK: WebSocket already connected for this conversation');
            feedbackConnected = true;
        } else {
            console.log('🔄 FEEDBACK: Attempting to connect WebSocket for conversation:', conversationId);
            await feedbackWebSocket.connect(conversationId);
            console.log('🔄 FEEDBACK: WebSocket connected successfully');

            // Notify components that WebSocket is ready
            (window as any).feedbackWebSocketReady = true;
            feedbackConnected = true;
        }
    } catch (e) {
        console.error('🔄 FEEDBACK: Failed to connect WebSocket:', e);
        console.warn('Failed to connect feedback WebSocket:', e);
        (window as any).feedbackWebSocketReady = false;
    }

    // Create an AbortController to handle cancellation
    const abortController = new AbortController();
    const { signal } = abortController;

    let isAborted = false;

    // Remove any existing listeners for this conversation ID to prevent duplicates
    document.removeEventListener('abortStream', window[`abortListener_${conversationId}`]);

    // Set up abort event listener
    const abortListener = (event: CustomEvent) => {
        if (event.detail.conversationId === conversationId) {
            console.log('🔄 ABORT: Received abort event for conversation:', conversationId);
            console.log(`Aborting stream for conversation: ${conversationId}`);
            abortController.abort();
            isAborted = true;

            console.log('Sending abort notification to server');
            // Also notify the server about the abort
            try {
                console.log('🔄 ABORT: Sending server-side abort notification');
                fetch('/api/abort-stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ conversation_id: conversationId }),
                }).catch(e => {
                    // Ignore errors from the abort request
                    console.warn('Error sending abort notification to server:', e);
                });
            } catch (e) { }

            removeStreamingConversation(conversationId);
            setIsStreaming(false);

            // Disconnect feedback WebSocket only if we connected it in this call
            // AND if there are no other active streaming conversations
            if (feedbackConnected && streamingConversations.size <= 1) {
                console.log('🔄 FEEDBACK: Last streaming conversation ending, disconnecting WebSocket');
                (window as any).feedbackWebSocketReady = false;
                feedbackWebSocket.disconnect();
            }
        }
    };
    document.addEventListener('abortStream', abortListener as EventListener);

    // CRITICAL FIX: Check if there's already an active stream for this conversation
    if (streamedContentMap.has(conversationId) && streamedContentMap.get(conversationId) !== '') {
        console.warn('🔄 Stream already active for conversation:', conversationId);
        console.warn('Aborting existing stream before starting new one');
        document.dispatchEvent(new CustomEvent('abortStream', { detail: { conversationId } }));
        await new Promise(resolve => setTimeout(resolve, 300));
    }

    try {
        // Filter out empty messages
        const messagesToSend = messages.filter(isValidMessage);

        // Log message count before and after filtering
        if (messages.length !== messagesToSend.length) {
            console.log("Filtered out empty messages:", {
                before: messages.length,
                after: messagesToSend.length,
                dropped: messages.length - messagesToSend.length
            });
        }

        setIsStreaming(true);
        let response = await getApiResponse(messagesToSend, question, checkedItems, conversationId, signal);
        console.log("Initial API response:", response.status, response.statusText);

        if (!response.ok) {
            if (response.status === 503) {
                console.log("Service unavailable, attempting retry");
                await handleStreamError(response);
                // Add a small delay before retrying
                await new Promise(resolve => setTimeout(resolve, 2000));
                // Retry the request once
                let retryResponse = await getApiResponse(messagesToSend, question, checkedItems, conversationId, signal);
                if (!retryResponse.ok) {
                    throw await handleStreamError(retryResponse);
                }
                response = retryResponse;
            } else if (response.status === 401) {
                console.log("Authentication error");
                // Handle auth failure explicitly
                throw await handleStreamError(response);
            } else {
                console.log("Other error:", response.status);
                // Handle other errors
                throw await handleStreamError(response);
            }
        }

        if (!response.body) {
            throw new Error('No body in response');
        }

        // Use ReadableStream API for more reliable streaming
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = ''; // Buffer for incomplete SSE messages

        // Process chunks as they arrive
        const processChunk = (chunk: string) => {
            // Add chunk to buffer
            buffer += chunk;

            // Split by double newlines to get complete SSE messages
            const messages = buffer.split('\n\n');

            // Keep the last potentially incomplete message in buffer
            buffer = messages.pop() || '';

            // DEBUG: Log how many complete messages we're processing
            if (messages.length > 0) {
                console.log(`🔍 CHUNK_DEBUG: Processing ${messages.length} complete SSE messages from buffer`);
            }

            // Process complete messages
            for (const sseMessage of messages) {
                if (!sseMessage.trim()) continue;

                // Check if it's an SSE data line
                if (sseMessage.startsWith('data:')) {
                    let dataContent = sseMessage.slice(5).trim();

                    // Handle multiple data: messages concatenated in the extracted content
                    // This happens when heartbeat messages get bundled with content messages
                    if (dataContent.includes('\n\ndata:') || dataContent.includes('\ndata:')) {
                        console.log('🔧 MULTI-DATA-FIX: Found concatenated data messages, splitting them');

                        // Split by data: boundaries and process each individually
                        const dataParts = dataContent.split(/\n\n?data:\s*/);
                        console.log('🔧 MULTI-DATA-FIX: Split into', dataParts.length, 'data parts');

                        // Process each part separately
                        for (let i = 0; i < dataParts.length; i++) {
                            const part = dataParts[i].trim();
                            if (!part) continue;

                            console.log(`🔧 MULTI-DATA-FIX: Processing data part ${i + 1}:`, part.substring(0, 100));

                            // Skip heartbeat messages entirely to prevent buffer pollution
                            if (part.includes('"heartbeat": true') || part.includes('"type": "heartbeat"')) {
                                console.log('🔧 MULTI-DATA-FIX: Skipping heartbeat message');
                                continue;
                            }

                            // Process the individual data part using the same logic below
                            processSingleDataMessage(part);
                        }

                        // Skip the original processing since we handled all parts above
                        continue;
                    }

                    // Process single data message (original logic)
                    processSingleDataMessage(dataContent);
                }
            }
        };

        // Extract the single data message processing logic into a separate function
        const processSingleDataMessage = (data: string) => {
            // Declare at function scope so it's accessible in all try blocks
            let jsonData: any;
            let unwrappedData: any;

            try {
                // Skip heartbeat messages entirely
                if (data.includes('"heartbeat": true') || data.includes('"type": "heartbeat"')) {
                    return;
                }

                // Parse JSON first to check message type
                jsonData = JSON.parse(data);

                // Unwrap tool_start and tool_result if they're wrapped
                unwrappedData = jsonData;
                if (jsonData.tool_start) {
                    unwrappedData = jsonData.tool_start;
                } else if (jsonData.tool_result) {
                    unwrappedData = jsonData.tool_result;
                    unwrappedData.type = 'tool_display';
                }

                // CRITICAL FIX: Check for errors BEFORE any other processing
                // This ensures errors are never skipped due to other conditions
                if (jsonData.error || jsonData.error_type === 'authentication_error') {
                    // Don't skip error processing - continue to the error handling code below
                    // But mark that we detected an error early
                    (window as any)._errorDetectedInChunk = true;
                }

                // Check if this is a hunk status update
                if (jsonData.request_id && jsonData.details && jsonData.details.hunk_statuses) {
                    // Dispatch a custom event with the hunk status update
                    window.dispatchEvent(new CustomEvent('hunkStatusUpdate', {
                        detail: {
                            requestId: jsonData.request_id,
                            hunkStatuses: jsonData.details.hunk_statuses
                        }
                    }));
                }

                // Handle context sync notifications from backend
                if (unwrappedData.type === 'context_sync') {
                    console.log('📂 CONTEXT_SYNC:', unwrappedData);

                    const addedFiles = unwrappedData.added_files || [];

                    if (addedFiles.length > 0) {
                        // Update frontend context to match backend
                        // This is just UI state sync - backend already has the files
                        try {
                            // Dispatch event for FolderContext to handle
                            window.dispatchEvent(new CustomEvent('syncContextFromBackend', {
                                detail: {
                                    addedFiles,
                                    reason: unwrappedData.reason
                                }
                            }));

                            console.log(`✅ Context UI synced: added ${addedFiles.join(', ')}`);
                        } catch (error) {
                            console.error('Error syncing context:', error);
                        }
                    }
                }

                // Handle diff validation failure with clear UI feedback
                if (unwrappedData.type === 'diff_validation_failed') {
                    console.log('❌ DIFF_VALIDATION_FAILED:', unwrappedData);

                    // Calculate rewind line (before the failed diff)
                    const lines = currentContent.split('\n');
                    const rewindLine = lines.length;

                    // Insert rewind marker
                    const rewindMarker = `<!-- REWIND_MARKER: ${rewindLine} -->`;
                    currentContent += `\n\n${rewindMarker}\n\n`;

                    // Add user-friendly notification
                    const notification = unwrappedData.context_enhanced
                        ? `🔄 **Validation Failed - Regenerating with Enhanced Context**\n\n` +
                        `Added files: ${unwrappedData.added_files.join(', ')}\n\n` +
                        `Regenerating ${unwrappedData.failed_hunks.length}/${unwrappedData.total_hunks} failed hunk(s)...`
                        : `🔄 **Validation Failed - Regenerating Diff**\n\n` +
                        `Fixing ${unwrappedData.failed_hunks.length}/${unwrappedData.total_hunks} failed hunk(s)...`;

                    currentContent += notification + '\n\n';

                    setStreamedContentMap((prev: Map<string, string>) => {
                        const next = new Map(prev);
                        next.set(conversationId, currentContent);
                        return next;
                    });

                    // The model will continue streaming the regenerated diff
                    return;  // Exit after handling validation failure
                }

                // Diff regeneration notifications are now just informational
                if (unwrappedData.type === 'diff_regeneration_requested') {
                    console.log('🔄 DIFF_REGENERATION:', unwrappedData);
                    // Context already enhanced by backend, just log it
                    if (unwrappedData.context_enhanced) {
                        console.log('📂 Backend enhanced context with:', unwrappedData.added_files);
                    }
                }

                // Filter internal tools using flag from backend
                if ((unwrappedData.type === 'tool_start' || unwrappedData.type === 'tool_display') &&
                    unwrappedData.is_internal === true) {
                    console.log('🔇 INTERNAL_TOOL: Skipping display for', unwrappedData.tool_name);
                    return; // Skip displaying this tool entirely
                }

                // CRITICAL: Check if this is tool-related content BEFORE doing any pattern detection
                const isToolContent = unwrappedData.type === 'tool_start' ||
                    unwrappedData.type === 'tool_display' ||
                    unwrappedData.type === 'tool_execution' ||
                    unwrappedData.tool_name ||
                    unwrappedData.tool_id;

                // Only check for diff patterns in non-tool content
                if (!isToolContent && !containsDiff && (
                    data.includes('```diff') || data.includes('diff --git') ||
                    data.match(/^@@ /) || data.match(/^\+\+\+ /) || data.match(/^--- /))) {
                    containsDiff = true;
                    console.log("Detected diff content, disabling error detection");
                }
            } catch (e) {
                // JSON parse error, continue processing
            }

            // Check for partial response preservation warnings
            try {
                const jsonData = JSON.parse(data);
                if (jsonData.warning === 'partial_response_preserved') {
                    console.warn('⚠️ PARTIAL RESPONSE PRESERVED:', jsonData.detail);
                    console.log('Preserved content:', jsonData.partial_content);

                    // Add the preserved content to current content
                    if (jsonData.partial_content && !currentContent.includes(jsonData.partial_content)) {
                        currentContent += jsonData.partial_content;
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });
                    }
                }
            } catch (e) {
                // Not a warning JSON, continue processing
            }

            // Check for errors using our new function - but be more careful
            // Skip error checking if the data looks like it contains tool execution results
            const containsCodeBlock = data.includes('```');
            const containsToolExecution = data.includes('tool_execution') || data.includes('⟩') || data.includes('⟨');

            // CRITICAL FIX: Check parsed JSON directly for error patterns
            // extractErrorFromSSE expects "data: " prefix which was already stripped
            let errorResponse = null;
            if (!(containsCodeBlock || containsDiff || containsToolExecution) && !errorAlreadyDisplayed) {
                if (jsonData && (jsonData.error || jsonData.error_type || jsonData.type === 'error')) {
                    const errorText = jsonData.error || jsonData.content || jsonData.detail || '';

                    // ValidationException handling (context too large)
                    if (errorText.includes('ValidationException') && errorText.includes('Input is too long')) {
                        errorResponse = {
                            error: 'context_size_error',
                            detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.',
                            status_code: 413
                        };
                    } else if (errorText.includes('ValidationException')) {
                        errorResponse = {
                            error: 'validation_error',
                            detail: errorText,
                            status_code: 400
                        };
                    } else if (
                        // Check error_type field
                        jsonData.error_type === 'authentication_error' ||
                        // Check if error field IS 'authentication_error'
                        jsonData.error === 'authentication_error' ||
                        // Check content/detail/retry_message for credential keywords
                        errorText.includes('credential') ||
                        errorText.includes('ExpiredToken') ||
                        errorText.includes('mwinit') ||
                        errorText.includes('AWS credentials') ||
                        // Also check retry_message specifically (server puts helpful message there)
                        (jsonData.retry_message && (
                            jsonData.retry_message.includes('credential') ||
                            jsonData.retry_message.includes('mwinit') ||
                            jsonData.retry_message.includes('expired')
                        ))
                    ) {
                        errorResponse = {
                            error: 'authentication_error',
                            detail: jsonData.retry_message || jsonData.content || errorText || 'Authentication failed. Please refresh your credentials.',
                            status_code: 401
                        };
                    } else {
                        // GENERIC FALLTHROUGH: Any error with meaningful text should be displayed
                        // This ensures unknown errors still show their message to the user
                        errorResponse = {
                            error: jsonData.error_type || 'unknown_error',
                            detail: errorText || jsonData.retry_message || 'An unknown error occurred',
                            status_code: jsonData.status_code || 500
                        };
                    }
                }
            }

            // NEW LOGIC: Distinguish between fatal and recoverable errors
            const hasSubstantialContent = currentContent.length > 1000;
            const isRecoverableError = errorResponse && (
                errorResponse.error === 'timeout' ||
                errorResponse.detail?.includes('timeout') ||
                errorResponse.detail?.includes('ReadTimeoutError') ||
                errorResponse.detail?.includes('Read timeout') ||
                (errorResponse.error === 'stream_error' && hasSubstantialContent)
            );

            if (errorResponse) {
                console.log('🔍 ERROR_HANDLING_START: About to process error and call showError');
                errorAlreadyDisplayed = true;  // Prevent duplicate error displays

                console.log('❌ ERROR DETECTED:', {
                    errorType: errorResponse.error,
                    contentLength: currentContent.length,
                    dataPreview: data.substring(0, 200),
                    conversationId: conversationId
                });

                // Handle recoverable errors after substantial content differently
                if (hasSubstantialContent && isRecoverableError) {
                    console.log('Recoverable error after substantial content - preserving and continuing:', {
                        contentLength: currentContent.length,
                        errorType: errorResponse.error,
                        errorDetail: errorResponse.detail
                    });

                    // Show warning but continue processing
                    showError(`Stream interrupted by ${errorResponse.error} after generating ${Math.round(currentContent.length / 1000)}KB of content. Content preserved.`, conversationId, addMessageToConversation, 'warning');
                }

                // CRITICAL: Always use the conversation_id from the original request
                // Don't let server errors override the target conversation
                const actualTargetId = conversationId; // Always use the original request's conversation ID

                console.log("Error routing debug:", {
                    originalConversationId: conversationId,
                    errorConversationId: errorResponse.conversation_id,
                    actualTargetId
                });

                const targetConversationId = actualTargetId;

                console.log("Current content when error detected:", currentContent.substring(0, 200) + "...");
                console.log("Current content length:", currentContent.length);
                console.log("Error detected in SSE data:", errorResponse);
                console.log("Error routing - local conversationId:", conversationId, "error conversation_id:", errorResponse.conversation_id, "target:", targetConversationId);

                // For throttling errors, include original request data for retry
                if (errorResponse.error === 'throttling_error' || errorResponse.error === 'throttling_error_exhausted') {
                    errorResponse.originalRequestData = {
                        messages, question, checkedItems, conversationId
                    };

                    // Dispatch custom event for throttling errors
                    document.dispatchEvent(new CustomEvent('throttlingError', {
                        detail: errorResponse
                    }));
                }

                // Check if the error data contains preserved content and dispatch it
                try {
                    const errorData = JSON.parse(data);

                    // CRITICAL: Extract and store successful tool results for retry
                    if (errorData.successful_tool_results && Array.isArray(errorData.successful_tool_results)) {
                        console.log('📦 THROTTLE_RECOVERY: Captured', errorData.successful_tool_results.length, 'successful tool results');

                        // Store in recovery data ref if provided
                        if (throttlingRecoveryDataRef) {
                            throttlingRecoveryDataRef.toolResults = errorData.successful_tool_results;
                            throttlingRecoveryDataRef.partialContent = currentContent;
                        }

                        // Also dispatch for other components that might need it
                        document.dispatchEvent(new CustomEvent('throttlingRecoveryData', {
                            detail: { conversationId, toolResults: errorData.successful_tool_results, partialContent: currentContent }
                        }));
                    }

                    // Include the current streamed content in the preserved data
                    if (currentContent && currentContent.trim()) {
                        errorData.existing_streamed_content = currentContent;
                        console.log('Including existing streamed content in preserved data:', currentContent.length, 'characters');
                    }

                    if (errorData.pre_streaming_work || errorData.preserved_content || errorData.successful_tool_results) {
                        console.log('Dispatching preserved content event from error data:', {
                            hasPreStreamingWork: !!errorData.pre_streaming_work,
                            hasPreservedContent: !!errorData.preserved_content,
                            hasSuccessfulTools: !!errorData.successful_tool_results
                        });

                        // Dispatch the preserved content event
                        document.dispatchEvent(new CustomEvent('preservedContent', {
                            detail: errorData
                        }));
                    }
                } catch (e) {
                    console.debug('Could not parse error data for preserved content:', e);
                }

                // Show different message for partial responses vs complete failures
                const errorMessage = currentContent.length > 0
                    ? `${errorResponse.detail} (Partial response preserved - ${currentContent.length} characters)`
                    : errorResponse.detail || 'An error occurred';

                console.log('🔍 CALLING_SHOW_ERROR:', {
                    errorMessage: errorMessage.substring(0, 100),
                    messageType: currentContent.length > 0 ? 'warning' : 'error',
                    targetConversationId
                });

                showError(errorMessage, targetConversationId, addMessageToConversation, currentContent.length > 0 ? 'warning' : 'error');
                console.log('✅ SHOW_ERROR_COMPLETED');
                errorOccurred = true;
                // Don't return here - let the stream finish naturally but prevent further content processing

                // If we have accumulated content, add it to the conversation before removing the stream
                if (currentContent && currentContent.trim()) {
                    const partialMessage: Message = {
                        role: 'assistant',
                        content: currentContent + '\n\n[Response interrupted: ' + (errorResponse.detail || 'An error occurred') + ']',
                        _timestamp: Date.now()
                    };
                    addMessageToConversation(partialMessage, targetConversationId, targetConversationId !== conversationId);
                    console.log('Preserved partial content as message:', currentContent.length, 'characters');
                }

                // Clean up streaming state
                setStreamedContentMap((prev: Map<string, string>) => {
                    const next = new Map(prev);
                    next.delete(targetConversationId);
                    return next;
                });
                return;  // Exit processSingleDataMessage, stream will continue to done marker
            }

            // Skip [DONE] marker
            if (data.trim() === '[DONE]') {
                return;
            }

            try {
                // Process the JSON object
                if (unwrappedData.heartbeat) {
                    console.log("Received heartbeat, skipping");
                    return;
                }

                // Handle done marker
                if (unwrappedData.done) {
                    console.log("Received done marker in JSON data");

                    // If error was already displayed, just end cleanly
                    if (errorAlreadyDisplayed) {
                        console.log('🔍 DONE_MARKER: Error already displayed, ending stream');
                        return;
                    }

                    // CRITICAL FIX: Check if there's an unhandled error in this chunk
                    // This handles edge cases where error and done arrive together
                    if ((jsonData.error || jsonData.error_type) && !errorAlreadyDisplayed) {
                        console.log('🚨 CRITICAL: Done marker received with error data in same chunk!', {
                            hasError: !!jsonData.error,
                            hasErrorType: !!jsonData.error_type,
                            errorType: jsonData.error_type,
                            errorPreview: jsonData.error?.substring(0, 100)
                        });

                        // Process the error before handling done
                        const combinedErrorResponse = {
                            error: jsonData.error_type || 'unknown_error',
                            detail: jsonData.error || jsonData.detail || 'An error occurred',
                            status_code: jsonData.status_code || 500
                        };

                        console.log('🚨 EMERGENCY_ERROR_DISPLAY: Showing error from done-marker chunk');
                        showError(combinedErrorResponse.detail, conversationId, addMessageToConversation, 'error');
                        errorOccurred = true;
                        errorAlreadyDisplayed = true;

                        // Clean up and stop processing
                        removeStreamingConversation(conversationId);
                        setIsStreaming(false);
                        return;
                    }

                    // No error, just a normal done marker
                    return;
                }

                // Handle throttling status messages
                if (unwrappedData.type === 'throttling_status') {
                    console.log('Throttling status:', unwrappedData.message);
                    showError(unwrappedData.message, conversationId, addMessageToConversation, 'warning');
                    return;
                }

                // Handle throttling failure
                if (unwrappedData.type === 'throttling_failed') {
                    console.log('Throttling failed:', unwrappedData.message);
                    showError(unwrappedData.message + ' Please retry your request.', conversationId, addMessageToConversation, 'error');
                    errorOccurred = true;
                    return;
                }

                // Handle throttling errors that occur between tool calls
                if (unwrappedData.type === 'throttling_error') {
                    console.log('🔄 THROTTLING_ERROR: Received throttling error chunk:', unwrappedData);

                    // Create an inline throttling notification after the last tool
                    const throttlingNotification = `\n\n---\n\n` +
                        `⚠️ **Rate Limit Reached**\n` +
                        `${unwrappedData.retry_message || unwrappedData.detail}\n\n` +
                        `**Tools executed before throttling:** ${unwrappedData.tools_executed || 0}\n\n` +
                        `<div style="margin-top: 12px; padding: 12px; background-color: rgba(250, 173, 20, 0.1); border-left: 3px solid #faad14; border-radius: 4px;">` +
                        `<button class="throttle-retry-button" data-conversation-id="${conversationId}" data-throttle-wait="${unwrappedData.suggested_wait || 60}" style="padding: 8px 16px; background-color: #1890ff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 500; margin-right: 8px;">🔄 Retry Now</button>` +
                        `<span style="color: #666; font-size: 13px;">Recommended wait: ${unwrappedData.suggested_wait || 60}s</span>` +
                        `</div>`;

                    // Add the notification to the streamed content
                    currentContent += throttlingNotification;
                    setStreamedContentMap((prev: Map<string, string>) => {
                        const next = new Map(prev);
                        next.set(conversationId, currentContent);
                        return next;
                    });

                    // Mark that streaming has ended due to throttling
                    errorOccurred = false; // Not a fatal error - user can retry

                    // Don't return - let the stream complete naturally to save content

                    // Dispatch event to notify MarkdownRenderer to attach handlers
                    setTimeout(() => {
                        document.dispatchEvent(new CustomEvent('throttleButtonRendered', {
                            detail: { conversationId }
                        }));
                    }, 150);
                }

                // SIMPLIFIED CONTENT PROCESSING - Single path for all content
                let contentToAdd = '';

                // Check for rewind markers in accumulated content first
                if (currentContent.includes('<!-- REWIND_MARKER:')) {
                    const rewindMatch = currentContent.match(/<!-- REWIND_MARKER: (\d+)(?:\|FENCE:([`~])(\w*))? -->(?:<\/span>)?/);
                    if (rewindMatch) {
                        const rewindLineNumber = parseInt(rewindMatch[1], 10);
                        const fenceType = rewindMatch[2]; // '`' or '~' or undefined
                        const fenceLanguage = rewindMatch[3]; // language tag or undefined

                        console.log(`🔄 REWIND: Marker detected - line ${rewindLineNumber}, fence: ${fenceType ? fenceType.repeat(3) + fenceLanguage : 'none'}`);

                        /**
                         * Parse markdown structure to detect unclosed blocks
                         * This is more robust than counting fences because it:
                         * 1. Only counts fences at line start (not in code/strings)
                         * 2. Matches fence types (``` with ```, ~~~ with ~~~)
                         * 3. Respects fence length (`````` opens/closes with ``````, not ```)
                         */
                        const parseMarkdownState = (content: string): {
                            inCodeBlock: boolean;
                            codeFenceType?: string;
                            codeFenceLanguage?: string;
                        } => {
                            const lines = content.split('\n');
                            const codeBlockStack: Array<{ type: string; language: string }> = [];

                            for (const line of lines) {
                                const trimmed = line.trimStart();

                                // Only match fences at line start (after optional whitespace)
                                const fenceMatch = trimmed.match(/^(`{3,}|~{3,})(\w*)/);
                                if (fenceMatch) {
                                    const fenceChars = fenceMatch[1];
                                    const language = fenceMatch[2] || '';
                                    const fenceType = fenceChars[0]; // '`' or '~'

                                    // Check if this closes the current block
                                    if (codeBlockStack.length > 0 &&
                                        codeBlockStack[codeBlockStack.length - 1].type === fenceType &&
                                        fenceChars.length >= 3) {
                                        // Closing fence - must be same type and at least 3 chars
                                        codeBlockStack.pop();
                                    } else if (fenceChars.length >= 3) {
                                        // Opening fence
                                        codeBlockStack.push({ type: fenceType, language });
                                    }
                                }
                            }

                            return {
                                inCodeBlock: codeBlockStack.length > 0,
                                codeFenceType: codeBlockStack.length > 0 ? codeBlockStack[codeBlockStack.length - 1].type : undefined,
                                codeFenceLanguage: codeBlockStack.length > 0 ? codeBlockStack[codeBlockStack.length - 1].language : undefined
                            };
                        };

                        // Remove everything from the rewind marker onwards
                        const lines = currentContent.split('\n');
                        const markerIndex = lines.findIndex(line => line.includes('<!-- REWIND_MARKER:') || line.includes('<span class="rewind-marker"'));
                        if (markerIndex >= 0) {
                            const beforeRewind = lines.slice(0, markerIndex).join('\n');

                            // Parse markdown state at the rewind point
                            const markdownState = parseMarkdownState(beforeRewind);

                            // If backend told us we're in a code block, trust that
                            // Otherwise, use our own analysis
                            const inCodeBlock = fenceType ? true : markdownState.inCodeBlock;
                            const needsFenceClosure = inCodeBlock && !fenceType; // We detected it but backend didn't tell us

                            if (inCodeBlock) {
                                console.log(`🔄 REWIND: At rewind point, we're inside a code block`, {
                                    backendFence: fenceType ? fenceType.repeat(3) + (fenceLanguage || '') : 'not specified',
                                    detectedFence: markdownState.codeFenceType?.repeat(3) + (markdownState.codeFenceLanguage || ''),
                                    willCloseFence: needsFenceClosure || !!fenceType
                                });

                                // Close the code block so the accumulated content is valid markdown
                                const fenceToUse = fenceType || markdownState.codeFenceType || '`';
                                currentContent = beforeRewind + '\n' + fenceToUse.repeat(3) + '\n';

                                // The continuation should re-open the fence
                                // If backend sent fence info, we know continuation will have raw code
                                // If we detected it ourselves, continuation might be malformed
                            } else {
                                currentContent = beforeRewind;
                            }
                            console.log(`🔄 REWIND: Reset content to before marker, length: ${currentContent.length}`);
                            // Update the map immediately
                            setStreamedContentMap((prev: Map<string, string>) => {
                                const next = new Map(prev);
                                next.set(conversationId, currentContent);
                                return next;
                            });
                        }
                    }
                }

                // Check for rewind markers that indicate continuation splicing
                if (jsonData.content && jsonData.content.includes('<!-- REWIND_MARKER:')) {
                    const rewindMatch = jsonData.content.match(/<!-- REWIND_MARKER: (\d+)(?:\|FENCE:([`~])(\w*))? -->(?:<\/span>)?/);
                    if (rewindMatch) {
                        const rewindLine = parseInt(rewindMatch[1], 10);
                        const fenceType = rewindMatch[2];
                        const fenceLanguage = rewindMatch[3];

                        console.log(`🔄 REWIND: Detected marker at line ${rewindLine}`);

                        // Rewind to the specified line number
                        const lines = currentContent.split('\n');
                        currentContent = lines.slice(0, rewindLine).join('\n');

                        // If backend told us we're in a code block, close it
                        if (fenceType) {
                            console.log(`🔄 REWIND: Backend indicates we're in a code block, closing it`);
                            currentContent += '\n' + fenceType.repeat(3) + '\n';
                        }

                        // Strip the marker and "Block continues" text from this chunk's content
                        // but keep any actual content that comes after
                        jsonData.content = jsonData.content
                            .replace(/<span class="rewind-marker"[^>]*><!-- REWIND_MARKER: \d+ --><\/span>\n?/g, '')
                            .replace(/<span class="rewind-marker"[^>]*><!-- REWIND_MARKER: \d+\|FENCE:[`~]\w* --><\/span>\n?/g, '')
                            .replace(/<!-- REWIND_MARKER: \d+ -->(?:<\/span>)?\n?/g, '')
                            .replace(/\*\*🔄 Block continues\.\.\.\*\*\n?/, '');

                        // If backend told us we're continuing a code block, 
                        // prepend the opening fence to the continuation content
                        if (fenceType && jsonData.content && jsonData.content.trim()) {
                            const fence = fenceType.repeat(3) + (fenceLanguage || '');
                            console.log(`🔄 REWIND: Prepending fence to continuation: ${fence}`);

                            // Only add fence if the continuation doesn't already start with one
                            const startsWithFence = jsonData.content.trimStart().match(/^(`{3,}|~{3,})/);
                            if (!startsWithFence) {
                                jsonData.content = fence + '\n' + jsonData.content;
                            }
                        }

                        console.log(`🔄 REWIND: Rewound to line ${rewindLine}, stripped marker text`);
                        // Update the map immediately to reflect the rewound content
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });

                        // Continue processing this chunk in case there's actual content after the marker
                    }
                }

                // Handle continuation rewind markers
                if (jsonData.type === 'continuation_rewind') {
                    // Handle marker-based rewind (for diff validation)
                    if (jsonData.type === 'rewind' && jsonData.to_marker) {
                        const marker = `<span class="diff-rewind-marker" data-marker="${jsonData.to_marker}"`;
                        console.log(`🔄 MARKER_REWIND: Searching for marker: ${marker}`);

                        const markerIndex = currentContent.indexOf(marker);
                        if (markerIndex >= 0) {
                            currentContent = currentContent.substring(0, markerIndex);
                            console.log(`✂️ MARKER_REWIND: Cut at marker position ${markerIndex}, preserved ${currentContent.length} chars`);

                            setStreamedContentMap((prev: Map<string, string>) => {
                                const next = new Map(prev);
                                next.set(conversationId, currentContent);
                                return next;
                            });
                        } else {
                            console.warn(`⚠️ MARKER_REWIND: Marker not found: ${marker}`);
                        }
                        return;
                    }

                    console.log('🔄 REWIND: Received continuation rewind marker:', jsonData);
                    // Remove the last incomplete line based on rewind_line
                    const lines = currentContent.split('\n');
                    if (jsonData.rewind_line && lines.length > jsonData.rewind_line) {
                        const beforeRewind = lines.slice(0, jsonData.rewind_line).join('\n');
                        currentContent = beforeRewind;
                        console.log(`🔄 REWIND: Trimmed content to line ${jsonData.rewind_line}, length: ${currentContent.length}`);
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });
                    }
                    return;
                }

                // Handle continuation failure
                if (jsonData.type === 'continuation_failed') {
                    console.log('🔄 CONTINUATION_FAILED:', jsonData);
                    const failureMessage = jsonData.can_retry
                        ? '⚠️ Response continuation was interrupted due to rate limiting. Click "Retry" to continue.'
                        : '❌ Response continuation failed. The response may be incomplete.';

                    showError(failureMessage, conversationId, addMessageToConversation, 'warning');

                    // Add retry button or indicator if applicable
                    if (jsonData.can_retry) {
                        // Could add a retry mechanism here
                    }
                    return;
                }

                if (jsonData.content) {
                    // Handle any content field - this covers most cases
                    contentToAdd = jsonData.content;
                } else if (jsonData.text) {
                    // Handle legacy text field
                    contentToAdd = jsonData.text;
                }

                // Add content if we found any
                if (contentToAdd) {
                    currentContent += contentToAdd;

                    // Use functional update to prevent race conditions
                    setStreamedContentMap((prev: Map<string, string>) => {
                        const next = new Map(prev);
                        // Always use the latest currentContent value
                        next.set(conversationId, currentContent);
                        return next;
                    });
                }

                // Handle feedback readiness - consolidated handling
                if (unwrappedData.type === 'feedback_ready' || jsonData.type === 'feedback_ready') {
                    const feedbackData = unwrappedData.type === 'feedback_ready' ? unwrappedData : jsonData;
                    console.log('🔄 FEEDBACK: Tool ready for feedback:', feedbackData.tool_name, 'ID:', feedbackData.tool_id);

                    // Dispatch event to enable feedback UI
                    document.dispatchEvent(new CustomEvent('feedbackReady', {
                        detail: {
                            toolId: feedbackData.tool_id,
                            toolName: feedbackData.tool_name,
                            conversationId
                        }
                    }));
                    console.log('🔄 FEEDBACK: Dispatched feedbackReady event');
                }

                // Handle MCP tool display events
                if (unwrappedData.type === 'tool_display') {
                    console.log('🔧 TOOL_DISPLAY received:', unwrappedData);

                    // Check for MCP tool errors in the result
                    if (unwrappedData.result && typeof unwrappedData.result === 'string') {
                        if (unwrappedData.result.includes('returned non-zero exit status') ||
                            unwrappedData.result.includes('Content truncated')) {
                            // Handle as recoverable tool error
                            console.warn('🔧 MCP tool error detected:', unwrappedData.result);
                            // Could show a warning or suggest retry
                        }
                    }

                    // Check for specialized tool handlers first
                    const contentRef = { value: currentContent };
                    const context: ToolEventContext = {
                        conversationId,
                        currentContent: contentRef,
                        setStreamedContentMap,
                        toolInputsMap
                    };

                    if (handleToolDisplay(unwrappedData.tool_name, unwrappedData, context)) {
                        currentContent = contentRef.value;
                        return; // Handler processed the event, return from function
                    }

                    let toolName = unwrappedData.tool_name;
                    const storedInput = toolInputsMap.get(unwrappedData.tool_id);
                    const storedHeader = toolInputsMap.get(`${unwrappedData.tool_id}_header`);

                    // Normalize tool name
                    if (!toolName.startsWith('mcp_')) {
                        toolName = `mcp_${toolName}`;
                    }
                    toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');

                    const actualToolName = toolName.replace('mcp_', '');

                    // Define isShellCommandTool here so it's available throughout the handler
                    const isShellCommandTool = actualToolName === 'run_shell_command';

                    // Build display header with proper fallback chain
                    let displayHeader = storedHeader;
                    if (!displayHeader) {
                        displayHeader = unwrappedData.display_header;
                    }
                    if (!displayHeader) {
                        displayHeader = toolName.replace('mcp_', '').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());
                    }

                    // Enhance shell command headers with the actual command
                    if (isShellCommandTool && storedInput?.command) {
                        // Truncate long commands for the header
                        const cmdPreview = storedInput.command.length > 50
                            ? storedInput.command.substring(0, 47) + '...'
                            : storedInput.command;
                        console.log('🔍 HEADER_DEBUG (tool_display): cmdPreview before sanitization:', JSON.stringify(cmdPreview));
                        displayHeader = `Shell Command: ${cmdPreview}`;
                        console.log('🔍 HEADER_DEBUG (tool_display): displayHeader before sanitization:', JSON.stringify(displayHeader));
                    }

                    // CRITICAL: Always sanitize displayHeader for shell commands AFTER all header logic
                    // This ensures newlines are removed regardless of header source (stored, enhanced, etc.)
                    // Newlines in displayHeader break HTML comments and regex matching
                    if (isShellCommandTool && displayHeader) {
                        displayHeader = displayHeader
                            .replace(/\n/g, ' ') // Replace newlines with spaces
                            .replace(/\s+/g, ' ') // Collapse multiple spaces
                            .trim();
                        console.log('🔍 HEADER_DEBUG (tool_display): displayHeader AFTER sanitization:', JSON.stringify(displayHeader));
                        console.log('🔍 HEADER_DEBUG (tool_display): Has newlines?', displayHeader.includes('\n'));
                    }

                    // Prepare content for display
                    let displayContent = unwrappedData.result;

                    // Parse result if it's a JSON string to extract the actual content
                    if (typeof displayContent === 'string' && (displayContent.startsWith('{') || displayContent.startsWith('['))) {
                        try {
                            displayContent = JSON.parse(displayContent);
                        } catch (e) {
                            // Keep as string if parsing fails
                        }
                    }

                    // For shell commands, add command as first line if not present
                    if (actualToolName === 'run_shell_command' && storedInput?.command && !displayContent.startsWith('$ ')) {
                        displayContent = `$ ${storedInput.command}\n${displayContent}`;
                    }

                    // Format the content
                    const inputForFormatter = unwrappedData.args || storedInput;

                    console.log('🔍 FORMATTING DEBUG:', {
                        toolName,
                        hasInput: !!inputForFormatter,
                        inputKeys: inputForFormatter ? Object.keys(inputForFormatter) : [],
                        hasRegistry: !!(window as any).FormatterRegistry,
                        registryFormatters: (window as any).FormatterRegistry ? (window as any).FormatterRegistry.getAllFormatters().length : 0
                    });

                    const formatted = formatMCPOutput(toolName, displayContent, inputForFormatter, {
                        showInput: false,
                        maxLength: 10000,
                        defaultCollapsed: true
                    });

                    console.log('🔍 FORMATTING RESULT:', {
                        type: formatted.type,
                        hasHierarchical: !!formatted.hierarchicalResults,
                        hierarchicalCount: formatted.hierarchicalResults?.length || 0
                    });

                    console.log('🔧 FORMATTED CONTENT:', formatted.content.substring(0, 200));
                    console.log('🔧 FORMATTED TYPE:', formatted.type);
                    console.log('🔧 HAS HIERARCHICAL:', !!formatted.hierarchicalResults);

                    // Create tool display with header - handle hierarchical results
                    let toolResultContent: string;
                    let toolResultDisplay: string;

                    if (actualToolName === 'run_shell_command') {
                        // Shell commands: wrap in TOOL_BLOCK with shell code fence inside
                        toolResultContent = displayContent;
                        const needsExtraNewline = !currentContent.endsWith('\n\n');
                        toolResultDisplay = `${needsExtraNewline ? '\n\n' : '\n'}<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n\`\`\`shell\n${toolResultContent}\n\`\`\`\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    } else if (formatted.hierarchicalResults && formatted.hierarchicalResults.length > 0) {
                        // CRITICAL: Pass hierarchicalResults as JSON structure, NOT as serialized markdown.
                        toolResultContent = JSON.stringify({
                            _isStructuredToolResult: true,
                            summary: formatted.summary || formatted.content,
                            type: formatted.type,
                            hierarchicalResults: formatted.hierarchicalResults
                        });
                        const needsExtraNewline = !currentContent.endsWith('\n\n');
                        toolResultDisplay = `${needsExtraNewline ? '\n\n' : '\n'}<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n${toolResultContent}\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    } else if (formatted.type === 'search_results' && formatted.content) {
                        // For search results without hierarchical structure, show the formatted content
                        toolResultContent = formatted.content;
                        const needsExtraNewline = !currentContent.endsWith('\n\n');
                        toolResultDisplay = `${needsExtraNewline ? '\n\n' : '\n'}<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n${toolResultContent}\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    } else {
                        // Default: use formatted content as-is
                        toolResultContent = formatted.content;
                        const needsExtraNewline = !currentContent.endsWith('\n\n');
                        toolResultDisplay = `${needsExtraNewline ? '\n\n' : '\n'}<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n${toolResultContent}\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    }

                    // STRATEGY 1: Use tool_id marker (most reliable)
                    const toolMarker = `<!-- TOOL_MARKER:${unwrappedData.tool_id} -->`;
                    let markerIndex = currentContent.indexOf(toolMarker);

                    console.log('🔧 TOOL_RESULT: Strategy 1 (tool_id marker):', markerIndex);


                    // DEBUG: Log marker search
                    console.log('🔧 TOOL_RESULT: Searching for marker:', toolMarker);
                    console.log('🔧 TOOL_RESULT: Current content includes marker?', currentContent.includes(toolMarker));
                    console.log('🔧 TOOL_RESULT: Current content length:', currentContent.length);
                    if (markerIndex === -1) {
                        console.error('MARKER NOT FOUND', {
                            toolId: unwrappedData.tool_id,
                            toolName: unwrappedData.tool_name,
                            contentIncludes: currentContent.includes('TOOL_MARKER')
                        });
                    }

                    if (markerIndex !== -1) {
                        // Found the marker! Now find the end of the tool block
                        const searchStart = markerIndex;

                        // Determine block end marker based on tool type
                        let blockEndMarker: string;
                        let blockEndOffset: number;

                        if (isShellCommandTool) {
                            // For shell commands, find the start of the ```shell block
                            // The marker is placed right before the shell block
                            // Pattern: \n```shell\n$ command\n⏳ Running...\n```
                            const afterMarkerContent = currentContent.substring(markerIndex);
                            // Pattern now includes the TOOL_BLOCK_START comment between TOOL_MARKER and shell fence
                            const shellBlockMatch = afterMarkerContent.match(/^<!-- TOOL_MARKER:[^>]+ -->\n<!-- TOOL_BLOCK_START:[^>]+ -->\n```shell\n/);
                            if (shellBlockMatch) {
                                // Find where the code block starts (right after the marker)
                                const shellBlockStart = markerIndex + shellBlockMatch[0].length;

                                // Find the closing fence after the marker
                                const afterShellBlock = currentContent.substring(shellBlockStart);
                                const closingFenceMatch = afterShellBlock.match(/\n```\n/);

                                if (closingFenceMatch) {
                                    const closingFenceIndex = shellBlockStart + afterShellBlock.indexOf(closingFenceMatch[0]);

                                    // Replace everything from the marker through the closing fence
                                    // This removes the entire "Running..." block and replaces it with the result
                                    currentContent = currentContent.substring(0, markerIndex) +
                                        toolResultDisplay +
                                        currentContent.substring(closingFenceIndex + closingFenceMatch[0].length);

                                    console.log('🔧 TOOL_RESULT: Replaced using tool_id marker (shell command)');
                                } else {
                                    // Couldn't find closing fence, append instead
                                    currentContent += toolResultDisplay;
                                    console.log('🔧 TOOL_RESULT: Could not find closing fence, appending');
                                }
                            } else {
                                // Pattern didn't match, append instead
                                currentContent += toolResultDisplay;
                                console.log('🔧 TOOL_RESULT: Shell block pattern not found, appending');
                            }
                        } else {
                            // For other tools, look for TOOL_BLOCK_END
                            // The TOOL_MARKER is placed right before TOOL_BLOCK_START, so the block starts at the marker position
                            const toolBlockStart = markerIndex;

                            blockEndMarker = `<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->`;
                            const blockEndIndex = currentContent.indexOf(blockEndMarker, searchStart);

                            if (blockEndIndex !== -1) {
                                blockEndOffset = blockEndMarker.length + 2; // +2 for trailing \n\n

                                // Replace from marker through end marker
                                currentContent = currentContent.substring(0, toolBlockStart) +
                                    toolResultDisplay +
                                    currentContent.substring(blockEndIndex + blockEndOffset);

                                console.log('🔧 TOOL_RESULT: Replaced using tool_id marker (standard tool)');
                            } else {
                                currentContent += toolResultDisplay;
                                console.log('🔧 TOOL_RESULT: Could not find block end after marker, appending');
                            }
                        }
                    } else {
                        // FALLBACK: Try pattern matching strategies (for backward compatibility)
                        console.log('🔧 TOOL_RESULT: Marker not found, falling back to pattern matching');

                        let lastStartIndex = -1;

                        // Strategy 2: Try exact match with current displayHeader (flexible backticks)
                        if (isShellCommandTool) {
                            // Use regex to match 3 or 4 backticks
                            const escapedToolName = toolName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                            const escapedHeader = displayHeader.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                            const pattern = new RegExp(`\`{3,4}tool:${escapedToolName}\\|${escapedHeader}`, 'g');
                            const matches = [...currentContent.matchAll(pattern)];
                            if (matches.length > 0) {
                                lastStartIndex = matches[matches.length - 1].index!;
                            }
                        } else {
                            const exactPattern = `<!-- TOOL_BLOCK_START:${toolName}|${displayHeader} -->`;
                            lastStartIndex = currentContent.lastIndexOf(exactPattern);
                        }
                        console.log('🔧 TOOL_RESULT: Strategy 2 (pattern match with flexible backticks):', lastStartIndex);

                        // Strategy 3: Try stored header
                        if (lastStartIndex === -1 && storedHeader && storedHeader !== displayHeader) {
                            if (isShellCommandTool) {
                                const escapedToolName = toolName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                                const escapedStoredHeader = storedHeader.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                                const pattern = new RegExp(`\`{3,4}tool:${escapedToolName}\\|${escapedStoredHeader}`, 'g');
                                const matches = [...currentContent.matchAll(pattern)];
                                if (matches.length > 0) {
                                    lastStartIndex = matches[matches.length - 1].index!;
                                }
                            } else {
                                const storedHeaderPattern = `<!-- TOOL_BLOCK_START:${toolName}|${storedHeader} -->`;
                                lastStartIndex = currentContent.lastIndexOf(storedHeaderPattern);
                            }
                            console.log('🔧 TOOL_RESULT: Strategy 3 (stored header):', lastStartIndex);
                        }

                        // Strategy 4: Last resort - match just tool name
                        if (lastStartIndex === -1) {
                            if (isShellCommandTool) {
                                const escapedToolName = toolName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                                const pattern = new RegExp(`\`{3,4}tool:${escapedToolName}\\|`, 'g');
                                const matches = [...currentContent.matchAll(pattern)];
                                if (matches.length > 0) {
                                    lastStartIndex = matches[matches.length - 1].index!;
                                }
                            } else {
                                const toolNameOnlyPattern = `<!-- TOOL_BLOCK_START:${toolName}|`;
                                lastStartIndex = currentContent.lastIndexOf(toolNameOnlyPattern);
                            }
                            console.log('🔧 TOOL_RESULT: Strategy 4 (tool name only):', lastStartIndex);
                        }

                        if (lastStartIndex !== -1) {
                            // Find block end with flexible backtick matching for shell commands
                            let blockEndMarker: string;
                            let endOffset: number;

                            if (isShellCommandTool) {
                                // Search for 3 or 4 backticks after the start
                                const afterStart = currentContent.substring(lastStartIndex);
                                const backtickMatch = afterStart.match(/\n(`{3,4})\n/);
                                if (backtickMatch) {
                                    blockEndMarker = backtickMatch[0];
                                    endOffset = backtickMatch[0].length;
                                    const blockEndIndex = lastStartIndex + afterStart.indexOf(backtickMatch[0]);
                                    const replaceStart = lastStartIndex > 0 && currentContent[lastStartIndex - 1] === '\n' ? lastStartIndex - 1 : lastStartIndex;
                                    currentContent = currentContent.substring(0, replaceStart) + toolResultDisplay + currentContent.substring(blockEndIndex + endOffset);
                                    console.log('🔧 TOOL_RESULT: Replaced tool block (pattern match)');
                                } else {
                                    currentContent += toolResultDisplay;
                                    console.log('🔧 TOOL_RESULT: No block end found, appending');
                                }
                            } else {
                                // Block end marker includes tool_id, so we need to search for it with tool_id
                                blockEndMarker = `<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->`;
                                const blockEndIndex = currentContent.indexOf(blockEndMarker, lastStartIndex);
                                if (blockEndIndex !== -1) {
                                    endOffset = blockEndMarker.length + 2;
                                    const replaceStart = lastStartIndex > 0 && currentContent[lastStartIndex - 1] === '\n' ? lastStartIndex - 1 : lastStartIndex;
                                    currentContent = currentContent.substring(0, replaceStart) + toolResultDisplay + currentContent.substring(blockEndIndex + endOffset);
                                    console.log('🔧 TOOL_RESULT: Replaced tool block (pattern match)');
                                } else {
                                    currentContent += toolResultDisplay;
                                    console.log('🔧 TOOL_RESULT: No block end found, appending');
                                }
                            }
                        } else {
                            currentContent += toolResultDisplay;
                            console.log('🔧 TOOL_RESULT: Pattern matching failed, appending');
                        }
                    }

                    setStreamedContentMap((prev: Map<string, string>) => {
                        const next = new Map(prev);
                        next.set(conversationId, currentContent);
                        return next;
                    });
                } else if (unwrappedData.type === 'tool_start') {
                    console.log('🔧 TOOL_START received:', unwrappedData);

                    // Check for specialized tool handlers first
                    const contentRef = { value: currentContent };
                    const context: ToolEventContext = {
                        conversationId,
                        currentContent: contentRef,
                        setStreamedContentMap,
                        toolInputsMap
                    };

                    if (handleToolStart(unwrappedData.tool_name, unwrappedData, context)) {
                        currentContent = contentRef.value;

                        // CRITICAL: Update the streamed content map so UI reflects the change
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });

                        // Store tool input for later use in tool_display
                        if (unwrappedData.args && unwrappedData.tool_id) {
                            toolInputsMap.set(unwrappedData.tool_id, unwrappedData.args);
                        }
                        if (unwrappedData.input && unwrappedData.tool_id) {
                            toolInputsMap.set(unwrappedData.tool_id, unwrappedData.input);
                        }
                        return; // Handler processed the event, return from function
                    }

                    // Store tool input for later use in tool_display
                    if (unwrappedData.args && unwrappedData.tool_id) {
                        toolInputsMap.set(unwrappedData.tool_id, unwrappedData.args);
                    }
                    if (unwrappedData.input && unwrappedData.tool_id) {
                        toolInputsMap.set(unwrappedData.tool_id, unwrappedData.input);
                    }

                    // Store display_header for matching in tool_result
                    if (unwrappedData.display_header && unwrappedData.tool_id) {
                        toolInputsMap.set(`${unwrappedData.tool_id}_header`, unwrappedData.display_header);
                    }

                    let toolName = unwrappedData.tool_name;
                    if (!toolName.startsWith('mcp_')) {
                        toolName = `mcp_${toolName}`;
                    }
                    toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');

                    const actualToolName = toolName.replace('mcp_', '');

                    // Enhance display header with search parameters using generic utility
                    const baseHeader = unwrappedData.display_header || toolName.replace('mcp_', '').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());
                    const inputArgs = unwrappedData.args || unwrappedData.input || {};
                    let displayHeader = enhanceToolDisplayHeader(toolName, baseHeader, inputArgs);
                    
                    // Sanitize displayHeader to ensure it never contains newlines (breaks HTML comments)
                    // This is critical for commands with \n in their arguments
                    displayHeader = displayHeader
                        .replace(/\n/g, ' ') // Replace newlines with spaces
                        .replace(/\s+/g, ' ') // Collapse multiple spaces
                        .trim();
                    console.log('🔍 HEADER_DEBUG (tool_start): displayHeader AFTER sanitization:', JSON.stringify(displayHeader));
                    console.log('🔍 HEADER_DEBUG (tool_start): Has newlines?', displayHeader.includes('\n'));

                    // Store the enhanced header for later matching in tool_display
                    if (unwrappedData.tool_id) {
                        toolInputsMap.set(`${unwrappedData.tool_id}_header`, displayHeader);
                    }

                    // Generate tool start display
                    let toolStartDisplay;

                    if (actualToolName === 'run_shell_command' && inputArgs.command) {
                        // For shell: add TOOL_MARKER before the shell block so tool_display can find and replace it
                        // Use TOOL_BLOCK format with shell code fence inside, matching tool_display format
                        toolStartDisplay = `\n<!-- TOOL_MARKER:${unwrappedData.tool_id} -->\n<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n\`\`\`shell\n$ ${inputArgs.command}\n⏳ Running...\n\`\`\`\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    } else if (actualToolName === 'get_current_time') {
                        // Add TOOL_MARKER for reliable replacement
                        toolStartDisplay = `\n<!-- TOOL_MARKER:${unwrappedData.tool_id} -->\n<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n⏳ Getting current time...\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    } else {
                        // Add TOOL_MARKER for all tools so tool_display can find and replace reliably
                        toolStartDisplay = `\n<!-- TOOL_MARKER:${unwrappedData.tool_id} -->\n<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${unwrappedData.tool_id} -->\n⏳ Running...\n<!-- TOOL_BLOCK_END:${toolName}|${unwrappedData.tool_id} -->\n\n`;
                    }

                    console.log('🔧 TOOL_START formatted:', toolStartDisplay);
                    currentContent += toolStartDisplay;
                    setStreamedContentMap((prev: Map<string, string>) => {
                        const next = new Map(prev);
                        next.set(conversationId, currentContent);
                        return next;
                    });
                }

                // Process operations if present
                const ops = jsonData.ops || [];
                for (const op of ops) {
                    if (op.op === 'add' && op.path === '/processing_state' && typeof setProcessingState === 'function') {
                        // Handle processing state updates
                        const stateValue = op.value;
                        if (stateValue === 'awaiting_model_response') {
                            setProcessingState('processing_tools');
                        }
                        // Note: State will auto-reset to 'idle' when removeStreamingConversation is called
                        // No return needed here, continue to next op
                    }
                    if (op.op === 'add' && op.path.endsWith('/reasoning_content/-')) {
                        // Handle reasoning content separately
                        const reasoningContent = op.value || '';
                        if (reasoningContent && setReasoningContentMap) {
                            console.log('ChatAPI: Adding reasoning content:', reasoningContent);
                            setReasoningContentMap((prev: Map<string, string>) => {
                                const next = new Map(prev);
                                const existing = next.get(conversationId) || '';
                                next.set(conversationId, existing + reasoningContent);
                                return next;
                            });
                        }
                        // No return needed here, continue to next op
                    }
                    if (op.op === 'add' && op.path.endsWith('/streamed_output_str/-')) {
                        let newContent = op.value || '';
                        if (!newContent) return;

                        // Handle new Bedrock format with content= wrapper
                        if (typeof newContent === 'string' && newContent.includes('content=')) {
                            let extractedContent = '';

                            // Find where content= starts and determine quote type
                            const contentMatch = newContent.match(/content=(['"])/);
                            if (contentMatch) {
                                const quoteChar = contentMatch[1];
                                const startPos = newContent.indexOf('content=') + 8 + 1; // +8 for 'content=', +1 for opening quote
                                const restOfString = newContent.substring(startPos);

                                // Find the closing quote by looking for: quote + whitespace + (additional_kwargs | response_metadata | end)
                                // Use a more careful approach that handles nested quotes
                                const escapedQuote = quoteChar === '"' ? '\\"' : "\\'";
                                const endPattern = new RegExp(`${quoteChar}\\s*(?:additional_kwargs=|response_metadata=|$)`);
                                const endMatch = restOfString.search(endPattern);

                                if (endMatch !== -1) {
                                    extractedContent = restOfString.substring(0, endMatch);
                                } else {
                                    // Fallback: find last occurrence of quote char
                                    const lastQuotePos = restOfString.lastIndexOf(quoteChar);
                                    extractedContent = lastQuotePos > 0
                                        ? restOfString.substring(0, lastQuotePos)
                                        : restOfString;
                                }
                            } else {
                                // No content= wrapper found, use as-is
                                extractedContent = newContent;
                            }

                            // Unescape common escape sequences
                            // Don't unescape backslashes inside math blocks as \\ is meaningful in LaTeX
                            // CRITICAL: Don't unescape \n inside mermaid/code blocks
                            const isMermaidBlock = extractedContent.includes('```mermaid') ||
                                extractedContent.includes('graph ') ||
                                extractedContent.includes('flowchart ');

                            const mathBlockRegex = /(\$\$[\s\S]*?\$\$|\$[^$\n]+?\$)/g;
                            const parts = extractedContent.split(mathBlockRegex);

                            newContent = parts.map((part, index) => {
                                // Odd indices are math blocks (captured by regex), even indices are regular text
                                const isMathBlock = index % 2 === 1;

                                if (isMathBlock) {
                                    // In math blocks, only unescape quotes, not backslashes
                                    return part
                                        .replace(/\\'/g, "'")
                                        .replace(/\\"/g, '"');
                                } else {
                                    // In regular text, unescape everything EXCEPT \n in mermaid blocks
                                    let processed = part
                                        .replace(/\\'/g, "'")
                                        .replace(/\\"/g, '"')
                                        .replace(/\\t/g, '\t')
                                        .replace(/\\r/g, '\r')
                                        .replace(/\\\\/g, '\\');

                                    // Only unescape \n if NOT in a mermaid block
                                    if (!isMermaidBlock) {
                                        processed = processed.replace(/\\n/g, '\n');
                                    }

                                    return processed;
                                }
                            }).join('');
                        }

                        currentContent += newContent;
                        const contentSnapshot = currentContent;
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, contentSnapshot);
                            return next;
                        });
                    } else if (op.op === 'add' && op.path.includes('/streamed_output/-')) {
                        // Check for error in messages array - but be careful not to match code examples
                        if (op.value && op.value.messages && Array.isArray(op.value.messages)) {
                            for (const msg of op.value.messages) {
                                if (msg.content && typeof msg.content === 'string') {
                                    // Check if this message contains diff syntax and set the flag
                                    if (!containsDiff && (
                                        msg.content.includes('```diff') || msg.content.includes('diff --git') ||
                                        msg.content.match(/^@@ /m) || msg.content.match(/^\+\+\+ /m) || msg.content.match(/^--- /m))) {
                                        containsDiff = true;
                                        console.log("Detected diff content in message, disabling error detection");
                                    }

                                    // Skip error checking if the message contains tool execution results
                                    const containsCodeBlock = msg.content.includes('```');
                                    const containsToolExecution = msg.content.includes('tool_execution') || msg.content.includes('⟩') || msg.content.includes('⟨');
                                    const errorResponse = (containsCodeBlock || containsDiff || containsToolExecution) ? null : extractErrorFromSSE(msg.content);

                                    if (errorResponse) {
                                        console.log("Error detected in message content:", errorResponse);
                                        const isPartialResponse = currentContent.length > 0;
                                        const errorMessage = isPartialResponse
                                            ? `${errorResponse.detail || 'An error occurred'} (Partial response preserved - ${currentContent.length} characters)`
                                            : errorResponse.detail || 'An error occurred';

                                        showError(errorMessage, conversationId, addMessageToConversation, isPartialResponse ? 'warning' : 'error');
                                        errorOccurred = true;

                                        // Preserve partial content before removing stream
                                        if (currentContent && currentContent.trim()) {
                                            const partialMessage: Message = {
                                                role: 'assistant',
                                                content: currentContent + '\n\n[Response interrupted: ' + (errorResponse.detail || 'An error occurred') + ']',
                                                _timestamp: Date.now()
                                            };
                                            addMessageToConversation(partialMessage, conversationId, !isStreamingToCurrentConversation);
                                        }

                                        // Clean up
                                        setIsStreaming(false);
                                        removeStreamingConversation(conversationId);
                                        setStreamedContentMap((prev: Map<string, string>) => new Map(prev));
                                        break;
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (error) {
                const e = error as Error;
                console.error('Error parsing JSON chunk:', { error: e, rawData: data });
                console.error('Error parsing JSON:', e);

                // FALLBACK: Try simple JSON.parse for basic content chunks
                try {
                    const simpleJson = JSON.parse(data);
                    console.log('Fallback JSON parse succeeded:', simpleJson);

                    // Handle simple content objects
                    if (simpleJson.content) {
                        console.log('Processing fallback content:', simpleJson.content);
                        currentContent += simpleJson.content;
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });
                    }
                } catch (fallbackError) {
                    console.warn('Fallback JSON parse also failed:', fallbackError);
                    console.warn('Lost content chunk:', data);
                }
            }
        };

        const readStream = async () => {
            // Metrics collection for debugging
            const metrics = {
                chunks_received: 0,
                bytes_received: 0,
                chunk_sizes: [] as number[],
                start_time: Date.now()
            };

            try {
                while (true) {
                    let chunk = '';
                    try {
                        if (signal.aborted) {
                            console.log("Stream aborted by user");
                            errorOccurred = true;
                            removeStreamingConversation(conversationId);
                            setIsStreaming(false);
                            return 'Response generation stopped by user.';
                        }
                        const { done, value } = await reader.read();
                        if (done) {
                            console.log("Stream read complete (done=true)");
                            // If the stream was aborted, don't process the final content
                            if (isAborted) {
                                console.log("Stream was aborted, discarding final content");
                                removeStreamingConversation(conversationId);
                                return 'Response generation stopped by user.';
                            }
                            break;
                        }
                        if (errorOccurred) {
                            console.log("Stream read aborted due to error");
                            break;
                        }
                        chunk = decoder.decode(value, { stream: true });

                        // Track metrics
                        metrics.chunks_received++;
                        metrics.bytes_received += chunk.length;
                        metrics.chunk_sizes.push(chunk.length);

                        if (!chunk) {
                            // Check if the stream was aborted during processing
                            if (isAborted) {
                                console.log("Stream was aborted during processing, discarding chunk");
                                removeStreamingConversation(conversationId);
                                setIsStreaming(false);
                                return 'Response generation stopped by user.';
                            }
                            console.log("Empty chunk received, continuing");
                            continue;
                        }

                        // Check if this chunk contains diff syntax and set the flag
                        if (!containsDiff && (
                            chunk.includes('```diff') || chunk.includes('diff --git') ||
                            chunk.match(/^@@ /m) || chunk.match(/^\+\+\+ /m) || chunk.match(/^--- /m))) {
                            containsDiff = true;
                            console.log("Detected diff content in chunk, disabling error detection");
                        }

                        // Check for errors using our new function - but be careful with code blocks
                        try {
                            // Skip error checking if the chunk contains code blocks or diffs
                            const containsCodeBlock = chunk.includes('```');

                            // Don't pre-emptively check for nested errors here in the chunk reader
                            // Let processChunk() → processSingleDataMessage() → extractErrorFromSSE() handle all errors
                            // This ensures authentication errors go through the existing error handling pipeline
                        } catch (error) {
                            console.warn("Error checking for nested errors:", error);
                        }

                        processChunk(chunk);
                    } catch (error) {
                        console.error('❌ Error reading stream:', error);
                        console.error('Error type:', (error as any)?.constructor?.name);
                        console.error('Error message:', (error as any)?.message);
                        console.error('Error stack:', (error as any)?.stack);
                        console.error('Last chunk before error:', chunk?.substring(0, 200));

                        // Save partial content before aborting
                        if (currentContent && currentContent.trim()) {
                            const partialMessage: Message = {
                                role: 'assistant',
                                content: currentContent + '\n\n[Stream interrupted - partial response saved]',
                                _timestamp: Date.now()
                            };
                            addMessageToConversation(partialMessage, conversationId, !isStreamingToCurrentConversation);
                            console.log('💾 Saved partial content on abort:', currentContent.length, 'characters');
                            showError(`Stream interrupted. Saved ${currentContent.length} characters of partial response.`, conversationId, addMessageToConversation, 'warning');
                        } else {
                            message.error('Stream reading error. Check JS console for details.');
                        }

                        errorOccurred = true;
                        removeStreamingConversation(conversationId);
                        setIsStreaming(false);
                        break;
                    }
                }
            } catch (error) {
                console.error('🔥 Outer catch - error type:', (error as any)?.constructor?.name, 'message:', (error as any)?.message);
                if (error instanceof DOMException && error.name === 'AbortError') return '';
                console.error('Unhandled Stream error in readStream:', { error });
                removeStreamingConversation(conversationId);
                setIsStreaming(false);
                throw error;
            } finally {
                // Flush any remaining bytes in the decoder
                try {
                    const finalChunk = decoder.decode();
                    if (finalChunk) {
                        console.log('🔍 FINAL_CHUNK_DEBUG: Processing final chunk from decoder:', finalChunk.substring(0, 100));
                        processChunk(finalChunk);
                    }

                    // Process any remaining buffered message
                    if (buffer.trim()) {
                        console.log('🔍 BUFFER_FLUSH_DEBUG: Processing remaining buffer:', buffer.substring(0, 100));
                        processChunk('');  // This will process the final buffer content
                    }

                    // SAFETY NET: If no content was streamed and no error was shown, check for missed errors
                    if (!currentContent && !errorOccurred && !errorAlreadyDisplayed) {
                        console.log('🚨 SAFETY_NET: No content and no error shown, checking for missed errors');

                        // Check if there's an error in the buffer that was never processed
                        if (buffer.includes('"error"')) {
                            console.log('🚨 RECOVERED_ERROR: Found error in unprocessed buffer');
                            // Try to extract meaningful error text from buffer
                            let missedError = null;
                            try {
                                const bufferJson = JSON.parse(buffer);
                                missedError = {
                                    error: bufferJson.error_type || 'unknown_error',
                                    detail: bufferJson.error || bufferJson.content || bufferJson.detail || 'An error occurred',
                                    status_code: bufferJson.status_code || 500
                                };
                            } catch (e) {
                                missedError = extractErrorFromSSE('data: ' + buffer);
                            }
                            if (missedError) {
                                console.log('🚨 DISPLAYING_RECOVERED_ERROR:', missedError);
                                showError(missedError.detail || 'An error occurred', conversationId, addMessageToConversation, 'error');
                                errorOccurred = true;
                            }
                        }
                    }
                } catch (error) {
                    console.warn("Error flushing decoder:", error);
                }

                // Log final streaming metrics
                console.log('📊 Final streaming metrics:', {
                    total_chunks: metrics.chunks_received,
                    total_bytes: metrics.bytes_received,
                    avg_chunk_size: (metrics.bytes_received / metrics.chunks_received).toFixed(2),
                    min_chunk: Math.min(...metrics.chunk_sizes),
                    max_chunk: Math.max(...metrics.chunk_sizes),
                    chunks_under_10: metrics.chunk_sizes.filter(s => s < 10).length,
                    duration_ms: Date.now() - metrics.start_time,
                    content_length: currentContent.length,
                    content_vs_bytes_ratio: (currentContent.length / metrics.bytes_received * 100).toFixed(1) + '%'
                });

                setIsStreaming(false);
                return !errorOccurred && currentContent ? currentContent : '';
            }
        }

        try {
            console.log("Starting stream read...");
            const result = await readStream();
            // After successful streaming, update with final content
            if (currentContent && !errorOccurred) {
                console.log("Stream completed successfully");

                // Check if the stream was aborted before adding the message
                if (isAborted) {
                    console.log("Stream was aborted, not adding final message");
                    removeStreamingConversation(conversationId);
                    return 'Response generation stopped by user.';
                }
                // Check if the content is an error message using our new function
                // Skip error checking if the content contains tool execution results
                const containsCodeBlock = currentContent.includes('```');
                const containsToolExecution = currentContent.includes('tool_execution') || currentContent.includes('⟩') || currentContent.includes('⟨');
                const errorResponse = (containsCodeBlock || containsDiff || containsToolExecution) ? null : extractErrorFromSSE(currentContent);

                if (errorResponse) {
                    // Handle recoverable errors after substantial content
                    const hasSubstantialContent = currentContent.length > 1000;
                    const isRecoverableError = (
                        errorResponse.error === 'timeout' ||
                        errorResponse.detail?.includes('timeout') ||
                        errorResponse.detail?.includes('ReadTimeoutError') ||
                        errorResponse.detail?.includes('Read timeout')
                    );

                    if (hasSubstantialContent && isRecoverableError) {
                        console.log('Final content check - recoverable error after substantial content, preserving');
                        // Don't treat this as an error, just complete normally
                        // The content will be added to conversation below
                    } else {
                        console.log("Error detected in final content:", errorResponse);

                        const isPartialResponse = currentContent.length > 0;

                        // Dispatch preserved content event before showing error and removing stream
                        if (isPartialResponse) {
                            document.dispatchEvent(new CustomEvent('preservedContent', {
                                detail: {
                                    existing_streamed_content: currentContent,
                                    error_detail: errorResponse.detail || 'An error occurred during processing'
                                }
                            }));
                        }

                        const errorMessage = isPartialResponse
                            ? `${errorResponse.detail} (Partial response preserved - ${currentContent.length} characters)`
                            : errorResponse.detail || 'An error occurred';
                        showError(errorMessage, conversationId, addMessageToConversation, isPartialResponse ? 'warning' : 'error');
                        errorOccurred = true;
                        removeStreamingConversation(conversationId);

                        // Still return the partial content so it can be used
                        return currentContent || '';
                    }
                }

                // Even if we detect an error in the final content, save what we have
                if (errorOccurred && currentContent && currentContent.trim()) {
                    const partialMessage: Message = {
                        role: 'assistant',
                        content: currentContent,
                        _timestamp: Date.now()
                    };

                    const isNonCurrentConversation = !isStreamingToCurrentConversation;
                    addMessageToConversation(partialMessage, conversationId, isNonCurrentConversation);
                    removeStreamingConversation(conversationId);
                    console.log('Saved partial content on error:', currentContent.length, 'characters');
                    return currentContent;
                }

                // Add message to conversation history for context, but keep displaying via StreamedContent
                const aiMessage: Message = {
                    role: 'assistant',
                    content: currentContent,
                    _timestamp: Date.now()
                };

                // Add to conversation history and remove from streaming
                const isNonCurrentConversation = !isStreamingToCurrentConversation;
                addMessageToConversation(aiMessage, conversationId, isNonCurrentConversation);
                removeStreamingConversation(conversationId);

            }
            return result;

        } catch (error) {
            // Type guard for DOMException
            if (error instanceof DOMException && error.name === 'AbortError') {
                console.log('Request was aborted');
                return 'Response generation stopped by user.';
            }
            if (error instanceof DOMException && error.name === 'AbortError') return '';

            console.error('Stream processing error in readStream catch block:', { error });
            removeStreamingConversation(conversationId);
            setIsStreaming(false);
            throw error;
        } finally {
            setIsStreaming(false);
        }
    } catch (error) {
        console.error('Error in sendPayload:', error);
        // Check for abort error
        if (error instanceof DOMException && error.name === 'AbortError') {
            return 'Response generation stopped by user.';
        }
        // Type guard for Error objects
        if (error instanceof Error) {
            console.error('Error details:', {
                name: error.name,
                message: error.message,
                stack: error.stack
            });
            message.error(`Error: ${error.message}`);
        }
        if (eventSource && typeof eventSource.close === 'function') eventSource.close();
        // Clear streaming state
        setIsStreaming(false);
        removeStreamingConversation(conversationId);
        throw error;
    } finally {
        if (eventSource && typeof eventSource.close === 'function') eventSource.close();
        document.removeEventListener('abortStream', abortListener as EventListener);
        setIsStreaming(false);
        removeStreamingConversation(conversationId);

        // Only disconnect WebSocket if we're truly done and not just switching conversations
        // The WebSocket should persist across multiple requests in the same conversation
    }

    return !errorOccurred && currentContent ? currentContent : '';
};


export async function fetchConfig(): Promise<AppConfig> {
    try {
        const response = await fetch('/api/config');
        if (!response.ok) {
            throw new Error('Failed to fetch config');
        }
        const config = await response.json();
        return { ...DEFAULT_CONFIG, ...config };
    } catch (error) {
        console.warn('Failed to fetch config, using defaults:', error);
        return DEFAULT_CONFIG;
    }
};

/* Unused helper functions - kept for future use
// Helper functions for sequential thinking tool
function handleSequentialThinkingStart(
    jsonData: any,
    currentContent: string,
    conversationId: string,
    setStreamedContentMap: React.Dispatch<React.SetStateAction<Map<string, string>>>
): void {
    // Extract the actual thinking content from the args
    const toolStart = jsonData.tool_start || jsonData;
    const thinkingContent = toolStart.args?.thought || '';
    const thoughtNumber = toolStart.args?.thoughtNumber || 1;
    const totalThoughts = toolStart.args?.totalThoughts || 1;
 
    if (thinkingContent) {
        // Escape any code fences in the thinking content to prevent breaking the outer fence
        const escapedContent = thinkingContent.replace(/```/g, '\\`\\`\\`');
        
        // Create a thinking block display
        const thinkingDisplay = `\n\`\`\`thinking:step-${thoughtNumber}\n🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n${escapedContent}\n\`\`\`\n\n`;
 
        currentContent += thinkingDisplay;
 
        // Update the streamed content map
        setStreamedContentMap((prev: Map<string, string>) => {
            const next = new Map(prev);
            next.set(conversationId, currentContent);
            return next;
        });
 
        console.log('🤔 THINKING_START: Added thinking content for step', thoughtNumber);
    }
}
 
function handleSequentialThinkingDisplay(
    jsonData: any,
    currentContent: string,
    toolInputsMap: Map<string, any>,
    conversationId: string,
    setStreamedContentMap: React.Dispatch<React.SetStateAction<Map<string, string>>>
): void {
    try {
        // Get the stored input from tool_start to access the actual thought content
        const toolId = jsonData.tool_id;
        const storedInput = toolInputsMap.get(toolId);
        const thinkingContent = storedInput?.thought || '';
        const thoughtNumber = storedInput?.thoughtNumber || 1;
        const totalThoughts = storedInput?.totalThoughts || 1;
        const nextThoughtNeeded = storedInput?.nextThoughtNeeded;
 
        // Parse the result to get completion status
        const toolResult = jsonData.tool_result || jsonData;
        const result = typeof toolResult.result === 'string' ?
            JSON.parse(toolResult.result) : toolResult.result;
        const isComplete = result.nextThoughtNeeded === false;
 
        if (thinkingContent) {
            // Escape any code fences in the thinking content to prevent breaking the outer fence
            const escapedContent = thinkingContent.replace(/```/g, '\\`\\`\\`');
            
            // Replace the "Running" indicator with the actual thinking content
            const toolStartPrefix = `\\cp_sequentialthinking\n⏳ Running:`;
            const toolStartSuffix = `\n\`\`\`\n\n`;
            const lastStartIndex = currentContent.lastIndexOf(toolStartPrefix);
 
            const thinkingDisplay = `\n\`\`\`thinking:step-${thoughtNumber}\n🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n${escapedContent}\n\n${nextThoughtNeeded ? '_Continuing to next thought..._' : '_Thinking complete._'}\n\`\`\`\n\n`;
 
            if (lastStartIndex !== -1) {
                const blockEndIndex = currentContent.indexOf(toolStartSuffix, lastStartIndex);
                if (blockEndIndex !== -1) {
                    currentContent = currentContent.substring(0, lastStartIndex) + thinkingDisplay + currentContent.substring(blockEndIndex + toolStartSuffix.length);
                } else {
                    currentContent += thinkingDisplay;
                }
            } else {
                currentContent += thinkingDisplay;
            }
 
            // Update the streamed content map
            setStreamedContentMap((prev: Map<string, string>) => {
                const next = new Map(prev);
                next.set(conversationId, currentContent);
                return next;
            });
 
            console.log('🤔 THINKING_DISPLAY: Updated thinking content for step', thoughtNumber, 'content length:', thinkingContent.length);
        } else {
            console.warn('🤔 THINKING_DISPLAY: No thinking content found for tool ID:', toolId);
        }
 
    } catch (e) {
        console.error('Error handling sequential thinking display:', e);
    }
}
*/

async function getApiResponse(messages: any[], question: string, checkedItems: string[], conversationId: string, signal?: AbortSignal) {
    const messageTuples: string[][] = [];

    // Messages are already filtered in SendChatContainer, no need to filter again
    for (const message of messages) {
        // Include images if present
        if (message.images && message.images.length > 0) {
            messageTuples.push([
                message.role,
                message.content,
                JSON.stringify(message.images)
            ]);
        } else {
            messageTuples.push([message.role, message.content]);
        }
    }

    // Debug log the conversation ID being sent
    console.log('🔍 API: Sending conversation_id to server:', conversationId);
    console.log('🖼️ API: Messages with images:', messages.filter(m => m.images?.length > 0).length);

    const payload = {
        messages: messageTuples,
        question,
        conversation_id: conversationId,
        files: checkedItems
    };

    return fetch('/api/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            // conversation_id is now in the payload body
            // But also add it to headers for error middleware
            'X-Conversation-Id': conversationId || ''
        },
        body: JSON.stringify(payload),
        signal
    });
}

/**
 * Restart stream with enhanced context by adding missing files
 */
export const restartStreamWithEnhancedContext = async (
    conversationId: string,
    addedFiles: string[],
    currentFiles: string[] = []
): Promise<void> => {
    console.log('🔄 CONTEXT_ENHANCEMENT: Restarting stream with enhanced context:', { conversationId, addedFiles, currentFiles });

    try {
        // Send restart request to backend which will handle the streaming
        const response = await fetch('/api/restart-stream-with-context', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream'
            },
            body: JSON.stringify({
                conversation_id: conversationId,
                added_files: addedFiles,
                current_files: currentFiles
            })
        });

        if (!response.ok) {
            throw new Error(`Failed to restart stream: ${response.status} ${response.statusText}`);
        }

        console.log('🔄 CONTEXT_ENHANCEMENT: Stream restart request sent successfully');

        // The response will be a new stream that the existing handlers will pick up
        // No need to handle the stream here - the existing chatApi will handle it

    } catch (error) {
        console.error('Error restarting stream with enhanced context:', error);
        throw error;
    }
};

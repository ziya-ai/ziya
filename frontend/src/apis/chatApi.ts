import { SetStateAction, Dispatch } from 'react';
import { message } from 'antd';
import { Message } from '../utils/types';
import { formatMCPOutput } from '../utils/mcpFormatter';
import { handleToolStart, handleToolDisplay, ToolEventContext } from '../utils/mcpToolHandlers';

// WebSocket for real-time feedback
class FeedbackWebSocket {
    private ws: WebSocket | null = null;
    private conversationId: string | null = null;
    private connectionPromise: Promise<void> | null = null;

    connect(conversationId: string): Promise<void> {
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
                console.log('🔄 FEEDBACK: WebSocket connected');
                resolve();
            };

            this.ws.onerror = (error) => {
                clearTimeout(connectionTimeout);
                console.error('🔄 FEEDBACK: WebSocket error:', error);
                console.error('🔄 FEEDBACK: WebSocket URL was:', wsUrl);
                console.error('🔄 FEEDBACK: WebSocket readyState:', this.ws?.readyState);
                reject(error);
            };

            this.ws.onclose = () => {
                clearTimeout(connectionTimeout);
                console.log('🔄 FEEDBACK: WebSocket closed');
            };
        });

        return this.connectionPromise;
    }

    sendFeedback(toolId: string, feedback: string) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'tool_feedback',
                tool_id: toolId,
                message: feedback
            }));
            console.log('🔄 FEEDBACK: Sent feedback:', feedback);
        } else {
            console.error('🔄 FEEDBACK: Cannot send feedback - WebSocket not ready. State:', this.ws?.readyState);
            // Fallback: Log that feedback would have been sent
            console.log('🔄 FEEDBACK: Would have sent feedback (WebSocket unavailable):', feedback);
        }
    }

    disconnect() {
        this.ws?.close();
        this.ws = null;
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

            // Check for throttling errors - but only in error-formatted messages
            // Check for authentication errors in plain text
            if (dataContent.includes('mwinit') ||
                (dataContent.includes('credential') && dataContent.includes('error')) ||
                dataContent.includes('authentication') ||
                dataContent.includes('AWS credentials have expired')) {
                return {
                    error: 'auth_error',
                    detail: 'AWS credentials have expired. Please run mwinit to authenticate and try again.',
                    status_code: 401
                };
            }

            // Be much more strict about what constitutes a throttling error
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
                if (data.error === 'authentication_error' || 
                    (data.error && data.content && typeof data.content === 'string' && 
                     (data.content.includes('mwinit') || data.content.includes('credentials') || 
                      data.content.includes('Authentication failed')))) {
                    return {
                        error: data.error || 'authentication_error',
                        detail: data.content || data.detail || 'Authentication failed',
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
    if (errorDetail.length > 100) {
        // Show inline as a collapsible message
        const errorMessage: Message = {
            role: 'system',
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
    } else {
        // Show as popup for short messages
        if (messageType === 'error') {
            message.error(errorDetail);
        } else {
            message.warning(errorDetail);
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
    setReasoningContentMap?: Dispatch<SetStateAction<Map<string, string>>>
): Promise<string> => {
    let eventSource: any = null;
    let currentContent = '';
    let containsDiff = false;
    let errorOccurred = false;
    let toolInputsMap = new Map<string, any>(); // Store tool inputs by tool ID
    let originalRequestParams = { messages, question, checkedItems, conversationId }; // Store for retry
    let activeFeedbackToolId: string | null = null;

    // Connect feedback WebSocket
    let feedbackConnected = false;
    try {
        console.log('🔄 FEEDBACK: Attempting to connect WebSocket for conversation:', conversationId);
        await feedbackWebSocket.connect(conversationId);
        console.log('🔄 FEEDBACK: WebSocket connected successfully');

        // Notify components that WebSocket is ready
        (window as any).feedbackWebSocketReady = true;
        feedbackConnected = true;
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

            // Disconnect feedback WebSocket only if we connected it
            if (feedbackConnected) {
                (window as any).feedbackWebSocketReady = false;
            }
            feedbackWebSocket.disconnect();
        }
    };
    document.addEventListener('abortStream', abortListener as EventListener);

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
            console.log('🔧 processChunk called with chunk length:', chunk.length);
            // Add chunk to buffer
            buffer += chunk;

            // Split by double newlines to get complete SSE messages
            const messages = buffer.split('\n\n');
            console.log('📬 Split into', messages.length, 'messages, buffer remaining:', buffer.length);

            // Keep the last potentially incomplete message in buffer
            buffer = messages.pop() || '';

            // Process complete messages
            for (const sseMessage of messages) {
                if (!sseMessage.trim()) continue;

                console.log('📨 Processing SSE message, length:', sseMessage.length, 'starts with:', sseMessage.substring(0, 50));

                // Check if it's an SSE data line
                if (sseMessage.startsWith('data:')) {
                    let dataContent = sseMessage.slice(5).trim();
                    console.log('📊 SSE data extracted, length:', dataContent.length, 'content:', dataContent.substring(0, 100));

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
            // Skip heartbeat messages entirely
            if (data.includes('"heartbeat": true') || data.includes('"type": "heartbeat"')) {
                console.log('📊 SSE: Skipping heartbeat message');
                return;
            }

            // Check if this chunk contains diff syntax and set the flag
            if (!containsDiff && (
                data.includes('```diff') || data.includes('diff --git') ||
                data.match(/^@@ /) || data.match(/^\+\+\+ /) || data.match(/^--- /))) {
                containsDiff = true;
                console.log("Detected diff content, disabling error detection");
            }

            // Check if this is a hunk status update
            try {
                const jsonData = JSON.parse(data);
                if (jsonData.request_id && jsonData.details && jsonData.details.hunk_statuses) {
                    // Dispatch a custom event with the hunk status update
                    window.dispatchEvent(new CustomEvent('hunkStatusUpdate', {
                        detail: {
                            requestId: jsonData.request_id,
                            hunkStatuses: jsonData.details.hunk_statuses
                        }
                    }));
                }
            } catch (e) {
                // Not JSON or not a hunk status update, ignore
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

            const errorResponse = (containsCodeBlock || containsDiff || containsToolExecution) ? null : extractErrorFromSSE(data);

            // NEW LOGIC: Distinguish between fatal and recoverable errors
            const hasSubstantialContent = currentContent.length > 1000; // More than 1KB of content
            const isRecoverableError = errorResponse && (
                errorResponse.error === 'timeout' ||
                errorResponse.detail?.includes('timeout') ||
                errorResponse.detail?.includes('ReadTimeoutError') ||
                errorResponse.detail?.includes('Read timeout') ||
                (errorResponse.error === 'stream_error' && hasSubstantialContent)
            );

            if (errorResponse) {
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
                showError(errorMessage, targetConversationId, addMessageToConversation, currentContent.length > 0 ? 'warning' : 'error');
                errorOccurred = true;

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
                return;
            }

            // Skip [DONE] marker
            if (data.trim() === '[DONE]') {
                return;
            }

            try {
                // Parse the JSON data
                const jsonData = JSON.parse(data);

                // Unwrap tool_start and tool_result if they're wrapped
                let unwrappedData = jsonData;
                if (jsonData.tool_start) {
                    console.log('🔧 Processing wrapped tool_start - this is legitimate tool data, not an error');
                    unwrappedData = jsonData.tool_start;
                } else if (jsonData.tool_result) {
                    console.log('🔧 Processing wrapped tool_result - this is legitimate tool data, not an error');

                    // Tool results should never be processed by error detection
                    // even if they contain error-related keywords in their content.
                    // This prevents legitimate tool output (source code, command output, etc.)
                    // from being misinterpreted as actual API errors.

                    unwrappedData = jsonData.tool_result;
                    unwrappedData.type = 'tool_display'; // Normalize to tool_display
                }

                // Process the JSON object
                if (unwrappedData.heartbeat) {
                    console.log("Received heartbeat, skipping");
                    return;
                }

                // Handle done marker
                if (unwrappedData.done) {
                    console.log("Received done marker in JSON data");
                    // Don't return here - let the stream complete naturally
                    // The done marker just indicates no more content chunks
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
                    
                    // Attach click handler after React renders the button
                    setTimeout(() => {
                        const retryButton = document.querySelector(`[data-conversation-id="${conversationId}"].throttle-retry-button`) as HTMLButtonElement;
                        if (retryButton && !retryButton.dataset.handlerAttached) {
                            retryButton.dataset.handlerAttached = 'true';
                            retryButton.onclick = async () => {
                                console.log('🔄 RETRY: User clicked retry button after throttling');
                                
                                // Disable the button during retry
                                retryButton.disabled = true;
                                retryButton.textContent = '⏳ Waiting...';
                                
                                // Wait the suggested time
                                const waitTime = parseInt(retryButton.dataset.throttleWait || '60', 10);
                                message.info(`Waiting ${waitTime} seconds before retry...`, waitTime);
                                await new Promise(resolve => setTimeout(resolve, waitTime * 1000));
                                
                                // Retry by calling sendPayload recursively with original parameters
                                message.info('Retrying request...');
                                await sendPayload(originalRequestParams.messages, originalRequestParams.question, 
                                    originalRequestParams.checkedItems, originalRequestParams.conversationId,
                                    streamedContentMap, setStreamedContentMap, setIsStreaming, 
                                    removeStreamingConversation, addMessageToConversation, isStreamingToCurrentConversation, setProcessingState, setReasoningContentMap);
                            };
                        }
                    }, 100);
                    
                    // Don't return - let the stream complete naturally to save content
                }

                // SIMPLIFIED CONTENT PROCESSING - Single path for all content
                let contentToAdd = '';

                // Check for rewind markers in accumulated content first
                if (currentContent.includes('<!-- REWIND_MARKER:')) {
                    const rewindMatch = currentContent.match(/<!-- REWIND_MARKER: (\d+)(?:\|PARTIAL:([^-]*))? -->/);
                    if (rewindMatch) {
                        const partialContent = rewindMatch[2] || '';
                        console.log(`🔄 REWIND: Detected marker in accumulated content with partial: "${partialContent}"`);
                        console.log(`🔄 REWIND: Trimming last incomplete line and continuing`);

                        // Remove everything from the rewind marker onwards
                        const lines = currentContent.split('\n');
                        const markerIndex = lines.findIndex(line => line.includes('<!-- REWIND_MARKER:'));
                        if (markerIndex >= 0) {
                            const beforeRewind = lines.slice(0, markerIndex).join('\n');
                            currentContent = beforeRewind;
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
                    const rewindMatch = jsonData.content.match(/<!-- REWIND_MARKER: (\d+)/);
                    if (rewindMatch) {
                        const rewindLine = parseInt(rewindMatch[1], 10);
                        console.log(`🔄 REWIND: Detected marker at line ${rewindLine}`);

                        // Rewind to the specified line number
                        const lines = currentContent.split('\n');
                        currentContent = lines.slice(0, rewindLine).join('\n');

                        console.log(`🔄 REWIND: Rewound to line ${rewindLine}, waiting for continuation chunks`);
                        // Update the map immediately to reflect the rewound content
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });

                        // Skip this chunk - continuation will come in separate chunks
                        return;
                    }
                }

                // Handle continuation rewind markers
                if (jsonData.type === 'continuation_rewind') {
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
                    console.log('Adding content chunk:', contentToAdd.substring(0, 50) + '...');
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
                    activeFeedbackToolId = feedbackData.tool_id;
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
                    // Use stored header from tool_start to ensure matching
                    const displayHeader = storedHeader || unwrappedData.display_header || toolName.replace('mcp_', '').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());

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
                    // Use unwrappedData.args if available, fallback to storedInput
                    const inputForFormatter = unwrappedData.args || storedInput;
                    const formatted = formatMCPOutput(toolName, displayContent, inputForFormatter, {
                        showInput: false,
                        maxLength: 10000,
                        defaultCollapsed: true
                    });

                    console.log('🔧 FORMATTED CONTENT:', formatted.content.substring(0, 200));

                    // Create tool display with header
                    const toolResultDisplay = `\n\`\`\`tool:${toolName}|${displayHeader}\n${formatted.content}\n\`\`\`\n\n`;
                    const toolStartPrefix = `\`\`\`tool:${toolName}|${displayHeader}\n`;

                    console.log('🔧 TOOL_RESULT: Looking for prefix:', toolStartPrefix);
                    console.log('🔧 TOOL_RESULT: Current content length:', currentContent.length);
                    const lastStartIndex = currentContent.lastIndexOf(toolStartPrefix);
                    console.log('🔧 TOOL_RESULT: Found at index:', lastStartIndex);
                    if (lastStartIndex !== -1) {
                        const blockEndIndex = currentContent.indexOf('\n```\n\n', lastStartIndex);
                        console.log('🔧 TOOL_RESULT: Block end at:', blockEndIndex);
                        if (blockEndIndex !== -1) {
                            // Replace from the character before the block (to preserve spacing) through the end
                            const replaceStart = lastStartIndex > 0 && currentContent[lastStartIndex - 1] === '\n' ? lastStartIndex - 1 : lastStartIndex;
                            currentContent = currentContent.substring(0, replaceStart) + toolResultDisplay + currentContent.substring(blockEndIndex + 6);
                            console.log('🔧 TOOL_RESULT: Replaced tool block');
                        } else {
                            currentContent += toolResultDisplay;
                            console.log('🔧 TOOL_RESULT: No block end found, appending');
                        }
                    } else {
                        console.log('🔧 TOOL_RESULT: Prefix not found, appending');
                        currentContent += toolResultDisplay;
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
                    const displayHeader = unwrappedData.display_header || toolName.replace('mcp_', '').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());
                    const inputArgs = unwrappedData.args || unwrappedData.input || {};

                    // Generate tool start display
                    let toolStartDisplay;

                    if (actualToolName === 'run_shell_command' && inputArgs.command) {
                        // For shell: show command on first line
                        toolStartDisplay = `\n\`\`\`tool:${toolName}|${displayHeader}\n$ ${inputArgs.command}\n⏳ Running...\n\`\`\`\n\n`;
                    } else if (actualToolName === 'get_current_time') {
                        toolStartDisplay = `\n\`\`\`tool:${toolName}|${displayHeader}\n⏳ Getting current time...\n\`\`\`\n\n`;
                    } else {
                        toolStartDisplay = `\n\`\`\`tool:${toolName}|${displayHeader}\n⏳ Running...\n\`\`\`\n\n`;
                    }

                    console.log('🔧 TOOL_START formatted:', toolStartDisplay);
                    currentContent += toolStartDisplay;
                setStreamedContentMap((prev: Map<string, string>) => {
                    const next = new Map(prev);
                    next.set(conversationId, currentContent);
                    return next;
                });
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
                        // More robust extraction for various formats:
                        // content='text' additional_kwargs={} response_metadata={}
                        // content="text" additional_kwargs={} response_metadata={}

                        let extractedContent = '';

                        // Try single quotes first
                        let match = newContent.match(/content='([^']*(?:\\.[^']*)*)'(?:\s+additional_kwargs=.*)?$/);
                        if (match) {
                            extractedContent = match[1];
                        } else {
                            // Try double quotes
                            match = newContent.match(/content="([^"]*(?:\\.[^"]*)*)"(?:\s+additional_kwargs=.*)?$/);
                            if (match) {
                                extractedContent = match[1];
                            } else {
                                // Fallback: extract anything between quotes after content=
                                match = newContent.match(/content=['"]([^'"]*)['"]/);
                                if (match) {
                                    extractedContent = match[1];
                                } else {
                                    // Last resort: use original content
                                    extractedContent = newContent;
                                }
                            }
                        }

                        // Unescape common escape sequences
                        newContent = extractedContent
                            .replace(/\\'/g, "'")
                            .replace(/\\"/g, '"')
                            .replace(/\\n/g, '\n')
                            .replace(/\\t/g, '\t')
                            .replace(/\\r/g, '\r')
                            .replace(/\\\\/g, '\\');
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
                    console.log('⏳ Waiting for next chunk from reader.read()...');
                    const { done, value } = await reader.read();
                    console.log('📨 Received from reader:', { done, valueLength: value?.length });
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
                    console.log('🔤 Decoded chunk length:', chunk.length);

                    // Track metrics
                    metrics.chunks_received++;
                    metrics.bytes_received += chunk.length;
                    metrics.chunk_sizes.push(chunk.length);

                    if (metrics.chunks_received % 100 === 0) {
                        console.log('📊 Streaming metrics:', {
                            chunks: metrics.chunks_received,
                            bytes: metrics.bytes_received,
                            avg_chunk: (metrics.bytes_received / metrics.chunks_received).toFixed(2),
                            elapsed_ms: Date.now() - metrics.start_time
                        });
                    }

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

                        if (!containsCodeBlock && !containsDiff) {
                            // Check for nested errors in LangChain ops structure
                            const nestedError = extractErrorFromNestedOps(chunk);
                            if (nestedError) {
                                // Handle recoverable errors after substantial content
                                const hasSubstantialContent = currentContent.length > 1000;
                                const isRecoverableError = (
                                    nestedError.error === 'timeout' ||
                                    nestedError.detail?.includes('timeout') ||
                                    nestedError.detail?.includes('ReadTimeoutError') ||
                                    nestedError.detail?.includes('Read timeout')
                                );

                                if (hasSubstantialContent && isRecoverableError) {
                                    console.log('Recoverable nested error after substantial content - preserving:', currentContent.length, 'characters');
                                    showError(`${nestedError.detail} (${Math.round(currentContent.length / 1000)}KB content preserved)`, conversationId, addMessageToConversation, 'warning');
                                }

                                console.log("Nested error detected in ops structure:", nestedError);

                                // Define isPartialResponse for this scope
                                const isPartialResponse = currentContent.length > 0;
                                const errorMessage = nestedError.detail || 'An error occurred';

                                // Dispatch preserved content event before showing error
                                if (isPartialResponse) {
                                    document.dispatchEvent(new CustomEvent('preservedContent', {
                                        detail: {
                                            existing_streamed_content: currentContent,
                                            error_detail: nestedError.detail || 'An error occurred during processing'
                                        }
                                    }));
                                    // Don't remove streaming conversation here - let preserved content handler do it
                                } else if (currentContent && currentContent.length > 0) {
                                    // Save partial content even without the preserved content event
                                    const partialMessage: Message = {
                                        role: 'assistant',
                                        content: currentContent + '\n\n[Response interrupted: ' + (nestedError.detail || 'An error occurred during processing') + ']',
                                        _timestamp: Date.now()
                                    };
                                    addMessageToConversation(partialMessage, conversationId, !isStreamingToCurrentConversation);
                                    console.log('Saved partial content directly:', currentContent.length, 'characters');
                                    removeStreamingConversation(conversationId);
                                }
                                else {
                                    // No partial content to save
                                    removeStreamingConversation(conversationId);
                                }

                                const messageType = isPartialResponse ? 'warning' : 'error';
                                showError(errorMessage, conversationId, addMessageToConversation, messageType);
                                errorOccurred = true;
                                break;
                            }
                        }
                    } catch (error) {
                        console.warn("Error checking for nested errors:", error);
                    }

                    console.log('📦 Processing chunk, length:', chunk.length, 'first 100 chars:', chunk.substring(0, 100));
                    processChunk(chunk);
                    console.log('✅ Chunk processed successfully');
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
                    processChunk(finalChunk);
                }

                // Process any remaining buffered message
                if (buffer.trim()) {
                    processChunk('');  // This will process the final buffer content
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
        // Create a thinking block display
        const thinkingDisplay = `\n\`\`\`thinking:step-${thoughtNumber}\n🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n${thinkingContent}\n\`\`\`\n\n`;

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
            // Replace the "Running" indicator with the actual thinking content
            const toolStartPrefix = `\\cp_sequentialthinking\n⏳ Running:`;
            const toolStartSuffix = `\n\`\`\`\n\n`;
            const lastStartIndex = currentContent.lastIndexOf(toolStartPrefix);

            const thinkingDisplay = `\n\`\`\`thinking:step-${thoughtNumber}\n🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n${thinkingContent}\n\n${nextThoughtNeeded ? '_Continuing to next thought..._' : '_Thinking complete._'}\n\`\`\`\n\n`;

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
        messageTuples.push([message.role, message.content]);
    }

    // Debug log the conversation ID being sent
    console.log('🔍 API: Sending conversation_id to server:', conversationId);

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

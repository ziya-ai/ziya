import { SetStateAction, Dispatch } from 'react';
import { message } from 'antd';
import { Message } from '../utils/types';

type ProcessingState = 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error';

interface ErrorResponse {
    error: string;
    detail: string;
    event?: string;
    status_code?: number;
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

    // Check for validation errors first (these are critical and should be shown)
    if (content.includes('ValidationException') && content.includes('Input is too long')) {
        return {
            error: 'context_size_error',
            detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.',
            status_code: 413
        };
    }
    
    // Check for throttling errors
    if (content.includes('ThrottlingException') || content.includes('Too many requests')) {
        return {
            error: 'throttling_error',
            detail: 'Too many requests to AWS Bedrock. Please wait a moment before trying again.',
            status_code: 429
        };
    }

    // Check for other validation errors
    try {
        // Check for JSON error format
        if (content.includes('"error"') || content.includes('"detail"')) {
            try {
                const parsed = JSON.parse(content);
                if (parsed.error || parsed.detail) {
                    return {
                        error: parsed.error || 'Unknown error',
                        detail: parsed.detail || parsed.error || 'An error occurred',
                        status_code: parsed.status_code
                    };
                }
            } catch (e) {
                // Not valid JSON, continue with other checks
            }
        }

        // Check for validation exception patterns in plain text
        if (content.includes('ValidationException') || content.includes('Input is too long')) {
            return {
                error: 'validation_error',
                detail: 'The request contains invalid data or is too large for the model.'
            };
        }

        // Check for specific error patterns - only at the beginning of lines
        // and not within code blocks
        const hasErrorPattern = content.match(/^Error:/m) || content.match(/^error:/m);
        if (hasErrorPattern) {
            // Check if this pattern is inside a code block
            const codeBlockRegex = /```[\s\S]*?(?:^Error:|^error:)[\s\S]*?```/m;
            const isInCodeBlock = codeBlockRegex.test(content);

            // Only treat as error if not in a code block
            if (!isInCodeBlock) {
                const errorMatch = content.match(/(?:^|\n)(?:Error|error):\s*(.+?)(?:\n|$)/);
                if (errorMatch && errorMatch[1]) {
                    return {
                        error: 'Error detected',
                        detail: errorMatch[1].trim()
                    };
                }
            }
        }

        // Check for "An error occurred" pattern - but not in code blocks
        if (content.toLowerCase().includes('an error occurred')) {
            // Check if this is likely part of code or documentation
            const isInCodeOrExample = /```[\s\S]*?an error occurred[\s\S]*?```/i.test(content) ||
                /`an error occurred`/i.test(content) ||
                /example.*?an error occurred/i.test(content);

            if (!isInCodeOrExample) {
                return {
                    error: 'Error detected',
                    detail: 'An error occurred during processing'
                };
            }
        }

        return null;
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
        if (chunk.includes('ValidationException') && chunk.includes('Input is too long')) {
            return {
                error: 'context_size_error',
                detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.',
                status_code: 413
            };
        }
        
        const jsonMatches = chunk.match(/(\{.*?\})/g);
        if (!jsonMatches) return null;

        for (const jsonStr of jsonMatches) {
            try {
                const data = JSON.parse(jsonStr);

                // Check for direct error properties
                if (data.error || data.detail) {
                    return {
                        error: data.error || 'Unknown error',
                        detail: data.detail || data.error || 'An error occurred',
                        status_code: data.status_code
                    };
                }

                // Check for ops array with errors
                if (data.ops && Array.isArray(data.ops)) {
                    for (const op of data.ops) {
                        if (op.value && typeof op.value === 'object') {
                            // Check for error in value
                            if (op.value.error || op.value.detail) {
                                return {
                                    error: op.value.error || 'Unknown error',
                                    detail: op.value.detail || op.value.error || 'An error occurred',
                                    status_code: op.value.status_code
                                };
                            }

                            // Check for error in messages array - but be careful not to match code examples
                            if (op.value.messages && Array.isArray(op.value.messages)) {
                                for (const msg of op.value.messages) {
                                    // Check for validation errors in message content
                                    if (msg.content && typeof msg.content === 'string' && 
                                        msg.content.includes('ValidationException') && 
                                        msg.content.includes('Input is too long')) {
                                        return { error: 'context_size_error', detail: 'The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.', status_code: 413 };
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

export const sendPayload = async (
    messages: Message[],
    question: string,
    checkedItems: string[],
    conversationId: string,
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>,
    setIsStreaming: Dispatch<SetStateAction<boolean>>,
    removeStreamingConversation: (id: string) => void,
    addMessageToConversation: (message: Message, conversationId: string, isNonCurrentConversation?: boolean) => void,
    isStreamingToCurrentConversation: boolean = true,
    setProcessingState?: (state: ProcessingState) => void
): Promise<string> => {
    let eventSource: any = null;
    let currentContent = '';
    let errorOccurred = false;
    let containsDiff = false;  // Flag to track if content contains diff blocks

    // Create an AbortController to handle cancellation
    const abortController = new AbortController();
    const { signal } = abortController;

    let isAborted = false;

    // Remove any existing listeners for this conversation ID to prevent duplicates
    document.removeEventListener('abortStream', window[`abortListener_${conversationId}`]);

    // Set up abort event listener
    const abortListener = (event: CustomEvent) => {
        if (event.detail.conversationId === conversationId) {
            console.log(`Aborting stream for conversation: ${conversationId}`);
            abortController.abort();
            isAborted = true;

            console.log('Sending abort notification to server');
            // Also notify the server about the abort
            try {
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

        // Process chunks as they arrive
        function processChunk(chunk: string) {
            // Split the chunk by newlines to handle multiple SSE events
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (!line.trim()) continue;

                // Check if it's an SSE data line
                if (line.startsWith('data:')) {
                    const data = line.slice(5).trim();

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

                    // Check for errors using our new function - but be careful not to match code examples
                    // Skip error checking if the data looks like it contains code blocks or diffs
                    const containsCodeBlock = data.includes('```');
                    const errorResponse = (containsCodeBlock || containsDiff) ? null : extractErrorFromSSE(data);

                    if (errorResponse) {
                        console.log("Current content when error detected:", currentContent.substring(0, 200) + "...");
                        console.log("Current content length:", currentContent.length);
                        console.log("Error detected in SSE data:", errorResponse);
                        
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
                        const isPartialResponse = currentContent.length > 0;
                        const errorMessage = isPartialResponse
                            ? `${errorResponse.detail} (Partial response preserved - ${currentContent.length} characters)`
                            : errorResponse.detail || 'An error occurred';

                        message.warning({
                            content: errorMessage,
                            duration: isPartialResponse ? 15 : 10,
                            key: 'stream-error'
                        });
                        errorOccurred = true;
                        // Don't remove streaming conversation yet - let the preserved content handler do it
                        // removeStreamingConversation(conversationId);
                        return;
                    }

                    try {
                        const jsonData = JSON.parse(data);

                        // Handle done marker
                        if (jsonData.done) {
                            console.log("Received done marker in JSON data");
                            return;
                        }

                        // Skip heartbeat messages
                        if (jsonData.heartbeat) {
                            console.log("Received heartbeat, skipping");
                            continue;
                        }

                        // Extract text content from the response
                        if (jsonData.text) {
                            currentContent += jsonData.text;
                            setStreamedContentMap((prev: Map<string, string>) => {
                                const next = new Map(prev);
                                next.set(conversationId, currentContent);
                                return next;
                            });
                        } else if (jsonData.content) {
                            currentContent += jsonData.content;
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
                                continue;
                            }
                            if (op.op === 'add' && op.path.endsWith('/streamed_output_str/-')) {
                                const newContent = op.value || '';
                                if (!newContent) continue;
                                currentContent += newContent;
                                setStreamedContentMap((prev: Map<string, string>) => {
                                    const next = new Map(prev);
                                    next.set(conversationId, currentContent);
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

                                            // Skip error checking if the message contains code blocks or diffs
                                            const containsCodeBlock = msg.content.includes('```');
                                            const errorResponse = (containsCodeBlock || containsDiff) ? null : extractErrorFromSSE(msg.content);

                                            if (errorResponse) {
                                                console.log("Error detected in message content:", errorResponse);
                                                message.error({
                                                    content: errorResponse.detail || 'An error occurred',
                                                    duration: 10,
                                                    key: 'stream-error'
                                                });
                                                errorOccurred = true;
                                                removeStreamingConversation(conversationId);
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
                    }
                }
            }
        }

        async function readStream() {
            try {
                while (true) {
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
                        const chunk = decoder.decode(value);
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
                                    console.log("Nested error detected in ops structure:", nestedError);

                                    // Show different message for partial responses vs complete failures
                                    const isPartialResponse = currentContent.length > 0;
                                    const errorMessage = isPartialResponse
                                        ? `${nestedError.detail} (Partial response preserved - ${currentContent.length} characters)`
                                        : nestedError.detail || 'An error occurred';

                                    message.warning({
                                        content: errorMessage,
                                        duration: isPartialResponse ? 15 : 10,
                                        key: 'stream-error'
                                    });
                                    errorOccurred = true;
                                    removeStreamingConversation(conversationId);
                                    break;
                                }
                            }
                        } catch (error) {
                            console.warn("Error checking for nested errors:", error);
                        }

                        processChunk(chunk);
                    } catch (error) {
                        console.error('Error reading stream:', error);
                        message.error('Stream reading error. Check JS console for details.');
                        errorOccurred = true;
                        removeStreamingConversation(conversationId);
                        setIsStreaming(false);
                        break;
                    }
                }
            } catch (error) {
                if (error instanceof DOMException && error.name === 'AbortError') return '';
                console.error('Unhandled Stream error in readStream:', { error });
                removeStreamingConversation(conversationId);
                setIsStreaming(false);
                throw error;
            } finally {
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
                // Skip error checking if the content contains code blocks or diffs
                const containsCodeBlock = currentContent.includes('```');
                const errorResponse = (containsCodeBlock || containsDiff) ? null : extractErrorFromSSE(currentContent);

                if (errorResponse) {
                    console.log("Error detected in final content:", errorResponse);

                    // Show different message for partial responses vs complete failures
                    const isPartialResponse = currentContent.length > 0;
                    const errorMessage = isPartialResponse
                        ? `${errorResponse.detail} (Partial response preserved - ${currentContent.length} characters)`
                        : errorResponse.detail || 'An error occurred';

                    message.warning({
                        content: errorMessage,
                        duration: isPartialResponse ? 15 : 10,
                        key: 'stream-error'
                    });
                    errorOccurred = true;
                    removeStreamingConversation(conversationId);
                    return '';
                }

                // Create a message object for the AI response
                const aiMessage: Message = {
                    role: 'assistant',
                    content: currentContent
                };

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
            return !errorOccurred && currentContent ? currentContent : '';

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
        return '';
    }
};

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
            'Content-Type': 'application/json'
            // conversation_id is now in the payload body
        },
        body: JSON.stringify(payload),
        signal
    });
}

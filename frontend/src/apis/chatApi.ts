import { SetStateAction, Dispatch } from 'react';
import { message } from 'antd';
import { Message } from '../utils/types';

type ProcessingState = 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'error';

interface ErrorResponse {
    error: string;
    detail: string;
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

                // Check for direct error properties
                if (data.error || data.detail) {
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
                            // Check for error in value
                            if (op.value.error || op.value.detail) {
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

        // Catch-all for any Exception patterns that weren't handled above
        if (chunk.includes('Exception') &&
            !chunk.includes('tool_execution') &&
            !chunk.includes('```') &&
            chunk.includes('error')) {
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
    setProcessingState?: (state: ProcessingState) => void,
    setReasoningContentMap?: Dispatch<SetStateAction<Map<string, string>>>
): Promise<string> => {
    let eventSource: any = null;
    let currentContent = '';
    let currentThinkingContent = '';
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
            // Add chunk to buffer
            buffer += chunk;
            
            // Split by double newlines to get complete SSE messages
            const messages = buffer.split('\n\n');
            
            // Keep the last potentially incomplete message in buffer
            buffer = messages.pop() || '';
            
            // Process complete messages
            for (const sseMessage of messages) {
                if (!sseMessage.trim()) continue;

                // Check if it's an SSE data line
                if (sseMessage.startsWith('data:')) {
                    const data = sseMessage.slice(5).trim();

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

                    if (errorResponse) {
                        console.log("Current content when error detected:", currentContent.substring(0, 200) + "...");
                        console.log("Current content length:", currentContent.length);
                        console.log("Error detected in SSE data:", errorResponse);

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
                        const isPartialResponse = currentContent.length > 0;
                        const errorMessage = isPartialResponse
                            ? `${errorResponse.detail} (Partial response preserved - ${currentContent.length} characters)`
                            : errorResponse.detail || 'An error occurred';

                        message[isPartialResponse ? 'warning' : 'error']({
                            content: errorMessage,
                            duration: isPartialResponse ? 15 : 10,
                            key: `stream-error-${conversationId}`
                        });
                        errorOccurred = true;

                        // If we have accumulated content, add it to the conversation before removing the stream
                        if (currentContent && currentContent.trim()) {
                            const partialMessage: Message = {
                                role: 'assistant',
                                content: currentContent + '\n\n[Response interrupted: ' + (errorResponse.detail || 'An error occurred') + ']'
                            };
                            addMessageToConversation(partialMessage, conversationId, !isStreamingToCurrentConversation);
                            console.log('Preserved partial content as message:', currentContent.length, 'characters');
                        }

                        // Clean up streaming state
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.delete(conversationId);
                            return next;
                        });
                        return;
                    }

                    // Skip [DONE] marker
                    if (data.trim() === '[DONE]') {
                        continue;
                    }

                    try {
                        // Parse the JSON data
                        const jsonData = JSON.parse(data);
                        
                        // Process the JSON object
                        if (jsonData.heartbeat) {
                            console.log("Received heartbeat, skipping");
                            continue;
                        }

                        // Handle done marker
                        if (jsonData.done) {
                            console.log("Received done marker in JSON data");
                            // Don't return here - let the stream complete naturally
                            // The done marker just indicates no more content chunks
                            continue;
                        }

                        // Handle throttling status messages
                        if (jsonData.type === 'throttling_status') {
                            console.log('Throttling status:', jsonData.message);
                            message.info({
                                content: jsonData.message,
                                duration: jsonData.delay + 1,
                                key: `throttling-${conversationId}`
                            });
                            continue;
                        }

                        // Handle throttling failure
                        if (jsonData.type === 'throttling_failed') {
                            console.log('Throttling failed:', jsonData.message);
                            message.error({
                                content: jsonData.message + ' Click to retry.',
                                duration: 0,
                                key: `throttling-failed-${conversationId}`,
                                onClick: () => {
                                    window.location.reload();
                                }
                            });
                            errorOccurred = true;
                            return;
                        }

                        // SIMPLIFIED CONTENT PROCESSING - Single path for all content
                        let contentToAdd = '';
                        
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

                        // Handle tool events separately (simplified)
                        if (jsonData.tool_start) {
                            const toolData = jsonData.tool_start;
                            console.log('🔧 TOOL_START received:', toolData);
                            
                            let toolName = toolData.tool_name;
                            if (!toolName.startsWith('mcp_')) {
                                toolName = `mcp_${toolName}`;
                            }
                            toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');
                            
                            let inputDisplay = '';
                            if (toolData.args && toolData.args.command) {
                                inputDisplay = `$ ${toolData.args.command}`;
                            }
                            
                            const toolStartDisplay = `\n\`\`\`tool:${toolName}\n⏳ Running: ${inputDisplay}\n\`\`\`\n\n`;
                            currentContent += toolStartDisplay;
                            setStreamedContentMap((prev: Map<string, string>) => {
                                const next = new Map(prev);
                                next.set(conversationId, currentContent);
                                return next;
                            });
                        }

                        if (jsonData.tool_result) {
                            const toolData = jsonData.tool_result;
                            console.log('🔧 TOOL_RESULT received:', toolData);
                            
                            let toolName = toolData.tool_name;
                            if (!toolName.startsWith('mcp_')) {
                                toolName = `mcp_${toolName}`;
                            }
                            toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');
                            
                            const result = toolData.result;
                            const toolResultDisplay = `\n\`\`\`tool:${toolName}\n${result}\n\`\`\`\n\n`;
                            
                            // Improved replacement logic to handle tool_start -> tool_result transition
                            const escapedToolName = toolName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                            
                            // Look for the most recent tool_start block for this tool using string search
                            const toolStartPrefix = `\n\`\`\`tool:${toolName}\n⏳ Running:`;
                            const toolStartSuffix = `\n\`\`\`\n\n`;
                            
                            // Find the last occurrence of this tool's start block
                            const lastStartIndex = currentContent.lastIndexOf(toolStartPrefix);
                            
                            if (lastStartIndex !== -1) {
                                // Find the end of this tool block
                                const blockEndIndex = currentContent.indexOf(toolStartSuffix, lastStartIndex);
                                if (blockEndIndex !== -1) {
                                    // Replace the entire tool_start block with the result
                                    const beforeBlock = currentContent.substring(0, lastStartIndex);
                                    const afterBlock = currentContent.substring(blockEndIndex + toolStartSuffix.length);
                                    currentContent = beforeBlock + toolResultDisplay + afterBlock;
                                    console.log('🔧 Replaced tool_start with result for:', toolName);
                                }
                            } else {
                                // No matching tool_start found, just append the result
                                currentContent += toolResultDisplay;
                                console.log('🔧 Added new tool result (no matching tool_start):', toolName);
                            }
                            
                            setStreamedContentMap((prev: Map<string, string>) => {
                                const next = new Map(prev);
                                next.set(conversationId, currentContent);
                                return next;
                            });
                        } else if (jsonData.type === 'tool_start') {
                            // Handle tool start events - show that a tool is starting
                            console.log('🔧 TOOL_START received:', jsonData);

                            // Normalize tool name - ensure single mcp_ prefix
                            let toolName = jsonData.tool_name;
                            if (!toolName.startsWith('mcp_')) {
                                toolName = `mcp_${toolName}`;
                            }
                            // Remove any double prefixes
                            toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');

                            // Format input for display
                            let inputDisplay = '';
                            if (jsonData.input && Object.keys(jsonData.input).length > 0) {
                                if (jsonData.input.command) {
                                    inputDisplay = jsonData.input.command;
                                } else if (jsonData.input.tool_input) {
                                    inputDisplay = jsonData.input.tool_input;
                                } else {
                                    inputDisplay = JSON.stringify(jsonData.input);
                                }
                            }

                            // Format as tool block that shows the tool is starting
                            let toolStartDisplay;
                            if (toolName === 'mcp_run_shell_command' && jsonData.args && jsonData.args.command) {
                                // For shell commands, show the actual command being executed
                                toolStartDisplay = `\n\`\`\`tool:${toolName}\n⏳ Running: $ ${jsonData.args.command}\n\`\`\`\n\n`;
                            } else if (toolName === 'mcp_run_shell_command' && jsonData.input && typeof jsonData.input === 'object' && jsonData.input.command) {
                                // Handle alternative input format
                                toolStartDisplay = `\n\`\`\`tool:${toolName}\n⏳ Running: $ ${jsonData.input.command}\n\`\`\`\n\n`;
                            }

                        console.log('🔧 TOOL_START formatted:', toolStartDisplay);
                        currentContent += toolStartDisplay;
                        console.log('🔧 CURRENT_CONTENT after tool_start:', currentContent.slice(-200));
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });
                    } else if (jsonData.type === 'tool_start') {
                        // Handle tool start events
                        console.log('🔧 TOOL_START received:', jsonData);

                        // Log timestamp for debugging
                        if (jsonData.timestamp) {
                            console.log(`[${jsonData.timestamp}] TOOL_START: ${jsonData.tool_name}`);
                        }

                        const toolStartContent = `\n\n🔧 **Executing Tool**: \`${jsonData.tool_name}\`\n\n`;
                        currentContent += toolStartContent;
                        setStreamedContentMap((prev: Map<string, string>) => {
                            const next = new Map(prev);
                            next.set(conversationId, currentContent);
                            return next;
                        });
                    } else if (jsonData.type === 'tool_execution') {
                        // Handle structured tool execution using existing ToolBlock syntax
                        console.log('🔧 TOOL_EXECUTION received:', jsonData);
                        const signedIndicator = jsonData.signed ? ' 🔒' : '';

                        // Normalize tool name - ensure single mcp_ prefix

                        // Log timestamp for debugging
                        if (jsonData.timestamp) {
                            console.log(`[${jsonData.timestamp}] TOOL_EXECUTION: ${jsonData.tool_name}`);
                        }
                        let toolName = jsonData.tool_name;
                        if (!toolName.startsWith('mcp_')) {
                            toolName = `mcp_${toolName}`;
                        }
                        // Remove any double prefixes
                        toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');

                        // Extract result - handle both string and object formats
                        let result = jsonData.result;
                        if (typeof result === 'string' && result.startsWith('{')) {
                            try {
                                const parsed = JSON.parse(result);
                                if (parsed.error && parsed.message) {
                                    result = parsed.message;
                                }
                            } catch (e) {
                                // Keep original result if parsing fails
                            }
                        }

                        // Format as tool block that the MarkdownRenderer will recognize and style properly
                        const toolDisplay = `\n\`\`\`tool:${toolName}${signedIndicator}\n${result}\n\`\`\`\n\n`;

                        console.log('🔧 TOOL_DISPLAY formatted:', toolDisplay);

                        // Replace the corresponding tool_start block if it exists
                        const toolStartPattern = new RegExp(`\\n\`\`\`tool:${toolName}\\n⏳ Running: \\$ ([^\\n]+)\\n\`\`\`\\n\\n`, 'g');
                        const toolStartMatch = currentContent.match(toolStartPattern);
                        
                        if (toolStartMatch) {
                            // Extract the command from the match
                            const commandMatch = toolStartMatch[0].match(/⏳ Running: \$ ([^\n]+)/);
                            const command = commandMatch ? commandMatch[1] : '';
                            
                            // Check if result contains an error
                            const isError = result.toLowerCase().includes('error') || 
                                          result.toLowerCase().includes('failed') ||
                                          result.toLowerCase().includes('command not found') ||
                                          result.toLowerCase().includes('permission denied');
                            
                            if (isError && command) {
                                // For errors, preserve the command and add the error result
                                const errorDisplay = `\n\`\`\`tool:${toolName}${signedIndicator}\n⏳ Attempted: $ ${command}\n\n${result}\n\`\`\`\n\n`;
                                currentContent = currentContent.replace(toolStartPattern, errorDisplay);
                                console.log('🔧 TOOL_EXECUTION: Replaced tool_start with error (preserved command)');
                            } else {
                                // For success, replace with just the result
                                currentContent = currentContent.replace(toolStartPattern, toolDisplay);
                                console.log('🔧 TOOL_EXECUTION: Replaced tool_start block with result');
                            }
                        } else {
                            currentContent += toolDisplay;
                            console.log('🔧 TOOL_EXECUTION: Added new tool block (no matching tool_start found)');
                        }

                    console.log('🔧 CURRENT_CONTENT after tool:', currentContent.slice(-200));
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
                        continue;
                    }
                    if (op.op === 'add' && op.path.endsWith('/streamed_output_str/-')) {
                        let newContent = op.value || '';
                        if (!newContent) continue;

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

                                        message[isPartialResponse ? 'warning' : 'error']({
                                            content: errorMessage,
                                            duration: isPartialResponse ? 15 : 10,
                                            key: `stream-error-${conversationId}`
                                        });
                                        errorOccurred = true;

                                        // Preserve partial content before removing stream
                                        if (currentContent && currentContent.trim()) {
                                            const partialMessage: Message = {
                                                role: 'assistant',
                                                content: currentContent + '\n\n[Response interrupted: ' + (errorResponse.detail || 'An error occurred') + ']'
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
        }
    }
        }

const readStream = async () => {
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
                const chunk = decoder.decode(value, { stream: true });
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

                            // Dispatch preserved content event before showing error
                            if (isPartialResponse && currentContent.length > 0) {
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
                                    content: currentContent + '\n\n[Response interrupted: ' + (nestedError.detail || 'An error occurred during processing') + ']'
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
                            message[messageType]({
                                content: errorMessage,
                                duration: isPartialResponse ? 15 : 10,
                                key: `stream-error-${conversationId}`
                            });
                            errorOccurred = true;
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

            const messageType = isPartialResponse ? 'warning' : 'error';
            message[messageType]({
                content: errorMessage,
                duration: isPartialResponse ? 15 : 10,
                key: `stream-error-${conversationId}`
            });
            errorOccurred = true;
            removeStreamingConversation(conversationId);

            // Still return the partial content so it can be used
            return currentContent || '';
        }

        // Even if we detect an error in the final content, save what we have
        if (errorOccurred && currentContent && currentContent.trim()) {
            const partialMessage: Message = {
                role: 'assistant',
                content: currentContent
            };

            const isNonCurrentConversation = !isStreamingToCurrentConversation;
            addMessageToConversation(partialMessage, conversationId, isNonCurrentConversation);
            removeStreamingConversation(conversationId);
            console.log('Saved partial content on error:', currentContent.length, 'characters');
            return currentContent;
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

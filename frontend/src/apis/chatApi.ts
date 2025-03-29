import { SetStateAction, Dispatch } from 'react';
import { createParser } from 'eventsource-parser';
import { message } from 'antd';
import { Message } from '../utils/types';

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
        
        // Check for specific error patterns
        if (content.includes('Error:') || content.includes('error:')) {
            const errorMatch = content.match(/(?:Error|error):\s*(.+?)(?:\n|$)/);
            if (errorMatch && errorMatch[1]) {
                return {
                    error: 'Error detected',
                    detail: errorMatch[1].trim()
                };
            }
        }
        
        // Check for "An error occurred" pattern
        if (content.toLowerCase().includes('an error occurred')) {
            return {
                error: 'Error detected',
                detail: 'An error occurred during processing'
            };
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
                            
                            // Check for error in messages array
                            if (op.value.messages && Array.isArray(op.value.messages)) {
                                for (const msg of op.value.messages) {
                                    if (msg.content && typeof msg.content === 'string') {
                                        const errorResponse = extractErrorFromSSE(msg.content);
                                        if (errorResponse) return errorResponse;
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
    isStreamingToCurrentConversation: boolean = true
): Promise<string> => {
    let eventSource: any = null;
    let currentContent = '';
    let errorOccurred = false;
    
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
        let response = await getApiResponse(messagesToSend, question, checkedItems);
        console.log("Initial API response:", response.status, response.statusText);
        
        if (!response.ok) {
            if (response.status === 503) {
                console.log("Service unavailable, attempting retry");
                await handleStreamError(response);
                // Add a small delay before retrying
                await new Promise(resolve => setTimeout(resolve, 2000));
                // Retry the request once
                let retryResponse = await getApiResponse(messagesToSend, question, checkedItems);
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

        // Set up cleanup on unmount or error
        const cleanup = () => {
            if (eventSource && typeof eventSource.close === 'function') eventSource.close();
            setIsStreaming(false);
        };

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
                    
                    // Check for errors using our new function
                    const errorResponse = extractErrorFromSSE(data);
                    if (errorResponse) {
                        console.log("Error detected in SSE data:", errorResponse);
                        message.error({
                            content: errorResponse.detail || 'An error occurred',
                            duration: 10,
                            key: 'stream-error'
                        });
                        errorOccurred = true;
                        removeStreamingConversation(conversationId);
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
                                // Check for error in messages array
                                if (op.value && op.value.messages && Array.isArray(op.value.messages)) {
                                    for (const msg of op.value.messages) {
                                        if (msg.content && typeof msg.content === 'string') {
                                            const errorResponse = extractErrorFromSSE(msg.content);
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
                        console.error('Error parsing JSON:', e);
                    }
                }
            }
        }

        async function readStream() {
            try {
                while (true) {
                    try {
                        const { done, value } = await reader.read();
                        if (done) {
                            console.log("Stream read complete (done=true)");
                            break;
                        }
                        if (errorOccurred) {
                            console.log("Stream read aborted due to error");
                            break;
                        }
                        const chunk = decoder.decode(value);
                        if (!chunk) {
                            console.log("Empty chunk received, continuing");
                            continue;
                        }
                        
                        // Check for errors using our new function
                        try {
                            // Check for nested errors in LangChain ops structure
                            const nestedError = extractErrorFromNestedOps(chunk);
                            if (nestedError) {
                                console.log("Nested error detected in ops structure:", nestedError);
                                message.error({
                                    content: nestedError.detail || 'An error occurred',
                                    duration: 10,
                                    key: 'stream-error'
                                });
                                errorOccurred = true;
                                removeStreamingConversation(conversationId);
                                break;
                            }
                        } catch (error) {
                            console.warn("Error checking for nested errors:", error);
                        }
                        
                        processChunk(chunk);
                    } catch (error) {
                        console.error('Error reading stream:', error);
                        errorOccurred = true;
                        removeStreamingConversation(conversationId);
                        setIsStreaming(false);
                        break;
                    }
                }
            } catch (error) {
                if (error instanceof DOMException && error.name === 'AbortError') return '';
                console.error('Stream error:', error);
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

                // Check if the content is an error message using our new function
                const errorResponse = extractErrorFromSSE(currentContent);
                if (errorResponse) {
                    console.log("Error detected in final content:", errorResponse);
                    message.error({
                        content: errorResponse.detail || 'An error occurred',
                        duration: 10,
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
            if (error instanceof DOMException && error.name === 'AbortError') return '';
            console.error('Stream error:', error);
            removeStreamingConversation(conversationId);
            setIsStreaming(false);
            throw error;
        } finally {
            setIsStreaming(false);
            return !errorOccurred && currentContent ? currentContent : '';
        }
    } catch (error) {
        console.error('Error in sendPayload:', error);
        // Type guard for Error objects
        if (error instanceof Error) {
            console.error('Error details:', {
                name: error.name,
                message: error.message,
                stack: error.stack
            });
        }
        if (eventSource && typeof eventSource.close === 'function') eventSource.close();
        // Clear streaming state
        setIsStreaming(false);
        removeStreamingConversation(conversationId);
        throw error;
    } finally {
        if (eventSource && typeof eventSource.close === 'function') eventSource.close();
        setIsStreaming(false);
        removeStreamingConversation(conversationId);
        return '';
    }
};

async function getApiResponse(messages: any[], question: string, checkedItems: string[]) {
    const messageTuples: string[][] = [];
    
    for (const message of messages) {
        messageTuples.push([message.role, message.content]);
    }
    
    const payload = {
        messages: messageTuples,
        question,
        files: checkedItems
    };
    
    return fetch('/api/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });
}

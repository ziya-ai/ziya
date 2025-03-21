import { SetStateAction, Dispatch } from 'react';
import { createParser } from 'eventsource-parser';
import { message } from 'antd';
import { Message } from '../utils/types';
import { db } from '../utils/db';


interface Operation {
    op: string;
    path: string;
    value?: string;
}

interface ErrorResponse {
    error: string;
    detail: string;
    event?: string;
    status_code?: number;
}

// Extended interface to support LangChain error format
interface LangChainErrorResponse {
    type: string;
    error: {
        message: string;
        code: string;
        status?: number;
    };
}

const isValidMessage = (message: any) => {
    if (!message || typeof message !== 'object') return false;
    if (!message.content || typeof message.content !== 'string') return false;
    return message.content.trim().length > 0;
};

const cleanMessages = (messages: any[]) => {
    const cleaned = messages
        .filter(isValidMessage)
        .map(msg => ({ ...msg, content: msg.content.trim() }));
    
    // Log the cleaning process for debugging
    console.debug('Message cleaning:', {
        original: messages.length,
        cleaned: cleaned.length,
        removed: messages.length - cleaned.length
    });

    return cleaned;
};

const isStreamingError = (error: any): boolean => {
    return error instanceof Error && (
        error.message.includes('validationException') ||
        error.message.includes('Input is too long')
    );
};

/**
 * Safely parses an SSE message, ensuring it's properly formatted
 * @param rawData The raw SSE message data
 * @returns Parsed JSON object or null if not a valid SSE message
 */
function parseSSEMessage(rawData: string): any {
  // Only process if it's a string
  if (!rawData || typeof rawData !== 'string') {
    return null;
  }
  
  // Check for LangChain error format directly
  if (rawData.includes('"type":"error"') || rawData.includes('"type": "error"')) {
    try {
      // Extract the error object using regex
      const errorMatch = /data: ({.*"type":\s*"error".*})\n\n/.exec(rawData);
      if (errorMatch && errorMatch[1]) {
        return JSON.parse(errorMatch[1]);
      }
    } catch (e) {
      console.warn('Failed to parse LangChain error format:', e);
    }
  }
  
  // Check if this is a properly formatted SSE message
  if (rawData.includes('data: {') || rawData.includes('data: [')) {
    // Extract only lines that start with "data: "
    const dataLines = rawData.split('\n')
      .filter(line => line.trim().startsWith('data: '))
      .map(line => line.trim().substring(6)); // Remove "data: " prefix
    
    if (dataLines.length === 0) {
      console.debug('No data lines found in SSE message');
      return null; // Not a valid SSE message
    }
    
    // Join the data lines and parse as JSON
    try {
      const jsonData = dataLines.join('');
      
      // Special case for [DONE] marker
      if (jsonData === '[DONE]') {
        return '[DONE]';
      }
      
      return JSON.parse(jsonData);
    } catch (e) {
      console.warn('Failed to parse SSE data as JSON:', e);
      return null;
    }
  } else {
    // This might be a direct JSON string, try to parse it directly
    try {
      return JSON.parse(rawData);
    } catch (e) {
      // Not valid JSON either
      return null;
    }
  }
}

/**
 * Extracts error information from a response, but only if it's a valid SSE message
 * @param chunk The response chunk to check for errors
 * @returns Error response object or null if no error found
 */
function extractErrorFromSSE(chunk: string): ErrorResponse | null {
  // First check if it's a valid SSE message
  const parsedSSE = parseSSEMessage(chunk);
  if (!parsedSSE) {
    console.debug('Not a valid SSE message, skipping error extraction');
    
    // Try direct regex extraction for simple error messages
    // This handles cases where the error message is in a simple format
    // that might not be properly parsed by parseSSEMessage
    const directErrorMatch = /data: (\{"error": "[^"]+", "detail": "[^"]+"(?:, "status_code": \d+)?\})\n\n/.exec(chunk);
    if (directErrorMatch) {
      try {
        const errorObj = JSON.parse(directErrorMatch[1]);
        if (errorObj.error && errorObj.detail) {
          console.log('Found direct error in SSE message:', errorObj);
          return errorObj as ErrorResponse;
        }
      } catch (e) {
        console.warn('Failed to parse direct error match:', e);
      }
    }
    
    return null;
  }
  
  // Handle LangChain error format
  if (parsedSSE && typeof parsedSSE === 'object' && parsedSSE.type === 'error' && parsedSSE.error) {
    console.log('Found LangChain error format:', parsedSSE);
    return {
      error: parsedSSE.error.code || 'error',
      detail: parsedSSE.error.message || 'An error occurred',
      status_code: parsedSSE.error.status || 500
    };
  }
  
  // If it's a parsed JSON object with an error field (original format)
  if (parsedSSE && typeof parsedSSE === 'object' && parsedSSE.error) {
    console.log('Found error in SSE message:', parsedSSE);
    return {
      error: parsedSSE.error,
      detail: parsedSSE.detail || 'Unknown error',
      status_code: parsedSSE.status_code || 500
    };
  }
  
  // Only apply regex to valid SSE messages that might contain errors
  // and make sure we're not just seeing error strings in file content
  if (typeof parsedSSE === 'string' && 
      (parsedSSE.includes('"error":') || parsedSSE.includes('"status_code":')) &&
      // Make sure this is an actual error response, not just file content
      parsedSSE.includes('data:')) {
    
    console.log('Attempting to extract error from string SSE payload');
    
    // Apply regex extraction only to valid SSE content
    const errorMatch = /"error":\s*"([^"]+)"/.exec(parsedSSE);
    const detailMatch = /"detail":\s*"([^"]+)"/.exec(parsedSSE);
    
    if (errorMatch && detailMatch) {
      return {
        error: errorMatch[1],
        detail: detailMatch[1],
        status_code: 500 // Default if not specified
      };
    }
  }
  
  return null;
}

/**
 * Extracts error messages from nested LangChain ops structure
 * @param chunk The response chunk to check for nested errors
 * @returns Error response object or null if no error found
 */
function extractErrorFromNestedOps(chunk: string): ErrorResponse | null {
  try {
    // First try to parse the outer JSON structure
    const match = /data: ({.*"ops".*})/s.exec(chunk);
    if (!match || !match[1]) return null;
    
    const parsed = JSON.parse(match[1]);
    
    // Check if this is an ops array
    if (!parsed.ops || !Array.isArray(parsed.ops)) return null;
    
    // Look for final_output entries that might contain error messages
    for (const op of parsed.ops) {
      if (op.op === 'add' && op.path && op.path.includes('/final_output') && op.value) {
        // Check if the value contains an output field with an error message
        if (op.value.output && typeof op.value.output === 'string') {
          // Try to extract error from the nested output
          const errorMatch = /data: ({.*"error".*})/s.exec(op.value.output);
          if (errorMatch && errorMatch[1]) {
            try {
              const errorObj = JSON.parse(errorMatch[1]);
              if (errorObj.error && errorObj.detail) {
                console.log('Found nested error in ops structure:', errorObj);
                return errorObj as ErrorResponse;
              }
            } catch (e) {
              console.warn('Failed to parse nested error:', e);
            }
          }
        }
      }
    }
  } catch (e) {
    console.warn('Failed to extract error from nested ops:', e);
  }
  
  return null;
}

const handleStreamError = async (response: Response): Promise<never> => {
    console.log("Handling stream error:", response.status);
    let errorMessage: string = 'An unknown error occurred';

    // Try to get detailed error message from response
    try {
        const errorData = await response.json();
        errorMessage = errorData.detail || errorMessage;
    } catch (error) {
        // If we can't parse the response, keep the default error message
        console.warn('Could not parse error response:', error);
    }

    // Handle different response status codes
    switch (response.status) {
        case 413:
            console.log("Content too large error detected");
            errorMessage = 'Selected content is too large for the model. Please reduce the number of files.';
            break;
        case 401:
            console.log("Authentication error");
            errorMessage = 'Authentication failed. Please check your credentials.';
            break;
        case 503:
            console.log("Service unavailable");
            errorMessage = 'Service is temporarily unavailable. Please try again in a moment.';
            break;
        default:
            errorMessage = 'An unexpected error occurred. Please try again.';
    }
    // Always throw error to stop the streaming process
    throw new Error(errorMessage);
};

const createEventSource = (url: string, body: any): EventSource => {
    try {
        const params = new URLSearchParams();
        params.append('data', JSON.stringify(body));
        const eventSource = new EventSource(`${url}?${params}`);
        eventSource.onerror = (error) => {
            console.error('EventSource error:', error);
            eventSource.close();
        };
        eventSource.close();
        message.error({
            content: 'Connection to server lost. Please try again.',
            duration: 5
	});
        return eventSource;
    } catch (error) {
        console.error('Error creating EventSource:', error);
        throw error;
    }
};

export const sendPayload = async (
    conversationId: string,
    question: string,
    isStreamingToCurrentConversation: boolean,
    messages: Message[],
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>,
    setIsStreaming: (streaming: boolean) => void,
    checkedItems: string[], 
    addMessageToConversation: (message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => void,
    removeStreamingConversation: (id: string) => void,
    onStreamComplete?: (content: string) => void
) => {
    
    // Log initial state
    console.debug('sendPayload initial state:', {
        messageCount: messages.length,
        messages: messages.map(m => ({
            role: m.role,
            contentPreview: m.content?.substring(0, 50),
            hasContent: Boolean(m.content),
            contentLength: m.content?.length
        })),
        isStreamingToCurrentConversation
    });
 
    let eventSource: EventSource | undefined;
    let hasError = false;
    let currentContent = '';

    // Track message filtering
    const preFilterMessages = isStreamingToCurrentConversation ? messages : messages.slice(0, messages.length - 1);
    console.debug('Pre-filter messages:', {
         original: messages.length,
         filtered: preFilterMessages.length,
         reason: isStreamingToCurrentConversation ? 'streaming' : 'non-streaming'
    });
     
    // Keep all messages but mark which ones are complete pairs
    const messagesToSend = messages.reduce<Message[]>((acc, msg, index) => {
        const next = index + 1 < messages.length ? messages[index + 1] : null;
        const isCompletePair = msg.role === 'human' && next?.role === 'assistant';
        
        acc.push({
            ...msg,
            isComplete: isCompletePair || msg.role === 'assistant'
        });
        
        return acc;
    }, [] as Message[]);
    
    console.debug('Message processing:', {
        original: messages.length,
        processed: messagesToSend.length,
        pairs: messagesToSend.filter(m => m.isComplete).length / 2,
        incomplete: messagesToSend.filter(m => !m.isComplete).length
    });

    try {
        console.log('Messages received in sendPayload:', messagesToSend.map(m => ({
            role: m.role,
            content: m.content.substring(0, 50)
        })));

        // Log any message filtering that happens
        if (messagesToSend.length !== messages.length) {
            console.debug('Message count changed:', {
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
            if (eventSource) eventSource.close();
            setIsStreaming(false);
            removeStreamingConversation(conversationId);
        };

        console.log("Setting up stream reader for response");
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let errorOccurred = false;
        const parser = createParser(onParse);

        function onParse(event) {
            console.log("SSE event received:", {
                type: event.type,
                data: typeof event.data === 'string' ? event.data.substring(0, 200) + '...' : event.data,
                lastEventId: event.lastEventId,
                origin: event.origin
            });

            if (event.type === 'event') {
                try {
                    console.log("Attempting to parse SSE data");
                    
                    // Check for errors using our new function
                    const errorResponse = extractErrorFromSSE(event.data);
                    if (errorResponse) {
                        console.log("Error detected in SSE message:", errorResponse);
                        message.error({
                            content: errorResponse.detail || 'An error occurred',
                            duration: 10,
                            key: 'stream-error'
                        });
                        errorOccurred = true;
                        removeStreamingConversation(conversationId);
                        return;
                    }
                    
                    const data = JSON.parse(event.data);
                    console.log("Successfully parsed SSE data:", data);
                    
                    // Check for errors in either direct response or nested in operations
                    let errorData: ErrorResponse | null = null;

                    if (data.error || data.event === 'error') {
                        console.log("Direct error found in SSE data:", data);
                        errorData = data;
                        message.error({
                            content: data.detail || 'An error occurred',
                            duration: 10,
                            key: 'stream-error'
                        });
                        errorOccurred = true;
                        removeStreamingConversation(conversationId);
                        return;
                    }

                    // Check for content in AIMessageChunk format
                    if (data.content) {
                        console.log("Found content in AIMessageChunk format:", {
                            contentType: typeof data.content,
                            preview: typeof data.content === 'string' ? data.content.substring(0, 200) : data.content,
                            fullContent: data.content,
                            length: typeof data.content === 'string' ? data.content.length : 'N/A'
                        });
                        try {
                            const contentData = JSON.parse(data.content);
                            console.log("Successfully parsed AIMessageChunk content:", contentData);
                            if (contentData.error) {
                                console.log("Error found in AIMessageChunk content:", contentData);
                                message.error({
                                    content: contentData.detail || 'An error occurred',
                                    duration: 10,
                                    key: 'stream-error'
                                });
                                errorOccurred = true;
                                removeStreamingConversation(conversationId);
                                return;
                            }
                        } catch (error) {
                            const e = error as Error;
                            const positionMatch = e.message.match(/position (\d+)/);
                            const position = positionMatch ? parseInt(positionMatch[1]) : null;
                            
                            console.log("AIMessageChunk parse error:", {
                                error: e,
                                message: e.message,
                                position: position,
                                charsAroundError: position !== null ? 
                                    data.content.substring(
                                        Math.max(0, position - 10),
                                        position + 10
                                    ) : null
                            });
                        }
                    }
                    
                    if (data.ops && Array.isArray(data.ops)) {
                        console.log("Processing ops array:", data.ops);
                        for (const op of data.ops) {
                            if (op.op === 'add') {

                                // Check for streamed output path specifically
                                if (op.path === '/streamed_output/-') {
                                    console.log("Found direct streamed output:", {
                                        valueType: typeof op.value,
                                        valuePreview: typeof op.value === 'string' ? op.value.substring(0, 200) : JSON.stringify(op.value).substring(0, 200),
                                        fullValue: op.value
                                    });
                                    
                                    // Check if it's a string that might contain an error
                                    if (typeof op.value === 'string') {
                                        // Use our new function to check for errors
                                        const errorResponse = extractErrorFromSSE(op.value);
                                        if (errorResponse) {
                                            console.log("Error detected in streamed output:", errorResponse);
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

                                // Check for error in output property
                                if (op.value && typeof op.value === 'object' && 'output' in op.value) {
                                    if (typeof op.value.output === 'string') {
                                        try {
                                            // Use our new function to check for errors
                                            const errorResponse = extractErrorFromSSE(op.value.output);
                                            if (errorResponse) {
                                                console.log("Error detected in operation output:", errorResponse);
                                                message.error({
                                                    content: errorResponse.detail || 'An error occurred',
                                                    duration: 10,
                                                    key: 'stream-error'
                                                });
                                                errorOccurred = true;
                                                removeStreamingConversation(conversationId);
                                                break;
                                            }
                                        } catch (error) {
                                            console.warn("Error checking operation output:", error);
                                        }
                                    } else if (typeof op.value.output === 'object' && op.value.output !== null) {
                                        // Handle case where output is an object
                                        console.log("Operation output is an object:", op.value.output);
                                        try {
                                            if (op.value.output.error) {
                                                console.log("Error found in operation output object:", op.value.output);
                                                message.error({
                                                    content: op.value.output.detail || 'An error occurred',
                                                    duration: 10,
                                                    key: 'stream-error'
                                                });
                                                errorOccurred = true;
                                                removeStreamingConversation(conversationId);
                                                break;
                                            }
                                        } catch (error) {
                                            console.warn("Error checking object output:", error);
                                        }
                                    }
                                } else if (op.path.includes('final_output')) {
                                    // Special handling for timestamp values that cause errors
                                    try {
                                        // Check if value is a timestamp string (common error case)
                                        if (op.value && typeof op.value === 'string' && 
                                            op.value.match(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/)) {
                                            console.log("Skipping timestamp value:", op.value);
                                            continue;
                                        }
                                        
                                        // Check if we have an object with output property
                                        if (op.value && typeof op.value === 'object' && 'output' in op.value) {
                                            if (typeof op.value.output !== 'string') {
                                                console.log("Non-string output in final_output:", op.value.output);
                                                // Don't try to use substring on non-string outputs
                                                continue;
                                            }
                                        }
                                    } catch (error) {
                                        console.warn("Error handling timestamp or final output:", error);
                                    }
                                }
                            }
                        }
                    }

                    // Process operations if present
                    const ops = data.ops || [];
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
                        } else {
                            continue;
                        }
                    }
                } catch (error) {
                    const e = error as Error;
                    console.error('Error parsing JSON:', e);
                }
            }
        }

        async function readStream() {
            let streamClosed = false;
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
                        
                        // Direct check for simple SSE error messages in the standard format
                        const directErrorMatch = /data: (\{"error": "[^"]+", "detail": "[^"]+", "status_code": \d+\})\n\n/.exec(chunk);
                        if (directErrorMatch) {
                            try {
                                const errorObj = JSON.parse(directErrorMatch[1]);
                                console.log("Direct error message detected:", errorObj);
                                message.error({
                                    content: errorObj.detail || 'An error occurred',
                                    duration: 10,
                                    key: 'stream-error'
                                });
                                errorOccurred = true;
                                removeStreamingConversation(conversationId);
                                break;
                            } catch (parseError) {
                                console.warn("Failed to parse direct error message:", parseError);
                            }
                        }
                        
                        // First check if this is a proper SSE message with data: prefix
                        if (chunk.includes('data: {')) {
                            const errorResponse = extractErrorFromSSE(chunk);
                            if (errorResponse) {
                                console.log("Error detected in raw chunk:", errorResponse);
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
                        
                        // Check for auth error specifically - use more precise detection
                        if ((chunk.includes('"error": "auth_error"') || 
                            chunk.includes('"error":"auth_error"')) &&
                            // Make sure it's in a proper SSE data format to avoid false positives
                            chunk.includes('data: {')) {
                            console.log("Auth error detected in chunk");
                            message.error({
                                content: "AWS credentials have expired. Please refresh your credentials.",
                                duration: 10,
                                key: 'stream-error'
                            });
                            errorOccurred = true;
                            removeStreamingConversation(conversationId);
                            break;
                        }
                    } catch (error) {
                        console.warn("Error checking chunk for errors:", error);
                    }
                    
                    // Feed the chunk to the SSE parser
                    parser.feed(chunk);
                } catch (error) {
                    console.error('Error reading stream:', error);
                    errorOccurred = true;
                    removeStreamingConversation(conversationId);
                    setIsStreaming(false);
                    break;
                }
            }
        }

        try {
            console.log("Starting stream read...");
            await readStream();
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
                    return '';
                }

                onStreamComplete?.(currentContent);
                // Add AI response to conversation
                const aiMessage: Message = {
                    role: 'assistant',
                    content: currentContent
                };

                const isNonCurrentConversation = !isStreamingToCurrentConversation;
                addMessageToConversation(aiMessage, conversationId, isNonCurrentConversation);
                removeStreamingConversation(conversationId);
            }
        } catch (error) {
            // Type guard for DOMException
            if (error instanceof DOMException && error.name === 'AbortError') return;
            console.error('Stream error:', error);
            removeStreamingConversation(conversationId);
            setIsStreaming(false);
            hasError = true;
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
        if (eventSource && eventSource instanceof EventSource) eventSource.close();
        // Clear streaming state
        setIsStreaming(false);
        removeStreamingConversation(conversationId);
        // Show error message if not already shown
        if (error instanceof Error) {
            // Let the server's error message through
            const errorMsg = error.message;
            if (!errorMsg.includes('AWS credential error')) {
                message.error({ content: errorMsg, key: 'stream-error', duration: 10 });
            }
        }
    } finally {
        if (eventSource && eventSource instanceof EventSource) eventSource.close();
        setIsStreaming(false);
        removeStreamingConversation(conversationId);
        return '';
    }
};

async function getApiResponse(messages: any[], question: string, checkedItems: string[]) {
    const messageTuples: string[][] = [];
    
    // Validate that we have files selected
    console.log('API Request File Selection:', {
	endpoint: '/ziya/stream_log',
        checkedItemsCount: checkedItems.length,
        checkedItems,
        sampleFile: checkedItems[0],
        hasD3Renderer: checkedItems.includes('frontend/src/components/D3Renderer.tsx')
    });

    // Log specific file paths we're interested in
    console.log('Looking for specific files:', {
        d3Path: 'frontend/src/components/D3Renderer.tsx',
        checkedItems,
        sampleFile: checkedItems[0]
    });

    console.log('Messages received in getApiResponse:', messages.map(m => ({
        role: m.role,
        content: m.content.substring(0, 50)
    })));

    // Build pairs of human messages and AI responses
    try {
        // If this is the first message, we won't have any pairs yet
        if (messages.length === 1 && messages[0].role === 'human') {
            console.log('First message in conversation, no history to send');
	        console.log('Selected files being sent to server:', checkedItems);
	        const response = await fetch('/ziya/stream_log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    input: {
                        chat_history: [],
                        question: question,
		    	        config: {
                            files: checkedItems,
                            // Add explicit file list for debugging
                            fileList: checkedItems.join(', ')
                        },
                    },
                }),
            });
            if (!response.ok) {
                throw await handleStreamError(response);
            }
            return response;
        }
        
                // Log message state before processing
                console.debug('getApiResponse processing:', {
                    inputMessages: messages.length,
                    messageTypes: messages.map(m => m.role),
                    hasQuestion: Boolean(question)
                });
 
        // For subsequent messages, build the history pairs
        const validMessages = messages.filter(msg => {
            const isValid = (msg?.content?.trim() &&
                            (msg.isComplete || msg.role === 'human'));
            if (!isValid) {
                console.debug('Invalid message:', { role: msg?.role, content: msg?.content });
            }
            return isValid;
        });

          // Log filtering results
          console.debug('Message validation results:', {
            before: messages.length,
            after: validMessages.length,
            invalidMessages: messages.filter(msg => !msg?.content?.trim()).map(m => m.role)
        });

        console.log('Messages before filtering:', {
            total: messages.length,
            valid: validMessages.length,
            invalid: messages.length - validMessages.length
        });

        console.log('Valid messages:', validMessages.map(m => ({
                role: m.role,
                content: m.content.substring(0, 50)
        })));

        // Build pairs from completed exchanges
        for (let i = 0; i < validMessages.length; i++) {
            const current = validMessages[i];
            const next = validMessages[i + 1];
            
            // Log each message being processed
            console.log('Processing message:', {
                index: i,
                role: current?.role,
                contentLength: current?.content?.length,
                nextRole: next?.role
            });

            // Only add complete human-assistant pairs
            if (current?.role === 'human' && next?.role === 'assistant') {
                messageTuples.push([current.content, next.content]);
                console.log('Added pair:', {
                    human: current.content.substring(0, 50),
                    ai: next.content.substring(0, 50),
                    humanRole: current.role,
                    aiRole: next.role
                });
                i++; // Skip the next message since we've used it
            } else {
                console.log('Skipping unpaired message:', {
                    currentRole: current?.role,
                    nextRole: next?.role
                });
            }

        }

        console.log('Chat history pairs:', messageTuples.length);
        console.log('Current question:', question);
        console.log('Full chat history:', messageTuples);

        const payload = {
            input: {
                chat_history: messageTuples,
                question: question,
                config: {
                    files: checkedItems,
                    // Add explicit file list for debugging
                    fileList: checkedItems.join(', ')
                },
            },
        };

        console.log('Sending payload to server:', JSON.stringify(payload, null, 2));
        const response = await fetch('/ziya/stream_log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            throw await handleStreamError(response);
        }
        return response;

    } catch (error) {
        console.error('API request failed:', error);
        throw error;
    }
}

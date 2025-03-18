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
	eventSource = undefined;
	let hasError = false;

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
        
	let currentContent = '';
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
                                console.log("Processing add operation:", {
                                    path: op.path,
                                    valueType: typeof op.value,
                                    hasOutput: op.value && 'output' in op.value,
                                    outputPreview: op.value?.output ? op.value.output.substring(0, 200) : 'none',
                                    fullValue: op.value
                                });

                                // Check for streamed output path specifically
                                if (op.path === '/streamed_output/-') {
                                    console.log("Found direct streamed output:", {
                                        valueType: typeof op.value,
                                        valuePreview: typeof op.value === 'string' ? op.value.substring(0, 200) : JSON.stringify(op.value).substring(0, 200),
                                        fullValue: op.value
                                    });
                                    
                                    // Check if it's a string that might contain an error
                                    if (typeof op.value === 'string') {
                                        if (op.value.includes('"error":') || op.value.includes('"validation_error"')) {
                                            console.log("Detected potential error in streamed output string");
                                            
                                            try {
                                                const errorData = JSON.parse(op.value);
                                                console.log("Successfully parsed streamed output as JSON:", errorData);
                                                
                                                if (errorData && errorData.error) {
                                                    console.log("Confirmed error in streamed output:", errorData);
                                                    message.error({
                                                        content: errorData.detail || 'An error occurred',
                                                        duration: 10,
                                                        key: 'stream-error'
                                                    });
                                                    errorOccurred = true;
                                                    removeStreamingConversation(conversationId);
                                                    break;
                                                }
                                            } catch (error) {
                                                const e = error as Error;
                                                const positionMatch = e.message.match(/position (\d+)/);
                                                const position = positionMatch ? parseInt(positionMatch[1]) : null;
                                                
                                                console.log("Streamed output parse error:", {
                                                    error: e,
                                                    message: e.message,
                                                    position: position,
                                                    charsAroundError: position !== null ? 
                                                        op.value.substring(
                                                            Math.max(0, position - 10),
                                                            position + 10
                                                        ) : null
                                                });
                                                
                                                // Try regex-based extraction
                                                const detailMatch = op.value.match(/"detail":\s*"([^"]+)"/);
                                                if (detailMatch && detailMatch[1]) {
                                                    console.log("Extracted error message using regex:", detailMatch[1]);
                                                    message.error({
                                                        content: detailMatch[1],
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

                                // Check for error in output property
                                if (op.value && typeof op.value.output === 'string') {
                                    try {
                                        const outputData = JSON.parse(op.value.output);
                                        console.log("Successfully parsed operation output:", outputData);
                                        if (outputData && typeof outputData === 'object' && 'error' in outputData) {
                                            console.log("Error found in operation output:", outputData);
                                            message.error({
                                                content: outputData.detail || 'An error occurred',
                                                duration: 10,
                                                key: 'stream-error'
                                            });
                                            errorOccurred = true;
                                            removeStreamingConversation(conversationId);
                                            break;
                                        }
                                    } catch (error) {
                                        const e = error as Error;
                                        const positionMatch = e.message.match(/position (\d+)/);
                                        const position = positionMatch ? parseInt(positionMatch[1]) : null;
                                        
                                        console.log("Operation output parse error:", {
                                            error: e,
                                            message: e.message,
                                            position: position,
                                            valuePreview: op.value.output.substring(0, 200),
                                            valueLength: op.value.output.length,
                                            charsAroundError: position !== null ? 
                                                op.value.output.substring(
                                                    Math.max(0, position - 10),
                                                    position + 10
                                                ) : null
                                        });

                                        // Try regex-based error extraction as fallback
                                        if (op.value.output.includes('"error":') || 
                                            op.value.output.includes('"validation_error"')) {
                                            const detailMatch = op.value.output.match(/"detail":\s*"([^"]+)"/);
                                            if (detailMatch && detailMatch[1]) {
                                                console.log("Extracted error message using regex:", detailMatch[1]);
                                                message.error({
                                                    content: detailMatch[1],
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
                    
                    console.log("Raw chunk received:", {
                        length: chunk.length,
                        preview: chunk.substring(0, 100) + (chunk.length > 100 ? '...' : '')
                    });
                    
                    // Try to parse the chunk as JSON to check for validation error
                    try {
                        const jsonData = JSON.parse(chunk);
                        console.log("Successfully parsed chunk as JSON:", jsonData);
                        
                        if (jsonData.error === 'validation_error') {
                            console.log("Validation error detected in chunk:", jsonData);
                            const response = new Response(chunk, { status: 413 });
                            try {
                                throw await handleStreamError(response);
                            } catch (error) {
                                console.error("Error handling validation error:", error);
                                errorOccurred = true;
                                removeStreamingConversation(conversationId);
                                throw error;
                            }
                            break;
                        }
                    } catch (error) {
                        console.log("Chunk is not valid JSON, treating as SSE");
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

                // Check if the content is an error message
                try {
                    const errorData = JSON.parse(currentContent);
                    console.log("Parsed final content:", errorData);
                    if (errorData.error === 'validation_error') {
                        message.error({
                            content: errorData.detail,
                            duration: 10
                        });
                        return '';
                    }
                } catch (e) {} // Not JSON or not an error

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

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
    return messages
        .filter(isValidMessage)
        .map(msg => ({ ...msg, content: msg.content.trim() }));
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
    } catch (e) {
        // If we can't parse the response, keep the default error message
        console.warn('Could not parse error response:', e);
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
    let eventSource: EventSource | undefined;
    const messagesToSend = isStreamingToCurrentConversation ? messages : messages.slice(0, messages.length -1);

    try {
	eventSource = undefined;
	let hasError = false;

	console.log('Messages received in sendPayload:', messagesToSend.map(m => ({
            role: m.role,
            content: m.content.substring(0, 50)
        })));

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
            if (event.type === 'event') {
		// Received SSE Event
                try {
                    const data = JSON.parse(event.data);
		    // Check for errors in either direct response or nested in operations
		    let errorData: ErrorResponse | null = null;

                    if (data.error || data.event === 'error') {
                        errorData = data;
                    }
                    if (data.ops && Array.isArray(data.ops)) {
                        for (const op of data.ops) {
                            if (op.op === 'add' && op.value && typeof op.value.output === 'string') {
                                try {
                                    const outputData = JSON.parse(op.value.output);
				    if (outputData && typeof outputData === 'object' && 'error' in outputData) {
					const response = new Response(JSON.stringify(outputData), {
					    status: outputData.error === 'validation_error' ? 413 : 500
					});
					message.error({
					    content: outputData.detail || 'An error occurred',
					    duration: 10,
					    key: 'stream-error'
					});
					errorOccurred = true;
					removeStreamingConversation(conversationId);
					break;
				    }
                                } catch (e) {} // Not JSON or not an error
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
                } catch (e) {
                    console.error('Error parsing JSON:', e);
                }
            }
        }

        async function readStream() {
	    let streamClosed = false;
            while (true) {
		try {
                    const { done, value } = await reader.read();
                    if (done) break;
                    if (errorOccurred) break;
                    const chunk = decoder.decode(value);
                    if (!chunk) continue;
                    // Try to parse the chunk as JSON to check for validation error
                    try {
                        const jsonData = JSON.parse(chunk);
                        if (jsonData.error === 'validation_error') {
		            const response = new Response(chunk, { status: 413 });
			    try {
                                throw await handleStreamError(response);
                            } catch (error) {
                                errorOccurred = true;
                                removeStreamingConversation(conversationId);
                                throw error;
                            }
                            break;
                        }
                    } catch (e) {} // Ignore parse errors for non-JSON chunks
		    parser.feed(chunk);
                } catch (error) {
                    console.error('Error reading stream:', error);
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
 
        // For subsequent messages, build the history pairs
        const validMessages = messages.filter(msg => msg?.content?.trim());

	console.log('Valid messages:', validMessages.map(m => ({
            role: m.role,
            content: m.content.substring(0, 50)
        })));

	// Build pairs from completed exchanges
        for (let i = 0; i < validMessages.length; i++) {
            const current = validMessages[i];
            const next = validMessages[i + 1];

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

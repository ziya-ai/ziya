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
    let errorMessage: string = 'An unknown error occurred';

    // Try to get detailed error message from response
    try {
        const errorData = await response.json();
        errorMessage = errorData.detail || errorMessage;
    } catch (e) {
        // If we can't parse the response, keep the default error message
        console.warn('Could not parse error response:', e);
    }

    if (response.status === 413) {
	errorMessage = 'Selected content is too large for the model. Please reduce the number of files.';
	message.error({
            content: errorMessage,
            duration: 10
        });
    } else if (response.status === 401) {
        errorMessage = 'Authentication failed. Please refresh your AWS credentials.';
        message.error({
            content: errorMessage,
            duration: 10,
            key: 'auth-error'
        });
    } else {

        message.error({
            content: errorMessage,
            duration: 10
        });
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
    messages: Message[],
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>,
    setIsStreaming: (streaming: boolean) => void,
    checkedItems: string[], 
    addMessageToConversation: (message: Message) => void,
    removeStreamingConversation: (id: string) => void,
    onStreamComplete?: (content: string) => void
) => {
    let eventSource: EventSource | undefined;
    try {
	eventSource = undefined;
	let hasError = false;

	console.log('Messages received in sendPayload:', messages.map(m => ({
            role: m.role,
            content: m.content.substring(0, 50)
        })));

	let currentContent = '';
	setIsStreaming(true);
	let response = await getApiResponse(messages, question, checkedItems);
        
        if (!response.ok) {
            if (response.status === 503) {
                await handleStreamError(response);
                // Add a small delay before retrying
                await new Promise(resolve => setTimeout(resolve, 2000));
                // Retry the request once
                let retryResponse = await getApiResponse(messages, question, checkedItems);
                if (!retryResponse.ok) {
                    throw await handleStreamError(retryResponse);
                }
                response = retryResponse;
            } else if (response.status === 401) {
                // Handle auth failure explicitly
                throw await handleStreamError(response);
            } else {
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

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
	let errorOccurred = false;
        const parser = createParser(onParse);

        function onParse(event) {
            if (event.type === 'event') {
                try {
                    const data = JSON.parse(event.data);
		    // Check for any type of error response
                    if (data.error || data.event === 'error') {
                        errorOccurred = true;
                        message.error({
			    content: data.detail || 'An error occurred during processing',
                            duration: 10,
                            key: data.error || 'stream-error'
                        });
                        setIsStreaming(false);
                        removeStreamingConversation(conversationId);
                        return;
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
                    if (chunk) {
                        parser.feed(chunk);
                    }
                } catch (error) {
                    console.error('Error reading stream:', error);
                    break;
		}
            }
        }

	try {
            await readStream();
	    // After successful streaming, update with final content
	    if (currentContent && !errorOccurred) {
                onStreamComplete?.(currentContent);
		// Add AI response to conversation
                const aiMessage: Message = {
                    role: 'assistant',
                    content: currentContent
                };
                addMessageToConversation(aiMessage);
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
	if (eventSource && eventSource instanceof EventSource) eventSource.close();
	// Clear streaming state
        setIsStreaming(false);
        removeStreamingConversation(conversationId);
        // Show error message if not already shown
        if (!(error instanceof Error && error.message.includes('AWS credential error'))) {
            message.error({
                content: error instanceof Error ? error.message : 'An unknown error occurred. Please try again.',
		key: 'stream-error',
                duration: 5,
            });
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
            return await fetch('/ziya/stream_log', {
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
            }) as Response;
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
        return await fetch('/ziya/stream_log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
	    body: JSON.stringify(payload),
        }) as Response;
    } catch (error) {
        console.error('API request failed:', error);
        throw error;
    }
}

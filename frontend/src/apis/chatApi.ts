import { createParser } from 'eventsource-parser';
import { message } from 'antd';

interface Operation {
    op: string;
    path: string;
    value?: string;
}

export type SetStreamedContentFunction = {
    (updater: (prev: string) => string): void;
};

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

const handleStreamError = async (response: Response) => {
    const errorData = await response.json();
    const errorMessage = errorData.detail || 'An unknown error occurred';
    
    message.error({
        content: errorMessage,
        duration: 10
    });
};

const createEventSource = (url: string, body: any): EventSource => {
    const params = new URLSearchParams();
    params.append('data', JSON.stringify(body));
    const eventSource = new EventSource(`${url}?${params}`);

    // Add error handling for the EventSource
    eventSource.onerror = (error) => {
        eventSource.close();
        message.error({
            content: 'Connection to server lost. Please try again.',
            duration: 5
        });
    };

    return eventSource;
};

export const sendPayload = async (
    messages: any[],
    question: string,
    setStreamedContent: SetStreamedContentFunction,
    setIsStreaming: (streaming: boolean) => void,
    checkedItems: string[],
    onStreamComplete?: (content: string) => void
) => {
    try {
	let hasError = false;

	// Filter out any empty messages
        const validMessages = messages.filter(msg => msg.content?.trim());
        if (validMessages.length === 0) {
            throw new Error('No valid messages to send');
        }

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
                    await handleStreamError(retryResponse);
                    return;
                }
                response = retryResponse;
            } else {
                await handleStreamError(response);
                return;
            }
        }

        if (!response.body) {
            throw new Error('No body in response');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
	let currentContent = '';
	let errorOccurred = false;
        const parser = createParser(onParse);

        function onParse(event) {
            if (event.type === 'event') {
                try {
                   const data = JSON.parse(event.data);
		   // Check if this is an error event first
                    if (data.event === 'error') {
                        errorOccurred = true;
                        message.error({
                            content: data.detail || 'An error occurred during processing',
                            duration: 5
                        });
                        return;
                    }

                    // Process operations if present
                    const ops = data.ops || [];
                    for (const op of ops) {
                        if (op.op === 'add' && op.path.endsWith('/streamed_output_str/-')) {
                            const newContent = op.value || '';
                            if (!newContent) continue;

                            currentContent += newContent;
                            setStreamedContent(() => {
                                return currentContent;
                            });
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
            }
        } catch (error) {
	    // Type guard for DOMException
            if (error instanceof DOMException && error.name === 'AbortError') return;
            console.error('Stream error:', error);
            hasError = true;
            throw error;
        }
    } catch (error) {
        console.error('Error in sendPayload:', error);
	// Only show error message if it's not an auth/service error (which is already handled)
        if (!(error instanceof Error && error.message.includes('401'))) {
            message.error({
                content: error instanceof Error ? error.message : 'An unknown error occurred',
                duration: 5,
            });
        }
    } finally {
        setIsStreaming(false);
    }
};

async function getApiResponse(messages: any[], question: string, checkedItems: string[]) {
    const messageTuples = [];
    let tempArray = [];

    messages = cleanMessages(messages);

    for (let i = 0; i < messages.length; i++) {
        // @ts-ignore
        tempArray.push(messages[i].content);
        if (tempArray.length === 2) {
            // @ts-ignore
            messageTuples.push(tempArray);
            tempArray = [];
        }
    }
    console.log('Sending payload:', JSON.stringify({chat_history: messageTuples, question, config: { files: checkedItems }}, null, 2));
    const response = await fetch('/ziya/stream_log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            input: {
                chat_history: messageTuples,
                question: question,
                config: { files: checkedItems },
            },
        }),
    });

    if (!response.ok) {
        const error = await response.text();
        throw new Error(`API request failed: ${error}`);
    }
    return response;
}

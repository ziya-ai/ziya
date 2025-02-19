import { createParser } from 'eventsource-parser';
import { message } from 'antd';

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

const testOpsPathRegex = (op: Operation) => {
    return /^\/logs\/.*\/streamed_output_str\/-$/.test(op.path);
}

export const sendPayload = async (messages, question, setStreamedContent, setIsStreaming, checkedItems) => {
    try {

	// Filter out any empty messages
        const validMessages = messages.filter(msg => msg.content?.trim());
        if (validMessages.length === 0) {
            throw new Error('No valid messages to send');
        }

        const response = await getApiResponse(messages, question, checkedItems);

	if (!response.ok) {
            let errorMessage = 'Failed to get response from server';
            try {
                const errorData = await response.json();
                if (response.status === 503) {
                    // Show service unavailable errors with a specific style
                    message.error({
                        content: errorData.detail,
                        duration: 10,
                        className: 'service-error-message',
                        style: {
                            width: '400px'
                        }
                    });
                    return;
                }
                if (response.status === 401) {
                    // Show authentication errors with more prominence
                    message.error({
                        content: errorData.detail,
                        duration: 10,
                        className: 'auth-error-message',
                        style: {
                            width: '600px',
                            whiteSpace: 'pre-wrap'  // Preserve line breaks in error message
                        }
                    });
                    return;
                }
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                console.error('Error parsing error response:', e);
            }
            throw new Error(errorMessage);
        }

        if (!response.body) {
            throw new Error('No body in response');
        }

        const reader = response.body.getReader();
        const contentChunks: string[] = [];
        const decoder = new TextDecoder('utf-8');
        const parser = createParser(onParse);

        function onParse(event) {
            if (event.type === 'event') {
                try {
                    const data = JSON.parse(event.data);
                    processOps(data.ops);
                } catch (e) {
                    console.error('Error parsing JSON:', e);
                }
            }
        }

        function processOps(ops: Operation[]) {
            for (const op of ops) {
                if (
                    op.op === 'add' &&
                    testOpsPathRegex(op)
                ) {
                    contentChunks.push(op.value || '');
		    const newContent = op.value || '';
		    setStreamedContent(prev => prev + newContent);
                }
            }
        }

        async function readStream() {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value);
                parser.feed(chunk);
            }
        }

        await readStream();

    } catch (error) {
        console.error('Error in sendPayload:', error);
	// Only show error message if it's not an auth/service error (which is already handled)
        if (!(error instanceof Error && error.message.includes('401'))) {
            message.error({
                content: error instanceof Error ? error.message : 'An unknown error occurred',
                duration: 5,
            });
            setIsStreaming(false);
        }
        setIsStreaming(false);
    }
};

async function getApiResponse(messages, question, checkedItems) {
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

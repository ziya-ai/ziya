import { createParser } from 'eventsource-parser';

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

export const sendPayload = async (messages, question, setStreamedContent, checkedItems) => {
    try {

	// Filter out any empty messages
        const validMessages = messages.filter(msg => msg.content?.trim());
        if (validMessages.length === 0) {
            throw new Error('No valid messages to send');
        }

        const response = await getApiResponse(messages, question, checkedItems);

        if (!response.body) {
            throw new Error('No body in response');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        const parser = createParser(onParse);
        const contentChunks = [];

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

        function processOps(ops) {
            for (const op of ops) {
                if (
                    op.op === 'add' &&
                    op.path === '/logs/ChatBedrock/streamed_output_str/-'
                ) {
                    // @ts-ignore
                    contentChunks.push(op.value);
                    setStreamedContent(contentChunks.join(''));
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
        console.error(error);
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

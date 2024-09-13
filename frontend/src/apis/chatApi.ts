import { createParser } from 'eventsource-parser';

export const sendPayload = async (messages, question, setStreamedContent, checkedItems) => {
    try {
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
    for (let i = 0; i < messages.length; i++) {
        // @ts-ignore
        tempArray.push(messages[i].content);
        if (tempArray.length === 2) {
            // @ts-ignore
            messageTuples.push(tempArray);
            tempArray = [];
        }
    }
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
    return response;
}

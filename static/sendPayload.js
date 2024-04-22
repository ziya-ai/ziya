async function getApiResponse(messages, question) {
    const messageTuples = [];
    let tempArray = [];
    for (let i = 0; i < messages.length; i++) {
        tempArray.push(messages[i].content);
        if (tempArray.length === 2) {
            messageTuples.push(tempArray);
            tempArray = [];
        }
    }
    const response = await fetch('/ziya/stream_log', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            input: {
                chat_history: messageTuples,
                question: question,
            },
            config: {},
        }),
    });
    return response;
}

const processLine = (line, setStreamedContent) => {
    const data = JSON.parse(line.slice(6));
    const streamedOutputOp = data.ops.find(
        (op) => op.op === 'add' && op.path === '/logs/ChatBedrock/streamed_output_str/-'
    );

    if (streamedOutputOp) {
        setStreamedContent((prevContent) => prevContent + streamedOutputOp.value);
    }
};

window.sendPayload = async (messages, question, setStreamedContent, setIsStreaming) => {
    try {
        const response = await getApiResponse(messages, question);
        const reader = response.body.getReader();

        const processData = async ({done, value}) => {
            if (done) {
                setIsStreaming(false);
                return;
            }
            let buffer = '';
            buffer += new TextDecoder('utf-8').decode(value);
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    processLine(line, setStreamedContent);
                }
            }
            await reader.read().then(processData);
        };
        await reader.read().then(processData);
    } catch (error) {
        console.error(error);
        setIsStreaming(false);
    }
};

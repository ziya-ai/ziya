const {useState, useRef, useEffect} = React;

const App = () => {
    const [messages, setMessages] = useState([]);
    const [question, setQuestion] = useState('');
    const [streamedContent, setStreamedContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const inputTextareaRef = useRef(null);

    useEffect(() => {
        if (inputTextareaRef.current) {
            inputTextareaRef.current.focus();
        }
    }, []);

    const handleSendPayload = async () => {
        setQuestion('');
        setIsStreaming(true);
        setMessages((messages) => [...messages, {content: question, role: 'human'}]);
        setStreamedContent('');
        await window.sendPayload(messages, question, setStreamedContent, setIsStreaming);
        setStreamedContent((cont) => {
            setMessages((messages) => [...messages, {content: cont, role: 'assistant'}]);
            return ""
        });
        inputTextareaRef.current.focus();
    };

    const handleChange = (event) => {
        setQuestion(event.target.value);
    };

    return (
        <div><h2 style={{textAlign: "center"}}>Ziya: Code Assist</h2>
            <div className="container">
                <InputContainer
                    inputTextareaRef={inputTextareaRef}
                    question={question}
                    handleChange={handleChange}
                    sendPayload={handleSendPayload}
                    isStreaming={isStreaming}
                />
                {streamedContent || messages.length > 0 ? <div className="chat-container">
                    {streamedContent && <StreamedContent streamedContent={streamedContent}/>}
                    {messages.length > 0 && <ChatContainer messages={messages}/>}
                </div> : null}
            </div>
        </div>
    );
};

const ChatContainer = ({messages}) => (
    <div>
        {messages.slice().reverse().map((msg, index) => (
            <div key={index} className={`message ${msg.role}`}>
                {msg.role === 'human' ? <div className="message-sender">You:</div> :
                    <div className="message-sender">AI:</div>}
                <MarkdownRenderer markdown={msg.content}/>
            </div>
        ))}
    </div>
);

const isQuestionEmpty = (input) => input.trim().length === 0;

const StreamedContent = ({streamedContent}) => (
    <div className="message assistant">
        <div className="message-sender">AI:</div>
        <MarkdownRenderer markdown={streamedContent}/>
    </div>
);
const InputContainer = ({question, handleChange, sendPayload, isStreaming, inputTextareaRef}) => (

    <div className="input-container">
    <textarea
        ref={inputTextareaRef}
        value={question}
        onChange={handleChange}
        placeholder="Enter your question.."
        rows={3}
        className="input-textarea"
        onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !isQuestionEmpty(question)) {
                event.preventDefault(); // Prevent the default behavior of adding a new line
                sendPayload();
            }
        }}
    />
        <button onClick={sendPayload} disabled={isStreaming || isQuestionEmpty(question)} className="send-button">
            {isStreaming ? `Sending..` : `Send`}
        </button>
    </div>
);

const MarkdownRenderer = ({markdown}) => {
    const renderMarkdown = () => {
        const html = marked.parse(markdown);
        return {__html: html};
    };

    return <div dangerouslySetInnerHTML={renderMarkdown()}/>;
};

ReactDOM.render(<App/>, document.getElementById('root'));
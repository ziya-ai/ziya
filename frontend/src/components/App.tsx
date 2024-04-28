import React, {useEffect, useRef, useState} from 'react';
import {FolderTree} from "./FolderTree";
import {MarkdownRenderer} from "./MarkdownRenderer";
import {ChatHistory} from "./ChatHistory";
import {MainChatContainer} from "./MainChatContainer";
import {sendPayload} from "../apis/chatApi";

const App = () => {
    const [messages, setMessages] = useState([]);
    const [question, setQuestion] = useState('');
    const [streamedContent, setStreamedContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [checkedItems, setCheckedItems] = useState<string[]>([]);

    const inputTextareaRef = useRef(null);

    useEffect(() => {
        if (inputTextareaRef.current) {
            // @ts-ignore
            inputTextareaRef.current.focus();
        }
    }, []);

    const handleSendPayload = async (msgs, msg, folders) => {
        setQuestion('');
        setIsStreaming(true);
        // @ts-ignore
        setMessages((messages) => [...msgs, {content: msg, role: 'human'}]);
        setStreamedContent('');
        await sendPayload(messages, msg, setStreamedContent, setIsStreaming, Array.from(folders));
        setStreamedContent((cont) => {
            // @ts-ignore
            setMessages((messages) => [...messages, {content: cont, role: 'assistant'}]);
            return ""
        });
        // @ts-ignore
        inputTextareaRef.current.focus();
    };

    const handleChange = (event) => {
        setQuestion(event.target.value);
    };

    return (
        <div><h2 style={{textAlign: "center"}}>Ziya: Code Assist</h2>
            <div className="container">
                <FolderTree
                    setCheckedItems={setCheckedItems}
                />
                <MainChatContainer
                    inputTextareaRef={inputTextareaRef}
                    question={question}
                    handleChange={handleChange}
                    sendPayload={() => {
                        handleSendPayload(messages, question, checkedItems)
                    }}
                    isStreaming={isStreaming}
                />
                {streamedContent || messages.length > 0 ? <div className="chat-container">
                    {streamedContent && <StreamedContent streamedContent={streamedContent}/>}
                    {messages.length > 0 &&
                        <ChatHistory messages={messages} setMessages={setMessages} checkedItems={checkedItems}
                                     handleSendPayload={handleSendPayload}/>}
                </div> : null}

            </div>
        </div>
    );
};

const StreamedContent = ({streamedContent}) => (
    <div className="message assistant">
        <div className="message-sender">AI:</div>
        <MarkdownRenderer markdown={streamedContent}/>
    </div>
);

export default App;

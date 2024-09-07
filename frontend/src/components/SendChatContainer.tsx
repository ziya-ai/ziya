import React, { useEffect, useRef } from "react";
import { useChatContext } from '../context/ChatContext';
import { sendPayload } from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";

const isQuestionEmpty = (input: string) => input.trim().length === 0;

export const SendChatContainer: React.FC = () => {
    const {
        question,
        setQuestion,
        isStreaming,
        setIsStreaming,
        messages,
        setMessages,
        setStreamedContent
    } = useChatContext();

    const {checkedKeys} = useFolderContext();

    const textareaRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        console.log("checkedKeys")
        console.log(checkedKeys)
    }, [checkedKeys]);

    useEffect(() => {
        if (question === '' && textareaRef.current) {
            textareaRef.current.focus();
        }
    }, [question]);

    const handleChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
        setQuestion(event.target.value);
    };

    const handleSendPayload = async () => {
        setQuestion('');
        setIsStreaming(true);
        setMessages((prevMessages) => [...prevMessages, {content: question, role: 'human'}]);
        setStreamedContent('');
        await sendPayload(messages, question, setStreamedContent, checkedKeys);
        setStreamedContent((cont) => {
            setMessages((prevMessages) => [...prevMessages, {content: cont, role: 'assistant'}]);
            return "";
        });
        setIsStreaming(false)
    };

    return (
        <div className="input-container">
            <textarea
                ref={textareaRef}
                value={question}
                onChange={handleChange}
                placeholder="Enter your question.."
                rows={3}
                className="input-textarea"
                onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey && !isQuestionEmpty(question)) {
                        event.preventDefault();
                        handleSendPayload();
                    }
                }}
            />
            <button
                onClick={handleSendPayload}
                disabled={isStreaming || isQuestionEmpty(question)}
                className="send-button"
            >
                {isStreaming ? `Sending..` : `Send`}
            </button>
        </div>
    );
};
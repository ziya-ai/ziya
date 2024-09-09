import React, {useEffect, useRef} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {Button, Input} from 'antd'; // Import Ant Design components

const {TextArea} = Input; // Destructure TextArea from Input

const isQuestionEmpty = (input: string) => input.trim().length === 0;

export const SendChatContainer: React.FC = () => {
    const {
        question,
        setQuestion,
        isStreaming,
        setIsStreaming,
        messages,
        addMessageToCurrentConversation,
        setStreamedContent
    } = useChatContext();

    const {checkedKeys} = useFolderContext();

    const textareaRef = useRef<any>(null);

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
        const newHumanMessage = {content: question, role: 'human' as 'human'};
        addMessageToCurrentConversation(newHumanMessage);

        setStreamedContent('');
        await sendPayload([...messages, newHumanMessage], question, setStreamedContent, checkedKeys);
        setStreamedContent((cont) => {
            const newAIMessage = {content: cont, role: 'assistant' as 'assistant'};
            addMessageToCurrentConversation(newAIMessage);
            return "";
        });
        setIsStreaming(false);
    };

    return (
        <div className="input-container">
            <TextArea
                ref={textareaRef}
                value={question}
                onChange={handleChange}
                placeholder="Enter your question.."
                className="input-textarea"
                onPressEnter={(event) => {
                    if (!event.shiftKey && !isQuestionEmpty(question)) {
                        event.preventDefault();
                        handleSendPayload();
                    }
                }}
            />
            <Button
                onClick={handleSendPayload}
                disabled={isStreaming || isQuestionEmpty(question)}
                className="send-button"
                type="primary"
            >
                {isStreaming ? `Sending..` : `Send`}
            </Button>
        </div>
    );
};
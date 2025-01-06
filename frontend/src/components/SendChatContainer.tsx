import React, {useEffect, useRef, memo, useCallback, useMemo, SetStateAction} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {useFolderContext} from "../context/FolderContext";
import {Button, Input} from 'antd';
import {SendOutlined} from "@ant-design/icons";
import debounce from 'lodash/debounce';

const {TextArea} = Input;

const isQuestionEmpty = (input: string) => input.trim().length === 0;

interface SendChatContainerProps {
    fixed?: boolean;
    empty?: boolean;
}

export const SendChatContainer = memo<SendChatContainerProps>(({ fixed = false, empty = false }) => {
    const {
        question,
        setQuestion,
        isStreaming,
        setIsStreaming,
        messages,
        addMessageToCurrentConversation,
        setStreamedContent,
	scrollToBottom,
	isTopToBottom
    } = useChatContext();

    const {checkedKeys} = useFolderContext();

    const textareaRef = useRef<any>(null);

    useEffect(() => {
        if (question === '' && textareaRef.current) {
            textareaRef.current.focus();
        }
    }, [question]);

    const handleChange = useCallback((event: React.ChangeEvent<HTMLTextAreaElement>) => {
	// Update the input value immediately for responsiveness
        setQuestion(event.target.value);
    }, [setQuestion]);

    // Clean up any pending debounced calls when component unmounts
    useEffect(() => {
        return () => {
            if (handleChange) {
                (handleChange as any).cancel?.();
            }
        };
    }, [handleChange]);

    const handleSendPayload = async () => {
        setQuestion('');
        setIsStreaming(true);
        const newHumanMessage = {content: question, role: 'human' as 'human'};
        addMessageToCurrentConversation(newHumanMessage);

	if (isTopToBottom) {
            // Ensure scroll to bottom after sending message in top-down mode
            setTimeout(scrollToBottom, 0);
        }

        if (!isTopToBottom) {
            // In bottom-up mode, scroll to the input
            const bottomUpContent = document.querySelector('.bottom-up-content');
            if (bottomUpContent) {
                bottomUpContent.scrollTop = 0;
                // Add a small delay to ensure proper positioning
                setTimeout(() => bottomUpContent.scrollTop = 0, 50);
            }
        }

        setStreamedContent('');
        await sendPayload([...messages, newHumanMessage], question, setStreamedContent, setIsStreaming, checkedKeys);
        setStreamedContent((cont) => {
            const newAIMessage = {content: cont, role: 'assistant' as 'assistant'};
            addMessageToCurrentConversation(newAIMessage);
            return "";
        });
        setIsStreaming(false);
    };

    return (
         <div className={`input-container ${empty ? 'empty-state' : ''}`}>
            <TextArea
                ref={textareaRef}
                value={question}
                onChange={handleChange}
                placeholder="Enter your question.."
		autoComplete="off"
                className="input-textarea"
                onPressEnter={(event) => {
                    if (!isStreaming && !event.shiftKey && !isQuestionEmpty(question)) {
                        event.preventDefault();
                        handleSendPayload();
                    }
                }}
            />
            <Button
                onClick={handleSendPayload}
                disabled={isStreaming || isQuestionEmpty(question)}
                type="primary"
                icon={<SendOutlined/>}
                style={{marginLeft: '10px'}}
            >
                {isStreaming ? 'Sending...' : 'Send'}
            </Button>
        </div>
    );
});

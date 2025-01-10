import React, {useEffect, useRef, memo, useCallback} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload, SetStreamedContentFunction} from "../apis/chatApi";
import {Message} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Button, Input, message} from 'antd';
import {SendOutlined} from "@ant-design/icons";
import debounce from 'lodash/debounce';

const {TextArea} = Input;

const isQuestionEmpty = (input: string) => input.trim().length === 0;

interface SendChatContainerProps {
    fixed?: boolean;
    empty?: boolean;
}

export const SendChatContainer: React.FC<SendChatContainerProps> = memo(({ fixed = false, empty = false }) => {
    const {
        question,
        setQuestion,
        isStreaming,
        setIsStreaming,
	streamedContent,
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

    // Memoize the disabled state to prevent unnecessary re-renders
    const isDisabled = isStreaming || isQuestionEmpty(question);

    const handleSendPayload = async () => {
        setQuestion('');
        setIsStreaming(true);

	// Get current max sequence before adding new message
        const currentMaxSequence = messages.length > 0
            ? Math.max(...messages.map(m => m.sequence))
            : 0;
	
	const newHumanMessage: Message = {
            content: question,
            role: 'human',
            // For new messages, use current time and next sequence
            timestamp: Date.now(),
	    sequence: currentMaxSequence + 1
        };

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

    try {
	const result = await sendPayload(
            [...messages, newHumanMessage],
            question,
	    (content) => {
                    setStreamedContent(content); // Only update streamed content, don't clear it
            },
            setIsStreaming,
            checkedKeys.map(key => String(key))
        );

	// Get the final streamed content
            const finalContent = streamedContent || result;

            if (finalContent) {
		const newAIMessage: Message = {
                    content: finalContent,
                    role: 'assistant',
                    // For AI responses, use current time and next sequence
                    timestamp: Date.now(),
		    sequence: Math.max(...messages.map(m => m.sequence)) + 1
                };
	    addMessageToCurrentConversation(newAIMessage);
        }
    } catch (error) {
        console.error('Error sending message:', error);
        message.error({
            content: 'Failed to send message. Please try again.',
            duration: 5
        });
    } 
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
                disabled={isDisabled}
                type="primary"
                icon={<SendOutlined/>}
                style={{marginLeft: '10px'}}
            >
                {isStreaming ? 'Sending...' : 'Send'}
            </Button>
        </div>
    );
});

import React, {useEffect, useRef, memo, useState, useCallback} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {Message} from "../utils/types";
import {convertKeysToStrings} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Button, Input, message, Tooltip} from 'antd';
import {SendOutlined} from "@ant-design/icons";

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
	addMessageToConversation,
        streamedContentMap,
        setStreamedContentMap,
        currentMessages,
        currentConversationId,
	streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
    } = useChatContext();

    const {checkedKeys} = useFolderContext();
    const textareaRef = useRef<any>(null);

    useEffect(() => {
        if (question === '' && textareaRef.current) {
            textareaRef.current.focus();
        }
    }, [question]);

    const [isProcessing, setIsProcessing] = useState(false);
    const handleQuestionChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setQuestion(e.target.value);
    }, [setQuestion]);
    const isDisabled = isQuestionEmpty(question) || streamingConversations.has(currentConversationId);

    const handleSendPayload = async () => {

	// Don't allow sending if we're already streaming in this conversation
	if (streamingConversations.has(currentConversationId)) {
            console.warn('Attempted to send while streaming');
            return;
        }

	// Check if the last message was from a human and we're still streaming
        const lastMessage = currentMessages[currentMessages.length - 1];
        if (lastMessage?.role === 'human'  && streamingConversations.has(currentConversationId)) {
            console.warn('Cannot send another human message before AI response');
            return;
        }

        setQuestion('');
	setStreamedContentMap(new Map());

	// Debug log the selected files state
        console.log('Current file selection state:', {
            checkedKeys,
            selectedFiles: convertKeysToStrings(checkedKeys)
        });
	setIsProcessing(true);
	
        // Create new human message
        const newHumanMessage: Message = {
            content: question,
            role: 'human'
        };

	// Add the human message immediately
	addMessageToConversation(newHumanMessage, currentConversationId);

	// Clear streamed content and add the human message immediately
	setStreamedContentMap(new Map());

        console.log('Added human message:', {
            id: currentConversationId,
            content: newHumanMessage.content,
            currentMessages: currentMessages.length
        });

        // Include the new message in messages for the API
        const messagesWithNew = [...currentMessages];
	addStreamingConversation(currentConversationId);
	const targetConversationId = currentConversationId;

        try {
	    // Get latest messages after state update
	    const selectedFiles = convertKeysToStrings(checkedKeys);
            const result = await sendPayload(
                targetConversationId,
                question,
                streamingConversations.has(currentConversationId),
                messagesWithNew,
                setStreamedContentMap,
                setIsStreaming,
                selectedFiles,
                addMessageToConversation,
                removeStreamingConversation
            );
            // Check if result is an error response
            if (typeof result === 'string' && result.includes('"error":"validation_error"')) {
                try {
                    const errorData = JSON.parse(result);
                    if (errorData.error === 'validation_error') {
                        message.error({
                            content: errorData.detail,
                            duration: 5,
                            key: 'validation-error'
                        });
                        return;
                    }
                } catch (e) {
                    console.error('Error parsing error response:', e);
                }
            }
            // Get the final streamed content
            const finalContent = streamedContentMap.get(currentConversationId) || result;
            if (finalContent) {
		// Check if result is an error response
                try {
                    const errorData = JSON.parse(finalContent);
                    if (errorData.error === 'validation_error') {
                        message.error(errorData.detail || 'Selected content is too large. Please reduce the number of files.');
                        return;
                    }
                } catch (e) {} // Not JSON or not an error response
                const aiMessage: Message = {
                    content: finalContent,
                    role: 'assistant'
                };
                addMessageToConversation(aiMessage, currentConversationId);
            }
        } catch (error) {
            console.error('Error sending message:', error);
	    // Check if this is a validation error
            if (error instanceof Error && error.message.includes('validationException')) {
                message.error({
                    content: 'Selected content is too large for the model. Please reduce the number of files.',
                    duration: 10
                });
            }
            message.error({
                content: 'Failed to send message. Please try again.',
                key: 'send-error',
                duration: 5
            });
        } finally {
            setIsProcessing(false);
            setIsStreaming(false);
            removeStreamingConversation(currentConversationId);
        }
    };

    return (
        <div className={`input-container ${empty ? 'empty-state' : ''}`}>
            <TextArea
                ref={textareaRef}
                value={question}
		onChange={handleQuestionChange}
                placeholder="Enter your question.."
                autoComplete="off"
		autoSize={{ minRows: 1, maxRows: 6 }}
                className="input-textarea"
                onPressEnter={(event) => {
		    if (!event.shiftKey && !isQuestionEmpty(question)) {
                        event.preventDefault();
                        handleSendPayload();
                    }
                }}
            />
            <Button
	        type="primary"
                onClick={handleSendPayload}
		disabled={isDisabled}
                icon={<SendOutlined/>}
		style={{ marginLeft: '10px' }}
		title={
                    streamingConversations.has(currentConversationId)
                        ? "Waiting for AI response..."
                        : currentMessages[currentMessages.length - 1]?.role === 'human'
                        ? "AI response may have failed - click Send to retry"
                        : "Send message"
                }
            >
		{streamingConversations.has(currentConversationId) ? 'Sending...' : 'Send'}
            </Button>
        </div>
    );
});

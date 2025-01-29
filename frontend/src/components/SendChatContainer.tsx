import React, {useEffect, useRef, memo, useState} from "react";
import {useChatContext} from '../context/ChatContext';
import {sendPayload} from "../apis/chatApi";
import {Message} from "../utils/types";
import {convertKeysToStrings} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Button, Input, message} from 'antd';
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

    const handleSendPayload = async () => {

	// Don't allow sending if we're already streaming in this conversation
	if (streamingConversations.has(currentConversationId)) {
            console.warn('Attempted to send while streaming');
            return;
        }

	// Check if the last message was from a human
        const lastMessage = currentMessages[currentMessages.length - 1];
        if (lastMessage?.role === 'human') {
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
        addMessageToConversation(newHumanMessage);

	// Clear streamed content and add the human message immediately
	setStreamedContentMap(new Map());

        console.log('Added human message:', {
            content: newHumanMessage.content,
            currentMessages: currentMessages.length
        });

        // Include the new message in messages for the API
        const messagesWithNew = [...currentMessages];
	addStreamingConversation(currentConversationId);

        try {
	    // Get latest messages after state update
	    const selectedFiles = convertKeysToStrings(checkedKeys);
            const result = await sendPayload(
                currentConversationId,
                question,
		messagesWithNew,
                setStreamedContentMap,
                setIsStreaming,
		selectedFiles,
		addMessageToConversation,
                removeStreamingConversation
            );

            // Get the final streamed content
	    const finalContent = streamedContentMap.get(currentConversationId) || result;

        } catch (error) {
            console.error('Error sending message:', error);
            message.error({
                content: 'Failed to send message. Please try again.',
                duration: 5
            });
	} finally {
	    setIsProcessing(false);
        }
    };

    return (
        <div className={`input-container ${empty ? 'empty-state' : ''}`}>
            <TextArea
                ref={textareaRef}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Enter your question.."
                autoComplete="off"
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
		disabled={Boolean(
                    // Only block if streaming in THIS conversation
	            streamingConversations.has(currentConversationId) ||
                    // Or if the question is empty
                    isQuestionEmpty(question) ||
                    // Or if the last message in this conversation was from human
                    currentMessages[currentMessages.length - 1]?.role === 'human'
                )}
                type="primary"
                icon={<SendOutlined/>}
                style={{marginLeft: '10px'}}
            >
		{streamingConversations.has(currentConversationId) ? 'Sending...' : 'Send'}
            </Button>
        </div>
    );
});

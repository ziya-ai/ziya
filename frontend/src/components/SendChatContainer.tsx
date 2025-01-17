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
        streamedContent,
        currentMessages,
        addMessageToCurrentConversation,
        setStreamedContent,
        currentConversationId,
	streamingConversationId,
        setStreamingConversationId
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

	// Don't allow sending if we're already streaming
        if (isStreaming) {
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
        setIsStreaming(true);
	setStreamedContent('');
	setStreamingConversationId(currentConversationId);

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

	// Add message and wait for state to update
        await new Promise<void>(resolve => {
	    setStreamedContent('');
            addMessageToCurrentConversation(newHumanMessage);
            // Use a small timeout to ensure state has updated
            setTimeout(resolve, 0);
        });

        try {
	    // Get latest messages after state update
            const updatedMessages = [...currentMessages, newHumanMessage];
	    const selectedFiles = convertKeysToStrings(checkedKeys);
            const result = await sendPayload(
                currentConversationId,
                question,
                updatedMessages,
                setStreamedContent,
                setIsStreaming,
		selectedFiles,
                addMessageToCurrentConversation
            );

            // Get the final streamed content
            const finalContent = streamedContent || result;

            if (finalContent) {
		console.log('Received AI response, adding to conversation');
            }
        } catch (error) {
            console.error('Error sending message:', error);
            message.error({
                content: 'Failed to send message. Please try again.',
                duration: 5
            });
	} finally {
	    setIsStreaming(false);
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
		disabled={
		    isStreaming || isProcessing || 
                    isQuestionEmpty(question) || 
		    currentMessages[currentMessages.length - 1]?.role === 'human'}
                type="primary"
                icon={<SendOutlined/>}
                style={{marginLeft: '10px'}}
            >
                {isStreaming ? 'Sending...' : 'Send'}
            </Button>
        </div>
    );
});

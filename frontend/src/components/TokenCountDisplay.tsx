import React, {useEffect, useState, useCallback, useMemo, useRef} from 'react';
import {Folders, Message} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Tooltip, Spin, Progress, Typography, message, ProgressProps} from "antd";
import { useTheme } from '../context/ThemeContext';
import { InfoCircleOutlined } from '@ant-design/icons';
import {useChatContext} from "../context/ChatContext";

const TOKEN_LIMIT = 160000;
const WARNING_THRESHOLD = 100000;  // Lower threshold to account for overhead
const DANGER_THRESHOLD = 140000;   // Lower threshold to account for overhead

const getTokenCount = async (text: string): Promise<number> => {
    try {
        const response = await fetch('/api/token-count', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Token count request failed');
        }
        const data = await response.json();
        return data.token_count;
    } catch (error) {
        message.error({
	    content: error instanceof Error ? error.message : 'An unknown error occurred',
            duration: 5
        });
        console.error('Error getting token count:', error);
        return 0;
    }
};

export const TokenCountDisplay = () => {

    const {folders, checkedKeys, getFolderTokenCount} = useFolderContext();
    const {currentMessages, currentConversationId, isStreaming} = useChatContext();
    const [totalTokenCount, setTotalTokenCount] = useState(0);
    const [chatTokenCount, setChatTokenCount] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const { isDarkMode } = useTheme();
    const lastMessageCount = useRef<number>(0);
    const lastMessageContent = useRef<string>('');
    const [tokenDetails, setTokenDetails] = useState<{[key: string]: number}>({}); 
    const combinedTokenCount = totalTokenCount + chatTokenCount;

    // only calculate tokens when checked files change
    useEffect(() => {
	if (folders && checkedKeys.length > 0) {
            console.debug('Recalculating file tokens due to checked files change');
	    let total = 0;
            const details: {[key: string]: number} = {};

            // Use getFolderTokenCount for each checked path
            checkedKeys.forEach(key => {
                const path = String(key);
		if (!folders) {
		    setTokenDetails({});
                    return;
                }
                const tokens = getFolderTokenCount(path, folders);
                if (tokens > 0) {
                    details[path] = tokens;
                    total += tokens;
                }
            });
            console.debug('Token count details:', details);
            setTokenDetails(details);
            setTotalTokenCount(total);
        } else {
	    setTokenDetails({});
            setTotalTokenCount(0);
        } 
    }, [checkedKeys, folders, getFolderTokenCount]);

    const getTokenColor = (count: number): string => {
        if (count >= DANGER_THRESHOLD) return '#ff4d4f';  // Red
        if (count >= WARNING_THRESHOLD) return '#faad14'; // Orange
        return '#52c41a';  // Green
    };
    const getTokenStyle = (count: number) => ({
        color: getTokenColor(count),
        fontWeight: 'bold'
    });

    const calculateTotalTokenCount = (checked: string[]) => {
	if (!folders) return;

        let totalTokenCount = 0;
        checked.forEach(item => {
            const tokenCount = getFolderTokenCount(item, folders);
            totalTokenCount += tokenCount;		
        });
        setTotalTokenCount(totalTokenCount);
    };

    const previousMessagesRef = useRef<string>('');
    const hasMessagesChanged = useCallback((messages: Message[]) => {
	const messagesContent = messages.length > 0 ? messages.map(msg => msg.content).join('\n') : '';
        if (messagesContent !== previousMessagesRef.current) {
            previousMessagesRef.current = messagesContent;
	    console.debug('Messages changed:', { length: messages.length, content: messagesContent.slice(0, 100) });
            return true;
        }
        return false;
    }, [previousMessagesRef]);

    const updateChatTokens = useCallback(async () => {
        if (currentMessages.length === 0) {
            setChatTokenCount(0);
	    lastMessageCount.current = 0;
            lastMessageContent.current = '';
            previousMessagesRef.current = '';
            console.debug('Skipping token count update - no messages');
            setIsLoading(false);
            return;
        }

        setIsLoading(true);
        try {
            const allText = currentMessages.map(msg => msg.content).join('\n');
            const tokens = await getTokenCount(allText);
            setChatTokenCount(tokens);
            lastMessageCount.current = currentMessages.length;
            lastMessageContent.current = allText;
	} catch (error) {
            console.error('Failed to get token count:', error);
            setChatTokenCount(0);
        } finally {
            setIsLoading(false);
        }
    }, [currentMessages]);

    // update chat tokens only when messages or conversation change
    useEffect(() => {
	console.debug('Conversation or messages changed:', {
            conversationId: currentConversationId,
            messageCount: currentMessages.length,
	    isStreaming
        });
        if (!currentConversationId || currentMessages.length === 0) {
            console.debug('Resetting token count - empty conversation');
            setChatTokenCount(0);
            lastMessageCount.current = 0;
            lastMessageContent.current = '';
	    previousMessagesRef.current = '';
            return;
        }
        
        // Only update tokens if we have messages
        if (hasMessagesChanged(currentMessages)) {
            console.debug('Updating chat tokens for conversation:', currentConversationId);
            updateChatTokens();
        }
    }, [currentMessages, updateChatTokens, currentConversationId, hasMessagesChanged, isStreaming]);

    const getProgressStatus = (count: number): ProgressProps['status'] => {
        if (count >= DANGER_THRESHOLD) return 'exception';
        if (count >= WARNING_THRESHOLD) return 'normal';
        return 'success';
    };
    const tokenDisplay = useMemo(() => (
	<div className="token-summary" style={{
            backgroundColor: 'inherit',
	    padding: '4px',
	    borderBottom: '1px solid',
            borderBottomColor: isDarkMode ? '#303030' : '#e8e8e8',
            transition: 'all 0.3s ease',
	    minHeight: '70px',
            boxSizing: 'border-box',
	    position: 'relative'
        }}>
	{isLoading && (
                <div style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    background: 'rgba(0, 0, 0, 0.1)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    zIndex: 1,
                    transition: 'all 0.3s ease'
                }}>
                    <Spin size="small" />
                </div>
            )}
            <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
                opacity: isLoading ? 0.5 : 1,
                transition: 'opacity 0.3s ease'
            }}>
                <Typography.Text strong style={{ fontSize: '12px', marginBottom: '2px' }}>
                    Token Estimates
                </Typography.Text>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                    <Tooltip title="Tokens from selected files" mouseEnterDelay={0.5}>
                        <span>Files: <span style={getTokenStyle(totalTokenCount)}>
                            {totalTokenCount.toLocaleString()}</span></span>
                    </Tooltip>
		    <Tooltip title="Tokens from chat history" mouseEnterDelay={0.5}>
                        <span>Chat: <span style={getTokenStyle(chatTokenCount)}>
                            {chatTokenCount.toLocaleString()}</span></span>
                    </Tooltip>
                    <Tooltip title="Combined tokens (files + chat)" mouseEnterDelay={0.5}>
                        <span>Total: <span style={getTokenStyle(combinedTokenCount)}>
                            {combinedTokenCount.toLocaleString()}</span></span>
                    </Tooltip>
                </div>
		<Tooltip title={`${combinedTokenCount.toLocaleString()} of ${TOKEN_LIMIT.toLocaleString()} tokens used`} mouseEnterDelay={0.5}>
                    <div>
                        <Progress
                            percent={Math.round((combinedTokenCount / TOKEN_LIMIT) * 100)}
                            size="small"
                            status={getProgressStatus(combinedTokenCount)}
                            showInfo={false}
                            strokeWidth={4}
                            style={{ margin: '4px 0', transition: 'all 0.3s ease' }}
                        />
                    </div>
                </Tooltip>
            </div>
        </div>
    ), [isLoading, totalTokenCount, chatTokenCount, combinedTokenCount]);
 
    return (
	<>
	    <div className="token-display" style={{ padding: '0 8px' }}>
                {tokenDisplay}
	    </div>
        </>
    );
};

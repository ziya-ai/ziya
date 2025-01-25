import React, {useEffect, useState, useCallback, useMemo, useRef} from 'react';
import {Folders, Message} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Tooltip, Spin, message} from "antd";
import {useChatContext} from "../context/ChatContext";

const TOKEN_LIMIT = 160000;
const WARNING_THRESHOLD = 120000;
const DANGER_THRESHOLD = 160000;

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

    const {folders, checkedKeys} = useFolderContext();
    const {currentMessages} = useChatContext();

    const [totalTokenCount, setTotalTokenCount] = useState(0);
    const [chatTokenCount, setChatTokenCount] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const lastMessageCount = useRef<number>(0);
    const lastMessageContent = useRef<string>('');

    const combinedTokenCount = totalTokenCount + chatTokenCount;

    // only calculate tokens when checked files change
    useEffect(() => {
	if (folders && checkedKeys.length > 0) {
            console.debug('Recalculating file tokens due to checked files change');
            calculateTotalTokenCount(checkedKeys as string[]);
        }
    }, [checkedKeys]);

    const getTokenCountClass = (count: number) => {
        if (count >= DANGER_THRESHOLD) return 'token-count-total red';
        if (count >= WARNING_THRESHOLD) return 'token-count-total orange';
        return 'token-count-total green';
    };

    const getFolderTokenCount = (filePath: string, folders: Folders): number => {

        let segments = filePath.split('/');
        let lastNode;
        for (const segment of segments) {
            if (folders[segment] && folders[segment].children) {
                folders = folders[segment].children!;
            } else {
                lastNode = folders[segment];
            }
        }
        return lastNode ? lastNode.token_count : 0;
    };

    const calculateTotalTokenCount = (checked: string[]) => {
	if (!folders) return;

        let totalTokenCount = 0;
        checked.forEach(item => {
            const tokenCount = getFolderTokenCount(item, folders);
            totalTokenCount += tokenCount;		
        });
        setTotalTokenCount(totalTokenCount);
    };

    const hasMessagesChanged = useCallback((messages: Message[]) => {
        if (messages.length !== lastMessageCount.current) {
            return true;
        }
        const newContent = messages.map(msg => msg.content).join('\n');
        if (newContent !== lastMessageContent.current) {
            return true;
        }
        return false;
    }, []);

    const updateChatTokens = useCallback(async () => {
        if (currentMessages.length === 0) {
            setChatTokenCount(0);
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

    // update chat tokens only when messages change
    useEffect(() => {
	if (currentMessages.length > 0 && hasMessagesChanged(currentMessages)) {
            console.debug('Updating chat tokens due to message changes');
            updateChatTokens();
	} else {
	    console.debug('Skipping token update - no message changes detected');
	}
    }, [currentMessages, updateChatTokens]);

    const tokenDisplay = useMemo(() => (
        isLoading ? <Spin size="small" /> : (
                <>
                    <Tooltip title="Tokens from selected files">
                        <span className="token-count-item">
                            <span className="token-label">File Tokens:</span>{' '}
                            {totalTokenCount.toLocaleString()}
                        </span>
                    </Tooltip>
                    <Tooltip title="Tokens from chat history">
                        <span className="token-count-item">
                            <span className="token-label">Chat Tokens:</span>{' '}
                            {chatTokenCount.toLocaleString()}
                        </span>
                    </Tooltip>
                    <Tooltip title="Combined tokens (files + chat)">
		        <span className={getTokenCountClass(combinedTokenCount)}>
                            <span className="token-label">Total Tokens:</span>{' '}
                            {combinedTokenCount.toLocaleString()} / {TOKEN_LIMIT.toLocaleString()}
                        </span>
                    </Tooltip>
                </>
            )
    ), [isLoading, totalTokenCount, chatTokenCount, combinedTokenCount]);
 
    return (
        <div className="token-display">
            {tokenDisplay}
        </div>
    );
};

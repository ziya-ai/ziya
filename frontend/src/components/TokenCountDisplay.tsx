import React, {useEffect, useState, useCallback} from 'react';
import {Folders} from "../utils/types";
import {useFolderContext} from "../context/FolderContext";
import {Tooltip, Spin} from "antd";
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
            throw new Error('Token count request failed');
        }
        const data = await response.json();
        return data.token_count;
    } catch (error) {
        console.error('Error getting token count:', error);
        return 0;
    }
};

export const TokenCountDisplay = () => {

    const {folders, checkedKeys} = useFolderContext();
    const {messages} = useChatContext();

    const [totalTokenCount, setTotalTokenCount] = useState(0);
    const [chatTokenCount, setChatTokenCount] = useState(0);
    const [isLoading, setIsLoading] = useState(false);

    const combinedTokenCount = totalTokenCount + chatTokenCount;

    useEffect(() => {
        calculateTotalTokenCount(checkedKeys as string[]);
    }, [checkedKeys]);

    const getTokenCountClass = (count: number) => {
        if (count >= DANGER_THRESHOLD) return 'token-count-total red';
        if (count >= WARNING_THRESHOLD) return 'token-count-total orange';
        return 'token-count-total green';
    };

    const getFolderTokenCount = (filePath: string, folders: Folders) => {
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
        let totalTokenCount = 0;
        checked.forEach(item => {
            const folderTokenCount = getFolderTokenCount(item, folders!);
            totalTokenCount += folderTokenCount;
        });
        setTotalTokenCount(totalTokenCount);
    };

    const updateChatTokens = useCallback(async () => {
        if (messages.length === 0) {
            setChatTokenCount(0);
            return;
        }

        setIsLoading(true);
        try {
            const allText = messages.map(msg => msg.content).join('\n');
            const tokens = await getTokenCount(allText);
            setChatTokenCount(tokens);
        } catch (error) {
            console.error('Failed to get token count:', error);
            setChatTokenCount(0);
        } finally {
            setIsLoading(false);
        }
    }, [messages]);

    useEffect(() => {
        updateChatTokens();
    }, [updateChatTokens]);

    // Add debounce effect for token updates
    useEffect(() => {
        const timer = setTimeout(() => {
            updateChatTokens();
        }, 500); // 500ms debounce
        return () => clearTimeout(timer);
    }, [messages, updateChatTokens]);

    return (
        <div className="token-display">
            {isLoading ? <Spin size="small" /> : (
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
            )}
        </div>
    );
};

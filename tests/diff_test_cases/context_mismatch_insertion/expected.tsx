import React, { useState, useRef, useCallback, memo, useEffect, useMemo } from 'react';

interface MessageType {
    id: string;
    role: string;
    content: string;
    muted?: boolean;
    modelChange?: { from: string; to: string; changeKey: string };
}

interface ConversationProps {
    enableCodeApply: boolean;
    onOpenFile?: (path: string) => void;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply, onOpenFile }) => {
    const [messages, setMessages] = useState<MessageType[]>([]);
    const [messageWindow, setMessageWindow] = useState<number>(8);
    const scrollToMessageIndexRef = useRef<number | null>(null);
    const renderedSystemMessagesRef = useRef(new Set<string>());
    const isTopToBottom = true;

    const currentMessages = useMemo(() => {
        return messages.slice(-messageWindow);
    }, [messages, messageWindow]);

    const handleScroll = useCallback(() => {
        // scroll logic
    }, []);

    const processMessage = useCallback((msg: MessageType) => {
        return msg.content;
    }, []);

    const formatTimestamp = (ts: number) => {
        return new Date(ts).toLocaleString();
    };

    const getMessageClass = (msg: MessageType) => {
        return `message ${msg.role || ''}`;
    };

    const needsResponseCheck = (msg: MessageType, index: number, arr: MessageType[]) => {
        return msg.role === 'human' && index === arr.length - 1;
    };

    return (
        <div className="chat-container" onScroll={handleScroll}>
            <div className="messages-wrapper">
                {currentMessages.map((msg, index) => {
                    const actualIndex = isTopToBottom ? index : currentMessages.length - 1 - index;
                    const needsResponse = needsResponseCheck(msg, index, currentMessages);
                    const systemMessageKey = `${msg.id}-${msg.role}`;

                    if (process.env.NODE_ENV === 'development' &&
                        msg.role === 'system' &&
                        !renderedSystemMessagesRef.current.has(systemMessageKey)) {
                        renderedSystemMessagesRef.current.add(systemMessageKey);
                    }

                    return <div
                        // Use message ID as key instead of index
                        key={`message-${msg.id || index}`}
                        data-message-index={actualIndex}
                        className={`message ${msg.role || ''}${msg.muted ? ' muted' : ''}${needsResponse
                            ? ' needs-response' : ''
                            }`}
                    >
                        {msg.role === 'system' && msg.modelChange ? (
                            <div className="model-change">
                                Changed from {msg.modelChange.from} to {msg.modelChange.to}
                            </div>
                        ) : (
                            <div className="message-content">
                                {processMessage(msg)}
                            </div>
                        )}
                    </div>;
                })}
            </div>
        </div>
    );
});

export default Conversation;

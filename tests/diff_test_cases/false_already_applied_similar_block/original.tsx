import React, { memo, useState, useEffect, useRef, useMemo, useTransition, useCallback } from 'react';
import { LoadingOutlined } from '@ant-design/icons';
import MarkdownRenderer from './MarkdownRenderer';

interface ConversationProps {
    enableCodeApply: boolean;
    onOpenShellConfig?: () => void;
}

// This is a large component with multiple MarkdownRenderer usages
const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply, onOpenShellConfig }) => {
    const [isRawMode, setIsRawMode] = useState(false);
    const [messages, setMessages] = useState<any[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);

    const handleToggleRawMode = useCallback(() => {
        setIsRawMode(prev => !prev);
    }, []);

    // ... lots of other hooks and logic ...

    const renderRetryButton = (index: number) => {
        return (
            <button className="retry-btn" onClick={() => console.log('retry', index)}>
                Retry
            </button>
        );
    };

    const renderFeedback = (msg: any) => {
        return (
            <div className="feedback-controls">
                <button className="thumbs-up">👍</button>
                <button className="thumbs-down">👎</button>
            </div>
        );
    };

    const renderTimestamp = (ts: string) => {
        return <span className="timestamp">{new Date(ts).toLocaleTimeString()}</span>;
    };

    const renderAvatar = (role: string) => {
        return (
            <div className={`avatar avatar-${role}`}>
                {role === 'human' ? '👤' : '🤖'}
            </div>
        );
    };

    const renderCopyButton = (content: string) => {
        return (
            <button className="copy-btn" onClick={() => navigator.clipboard.writeText(content)}>
                Copy
            </button>
        );
    };

    const renderMessageActions = (msg: any, index: number) => {
        return (
            <div className="message-actions">
                {renderCopyButton(msg.content)}
                <button className="edit-btn">Edit</button>
                <button className="delete-btn">Delete</button>
            </div>
        );
    };

    const renderBranchIndicator = (msg: any) => {
        if (!msg.branches || msg.branches.length <= 1) return null;
        return (
            <div className="branch-indicator">
                <span>Branch {msg.currentBranch + 1} of {msg.branches.length}</span>
            </div>
        );
    };

    const getMessageClassName = (msg: any, index: number) => {
        const classes = ['message', `message-${msg.role}`];
        if (index === messages.length - 1) classes.push('last-message');
        return classes.join(' ');
    };

    const renderLoadingIndicator = () => {
        if (!isStreaming) return null;
        return (
            <div className="loading-indicator">
                <LoadingOutlined spin />
                <span>Generating response...</span>
            </div>
        );
    };

    const renderEmptyState = () => {
        if (messages.length > 0) return null;
        return (
            <div className="empty-state">
                <h2>Start a conversation</h2>
                <p>Type a message below to begin.</p>
            </div>
        );
    };

    // Helper functions padding
    const filterMessages = (query: string) => {
        return messages.filter(m =>
            m.content.toLowerCase().includes(query.toLowerCase())
        );
    };

    const formatMessageForExport = (msg: any) => {
        return { role: msg.role, content: msg.content, timestamp: msg.timestamp };
    };

    const exportConversation = () => {
        const exported = messages.map(formatMessageForExport);
        const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'conversation.json';
        a.click();
    };

    const scrollToBottom = useCallback(() => {
        // scroll logic
    }, []);

    const handleScroll = useCallback((e: React.UIEvent) => {
        // scroll handler
    }, []);

    const computeMessageStats = () => {
        const total = messages.length;
        const human = messages.filter(m => m.role === 'human').length;
        const assistant = messages.filter(m => m.role === 'assistant').length;
        return { total, human, assistant };
    };

    const highlightSearchTerm = (content: string, term: string) => {
        if (!term) return content;
        const regex = new RegExp(`(${term})`, 'gi');
        return content.replace(regex, '<mark>$1</mark>');
    };

    const renderStreamingContent = (content: string) => {
        return (
            <div className="streaming-content">
                <MarkdownRenderer
                    markdown={content}
                    enableCodeApply={enableCodeApply}
                    onOpenShellConfig={onOpenShellConfig}
                        isStreaming={true}
                />
                <span className="cursor-blink">▊</span>
            </div>
        );
    };

    // Main render
    return (
        <div className="conversation-container">
            {renderEmptyState()}
            {renderLoadingIndicator()}
            <div className="messages-list">
                {messages.map((msg, actualIndex) => {
                    return (
                        <div key={actualIndex} className={getMessageClassName(msg, actualIndex)}>
                            {msg.role === 'assistant' && msg.isCompact ? (
                                <>
                                    {renderBranchIndicator(msg)}
                                    <div className="message-header">
                                        {renderAvatar(msg.role)}
                                        {renderTimestamp(msg.timestamp)}
                                        {renderRetryButton(actualIndex)}
                                    </div>
                                    <div className="message-content">
                                                {isRawMode ? (
                                                    <pre className="raw-markdown-view">{msg.content}</pre>
                                                ) : (
                                                    <MarkdownRenderer
                                                        markdown={msg.content}
                                                        enableCodeApply={enableCodeApply}
                                                        onOpenShellConfig={onOpenShellConfig}
                                                            isStreaming={false}
                                                    />
                                                )}
                                    </div>
                                </>
                            ) : null}

                            {msg.role === 'assistant' && !msg.isCompact ? (
                                <>
                                    {renderBranchIndicator(msg)}
                                    <div className="message-header">
                                        {renderAvatar(msg.role)}
                                        {renderTimestamp(msg.timestamp)}
                                        {renderRetryButton(actualIndex)}
                                    </div>
                                    <div className="message-content">
                                        <MarkdownRenderer
                                            markdown={msg.content}
                                            enableCodeApply={enableCodeApply}
                                            onOpenShellConfig={onOpenShellConfig}
                                                isStreaming={false}
                                        />
                                    </div>
                                </>
                            ) : null}

                            {msg.role === 'human' ? (
                                <>
                                    <div className="message-header">
                                        {renderAvatar(msg.role)}
                                        {renderTimestamp(msg.timestamp)}
                                    </div>
                                    <div className="message-content">
                                        <p>{msg.content}</p>
                                    </div>
                                </>
                            ) : null}

                            {renderMessageActions(msg, actualIndex)}
                            {renderFeedback(msg)}
                        </div>
                    );
                })}
            </div>
        </div>
    );
});

export default Conversation;

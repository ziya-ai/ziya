import React, { Suspense, useMemo, useEffect, useRef, memo, Component } from "react";
import { Spin } from 'antd';
import {EditSection} from "./EditSection";
import {RetrySection} from "./RetrySection";
import {useChatContext} from '../context/ChatContext';

// Lazy load the MarkdownRenderer
const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

class MessageErrorBoundary extends Component<{children: React.ReactNode}, {hasError: boolean}> {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true };
    }

    componentDidCatch(error, errorInfo) {
        console.error('Error rendering message:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="message error">
                    Error rendering message. Please try refreshing the page.
                </div>
            );
        }
        return this.props.children;
    }
}

interface ConversationProps {
    enableCodeApply: boolean;
}

const Conversation: React.FC<ConversationProps> = memo (({ enableCodeApply }) => {
    const {messages, isTopToBottom, isLoadingConversation} = useChatContext();
    // In top-to-bottom mode, show messages in chronological order
    // In bottom-to-top mode, show messages in reverse chronological order

    // Sort messages by sequence number to maintain strict ordering
    const displayMessages = useMemo(() => {
	const sorted = [...messages].sort((a, b) => {
            // First compare by sequence
            if (a.sequence !== b.sequence) {
                return a.sequence - b.sequence;
            }
            // If sequences are equal, use timestamp as tiebreaker
            return a.timestamp - b.timestamp;
        });
        return isTopToBottom ? sorted : sorted.reverse();
    }, [messages, isTopToBottom]);

    // Keep track of rendered messages for performance monitoring
    const renderedCountRef = useRef(0);

    useEffect(() => {
        if (messages.length !== renderedCountRef.current) {
            renderedCountRef.current = messages.length;
            console.log(`Rendered ${messages.length} messages`);
        }
    }, [messages.length]);

    // Loading indicator text based on progress
    const loadingText = useMemo(() => {
        if (!isLoadingConversation) return '';

        const progress = messages.length > 0
            ? `Loading messages (${messages.length} loaded)...`
            : 'Loading conversation...';

        return progress;
    }, [isLoadingConversation, messages.length]);

    // Progressive loading indicator
    const showProgressiveLoading = isLoadingConversation && messages.length > 0;

    // Track whether we're in the initial loading state
    const isInitialLoading = isLoadingConversation && messages.length === 0;

    // Memoize the message content to prevent unnecessary re-renders
    const messageContent = useMemo(() => (
        displayMessages.map((msg, index) => (
	    <div key={index} className={`message ${msg.role}`}>
                {msg.role === 'human' ? (
                    <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <div className="message-sender">You:</div>
                        <EditSection index={messages.length - 1 - index}/>
                    </div>
                ) : (
                    <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <div className="message-sender">AI:</div>
                        <div style={{alignSelf: 'flex-end'}}>
                            <RetrySection index={messages.length - 1 - index}/>
                        </div>
                    </div>
                )}
                <div className="message-content">
                    <Suspense fallback={<div>Loading content...</div>}>
                        <MarkdownRenderer 
                            markdown={msg.content} 
                            enableCodeApply={enableCodeApply}
                        />
                    </Suspense>
                </div>
	    </div>
        ))
    ), [displayMessages, messages.length, enableCodeApply]);

    return (
	<div style={{ position: 'relative' }}>
	    {isInitialLoading && (
		<div style={{
                    position: 'fixed',
                    top: 'var(--header-height)',
                    left: 'var(--folder-panel-width)',
                    right: 0,
                    bottom: 0,
                    backgroundColor: 'rgba(0, 0, 0, 0.5)',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
		    zIndex: 1000
                }}>
		    <Spin size="large" tip={loadingText} />
                </div>
            )}
            <div style={{ opacity: isLoadingConversation ? 0.5 : 1 }}>
	        {showProgressiveLoading && (
                    <div style={{
                        position: 'sticky',
                        top: 0,
                        backgroundColor: 'rgba(0, 0, 0, 0.7)',
                        color: '#fff',
                        padding: '8px 16px',
                        borderRadius: '4px',
                        margin: '8px 0',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        zIndex: 1000
                    }}>
                        <Spin size="small" />
                        <span>{loadingText}</span>
                    </div>
                )}
		<div>
                    {messageContent}
		</div>
            </div>
        </div>
    );
});

export default Conversation;

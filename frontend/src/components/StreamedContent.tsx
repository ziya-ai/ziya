import React, { useEffect, Suspense, useState } from 'react';
import {useChatContext} from '../context/ChatContext';
import { Space, Alert } from 'antd';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';

const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const {
        streamedContentMap,
	isStreaming,
        currentConversationId, 
        streamingConversations,
        currentMessages,
	isTopToBottom
    } = useChatContext();

    const LoadingIndicator = () => (
	<Space>
	    <div style={{
                padding: '20px',
                textAlign: 'center',
                color: 'var(--loading-color, #1890ff)',
                width: '100%', 
		order: isTopToBottom ? 0 : -1  // Place at top if bottom-up view
            }} className="loading-indicator">
            <Space>
                <RobotOutlined style={{ fontSize: '24px', animation: 'pulse 2s infinite' }} />
                <LoadingOutlined spin />
                <span style={{
                    animation: 'fadeInOut 2s infinite',
                    display: 'inline-block',
                    fontSize: '16px',
                    marginLeft: '8px',
                    verticalAlign: 'middle'
                }}>Processing response...</span>
            </Space>
        </div>
        </Space>
    );

    const ErrorDisplay = ({ message }: { message: string }) => (
        <Alert
            message="Error"
            description={message}
            type="error"
            showIcon
            className="stream-error"
            style={{ margin: '20px 0' }}
        />
    );

    // Reset error when new content starts streaming
    useEffect(() => {
        if (isStreaming) {
            setError(null);
        }
    }, [isStreaming]);

    // Add effect to handle conversation switches
    useEffect(() => {
        // Force scroll event to trigger re-render
        const triggerScroll = () => {
            window.requestAnimationFrame(() => {
                window.dispatchEvent(new CustomEvent('scroll'));
                // Force another scroll after a short delay to ensure content is visible
                setTimeout(() => window.dispatchEvent(new CustomEvent('scroll')), 100);
            });
        };
        triggerScroll();
    }, [currentConversationId, streamedContentMap]);

    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <div style={{
            display: 'flex',
            // In bottom-up view, reverse the order of elements
            flexDirection: isTopToBottom ? 'column' : 'column-reverse'
        }}>
	      {streamingConversations.has(currentConversationId) &&
              !currentMessages.some(msg => msg.role === 'assistant' &&
				    msg.content === streamedContentMap.get(currentConversationId)) && (

                <div className="message assistant">
                    <div className="message-sender">AI:</div>
		    <Suspense fallback={<div>Loading content...</div>}>
                        {error ? (
                            <ErrorDisplay message={error} />
                        ) : (
                            <MarkdownRenderer
                                markdown={streamedContentMap.get(currentConversationId) || ''}
                                enableCodeApply={enableCodeApply}
                            />
                        )}
                    </Suspense>
                </div>
            )}

	    {/* Loading indicator - shown at bottom in top-down mode, top in bottom-up mode */}
	    {streamingConversations.has(currentConversationId) &&
              // Only show loading indicator if we don't have any streamed content yet
              (!streamedContentMap.has(currentConversationId) ||
               streamedContentMap.get(currentConversationId) === '') && (
                <LoadingIndicator />
            )}
        </div>
    );
};

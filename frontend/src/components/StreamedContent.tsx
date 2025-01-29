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
        currentMessages
    } = useChatContext();

    const LoadingIndicator = () => (
	<Space>
            <RobotOutlined 
                style={{ 
                    fontSize: '24px',
                    animation: 'pulse 2s infinite'
                }}>
		</RobotOutlined>
            <LoadingOutlined spin />
            <span style={{
                animation: 'fadeInOut 2s infinite',
                display: 'inline-block',
                fontSize: '16px',
                marginLeft: '8px',
                verticalAlign: 'middle'
            }}>
                Processing response...
            </span>
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

    // Debug when content should be displayed
    useEffect(() => {
        console.debug('StreamedContent state:', {
	    hasContent: Boolean(streamedContentMap.get(currentConversationId)),
	    currentContent: streamedContentMap.get(currentConversationId),
            isStreaming,
            currentConversationId,
            isStreamingCurrent: streamingConversations.has(currentConversationId)
        });
    }, [streamedContentMap, isStreaming, currentConversationId, streamingConversations]);

    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <>
	    {streamingConversations.has(currentConversationId) && (
                <div className="message assistant">
                    <div className="message-sender">AI:</div>
                    {error && <ErrorDisplay message={error} />}
		    {!streamedContentMap.get(currentConversationId) ? (
                        <LoadingIndicator />
                    ) : (
                        <Suspense fallback={<div>Loading content...</div>}>
                            <MarkdownRenderer
			        markdown={streamedContentMap.get(currentConversationId) || ''}
				key={`${currentConversationId}-${streamedContentMap.get(currentConversationId)?.length}`}
				enableCodeApply={enableCodeApply}
			    />
                        </Suspense>
                    )}
                </div>
            )}
        </>
    );
};

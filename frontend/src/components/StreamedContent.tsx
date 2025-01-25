import React, { useEffect, Suspense, useState } from 'react';
import {useChatContext} from '../context/ChatContext';
import { Space, Alert } from 'antd';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';

const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const {
        streamedContent, 
        isStreaming, 
        currentConversationId, 
        streamingConversationId,
        currentMessages
    } = useChatContext();

    const LoadingIndicator = () => (
        <div style={{ 
            padding: '20px', 
            textAlign: 'center',
            color: 'var(--loading-color, #1890ff)'
        }} className="loading-indicator">
            <Space>
                <RobotOutlined 
                    style={{ 
                        fontSize: '24px',
                        animation: 'pulse 2s infinite'
                    }} 
                />
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
        </div>
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
    }, [currentConversationId, streamedContent]);

    // Debug when content should be displayed
    useEffect(() => {
        console.debug('StreamedContent state:', {
            hasContent: Boolean(streamedContent),
            isStreaming,
            currentConversationId,
            streamingConversationId
        });
    }, [streamedContent, isStreaming, currentConversationId, streamingConversationId]);

    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <>
            {(isStreaming || (streamedContent && streamingConversationId === currentConversationId)) && (
                <div className="message assistant">
                    <div className="message-sender" style={{ marginTop: 0 }}>AI:</div>
                    {error && <ErrorDisplay message={error} />}
                    {!streamedContent ? (
                        <LoadingIndicator />
                    ) : (
                        <Suspense fallback={<div>Loading content...</div>}>
                            <MarkdownRenderer
                                markdown={streamedContent || ''}
                                enableCodeApply={enableCodeApply}/>
                        </Suspense>
                    )}
                </div>
            )}
        </>
    );
};

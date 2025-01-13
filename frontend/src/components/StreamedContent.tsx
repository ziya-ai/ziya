import React, { useEffect, Suspense, useState } from 'react';
import {useChatContext} from '../context/ChatContext';
import { Space, Alert } from 'antd';
import { RobotOutlined, LoadingOutlined } from '@ant-design/icons';

const MarkdownRenderer = React.lazy(() => import("./MarkdownRenderer"));

export const StreamedContent: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const {streamedContent, isStreaming} = useChatContext();

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

    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <>
            {isStreaming && (
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

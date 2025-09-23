import React, { useState } from 'react';
import { Card, Typography, Button } from 'antd';
import { EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';

const { Text } = Typography;

interface ReasoningDisplayProps {
    conversationId: string;
}

export const ReasoningDisplay: React.FC<ReasoningDisplayProps> = ({ conversationId }) => {
    const [isVisible, setIsVisible] = useState<boolean>(true);
    const { reasoningContentMap } = useChatContext();
    
    const reasoningContent = reasoningContentMap.get(conversationId) || '';

    if (!reasoningContent) {
        return null;
    }

    return (
        <Card 
            size="small" 
            style={{ 
                marginBottom: 8, 
                backgroundColor: '#f8f9fa',
                border: '1px solid #e9ecef'
            }}
            title={
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text type="secondary" style={{ fontSize: '12px' }}>
                        Background Reasoning
                    </Text>
                    <Button 
                        type="text" 
                        size="small"
                        icon={isVisible ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                        onClick={() => setIsVisible(!isVisible)}
                    />
                </div>
            }
        >
            {isVisible && (
                <div style={{ 
                    fontSize: '11px', 
                    color: '#6c757d',
                    fontFamily: 'monospace',
                    whiteSpace: 'pre-wrap',
                    maxHeight: '200px',
                    overflowY: 'auto'
                }}>
                    {reasoningContent}
                </div>
            )}
        </Card>
    );
};

export default ReasoningDisplay;

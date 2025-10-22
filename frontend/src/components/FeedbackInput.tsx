import React, { useState, useEffect, useRef } from 'react';
import { Input, Button, Card, Typography, message } from 'antd';
import { SendOutlined, StopOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface FeedbackInputProps {
    conversationId: string;
    isStreaming: boolean;
}

const FeedbackInput: React.FC<FeedbackInputProps> = ({ conversationId, isStreaming }) => {
    const [feedbackText, setFeedbackText] = useState('');
    const [activeTool, setActiveTool] = useState<{toolId: string, toolName: string} | null>(null);
    const [isVisible, setIsVisible] = useState(false);
    const inputRef = useRef<any>(null);

    // Listen for feedback readiness events
    useEffect(() => {
        const handleFeedbackReady = (event: CustomEvent) => {
            const { toolId, toolName, conversationId: eventConvId } = event.detail;
            
            if (eventConvId === conversationId) {
                setActiveTool({ toolId, toolName });
                setIsVisible(true);
                console.log('🔄 FEEDBACK: UI enabled for tool:', toolName);
            }
        };

        const handleToolComplete = () => {
            setActiveTool(null);
            setIsVisible(false);
            setFeedbackText('');
        };

        document.addEventListener('feedbackReady', handleFeedbackReady as EventListener);
        document.addEventListener('toolComplete', handleToolComplete as EventListener);

        return () => {
            document.removeEventListener('feedbackReady', handleFeedbackReady as EventListener);
            document.removeEventListener('toolComplete', handleToolComplete as EventListener);
        };
    }, [conversationId]);

    // Hide when streaming stops
    useEffect(() => {
        if (!isStreaming) {
            setIsVisible(false);
            setActiveTool(null);
        }
    }, [isStreaming]);

    const sendFeedback = () => {
        if (!feedbackText.trim() || !activeTool) return;
        
        console.log('🔄 FEEDBACK: Attempting to send feedback:', feedbackText);
        
        // Send feedback via WebSocket
        const feedbackWS = (window as any).feedbackWebSocket;
        console.log('🔄 FEEDBACK: WebSocket available:', !!feedbackWS);
        console.log('🔄 FEEDBACK: WebSocket state:', feedbackWS?.ws?.readyState);
        
        if (feedbackWS) {
            feedbackWS.sendFeedback(activeTool.toolId, feedbackText);
            
            // Show visual confirmation
            message.success({
                content: `Feedback sent to ${activeTool.toolName}: "${feedbackText}"`,
                duration: 2
            });
            
            // Clear input and provide visual feedback
            setFeedbackText('');
            console.log('🔄 FEEDBACK: Sent to tool:', activeTool.toolName);
        }
    };

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendFeedback();
        }
    };

    if (!isVisible || !activeTool) {
        return null;
    }

    return (
        <Card 
            size="small"
            style={{
                position: 'fixed',
                bottom: '80px',
                right: '20px',
                width: '320px',
                zIndex: 1000,
                boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                border: '2px solid #1890ff'
            }}
            bodyStyle={{ padding: '12px' }}
        >
            <div style={{ marginBottom: '8px' }}>
                <Text strong style={{ color: '#1890ff' }}>
                    💬 Provide feedback to: {activeTool.toolName}
                </Text>
            </div>
            
            <div style={{ display: 'flex', gap: '8px' }}>
                <Input.TextArea
                    ref={inputRef}
                    value={feedbackText}
                    onChange={(e) => setFeedbackText(e.target.value)}
                    onKeyPress={handleKeyPress}
                    placeholder="Type feedback (Enter to send, Shift+Enter for newline)"
                    autoSize={{ minRows: 1, maxRows: 3 }}
                    style={{ flex: 1 }}
                />
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    <Button
                        type="primary"
                        icon={<SendOutlined />}
                        onClick={sendFeedback}
                        disabled={!feedbackText.trim()}
                        size="small"
                    />
                    <Button
                        icon={<StopOutlined />}
                        onClick={() => setIsVisible(false)}
                        size="small"
                        title="Hide feedback"
                    />
                </div>
                
                {/* Debug button - remove in production */}
                <Button
                    type="dashed"
                    size="small"
                    onClick={() => {
                        console.log('🔄 FEEDBACK DEBUG:', { activeTool, feedbackText, wsReady: (window as any).feedbackWebSocketReady });
                    }}
                >
                    Debug
                </Button>
            </div>
        </Card>
    );
};

export default FeedbackInput;

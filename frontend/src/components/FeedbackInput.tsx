import React, { useState, useEffect, useRef } from 'react';
import { Input, Button, Card, Typography } from 'antd';
import { SendOutlined, CloseOutlined } from '@ant-design/icons';

const { TextArea } = Input;
const { Text } = Typography;

interface FeedbackInputProps {
    conversationId: string;
    isStreaming: boolean;
}

interface FeedbackReadyEvent {
    toolId: string;
    toolName: string;
    conversationId: string;
}

const FeedbackInput: React.FC<FeedbackInputProps> = ({ conversationId, isStreaming }) => {
    const [isVisible, setIsVisible] = useState(false);
    const [feedbackText, setFeedbackText] = useState('');
    const [currentToolId, setCurrentToolId] = useState<string | null>(null);
    const [currentToolName, setCurrentToolName] = useState<string | null>(null);
    const [isSending, setIsSending] = useState(false);
    const textAreaRef = useRef<any>(null);

    useEffect(() => {
        const handleFeedbackReady = (event: CustomEvent<FeedbackReadyEvent>) => {
            const { toolId, toolName, conversationId: eventConversationId } = event.detail;
            
            // Only show feedback input for the current conversation
            if (eventConversationId === conversationId) {
                setCurrentToolId(toolId);
                setCurrentToolName(toolName);
                setIsVisible(true);
                setFeedbackText('');
                
                // Focus the text area after a short delay
                setTimeout(() => {
                    textAreaRef.current?.focus();
                }, 100);
            }
        };

        document.addEventListener('feedbackReady', handleFeedbackReady as EventListener);

        return () => {
            document.removeEventListener('feedbackReady', handleFeedbackReady as EventListener);
        };
    }, [conversationId]);

    const sendFeedback = async () => {
        if (!currentToolId || !feedbackText.trim() || isSending) return;

        setIsSending(true);

        try {
            // Use the global WebSocket if available
            const feedbackWebSocket = (window as any).feedbackWebSocket;
            if (feedbackWebSocket && (window as any).feedbackWebSocketReady) {
                feedbackWebSocket.sendFeedback(currentToolId, feedbackText.trim());
                console.log('🔄 FEEDBACK: Sent feedback for tool:', currentToolId, 'Message:', feedbackText.trim());
            } else {
                console.error('🔄 FEEDBACK: WebSocket not ready or not available');
            }

            // Close the feedback input
            closeFeedback();
        } catch (error) {
            console.error('🔄 FEEDBACK: Error sending feedback:', error);
        } finally {
            setIsSending(false);
        }
    };

    const closeFeedback = () => {
        setIsVisible(false);
        setFeedbackText('');
        setCurrentToolId(null);
        setCurrentToolName(null);
    };

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            sendFeedback();
        } else if (e.key === 'Escape') {
            closeFeedback();
        }
    };

    if (!isVisible || !currentToolName) return null;

    return (
        <Card
            size="small"
            style={{
                position: 'fixed',
                bottom: '20px',
                right: '20px',
                width: '400px',
                maxWidth: '90vw',
                zIndex: 1001,
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)'
            }}
            title={
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Text strong>Provide feedback for {currentToolName}</Text>
                    <Button type="text" size="small" icon={<CloseOutlined />} onClick={closeFeedback} />
                </div>
            }
        >
            <TextArea
                ref={textAreaRef}
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                onKeyDown={handleKeyPress}
                placeholder="Type your feedback here... (Ctrl+Enter to send, Esc to close)"
                rows={3}
                style={{ marginBottom: '12px' }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <Button onClick={closeFeedback}>Cancel</Button>
                <Button
                    type="primary"
                    icon={<SendOutlined />}
                    onClick={sendFeedback}
                    loading={isSending}
                    disabled={!feedbackText.trim() || isSending}
                >
                    Send Feedback
                </Button>
            </div>
        </Card>
    );
};

export default FeedbackInput;

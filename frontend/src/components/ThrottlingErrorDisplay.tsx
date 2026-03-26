import React, { useState, useEffect, useCallback } from 'react';
import { Alert, Button, Space, Typography, Progress } from 'antd';
import { ReloadOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import { useSendPayload } from '../hooks/useSendPayload';

const { Text, Paragraph } = Typography;

interface ThrottlingErrorDisplayProps {
  error: {
    error: string;
    detail: string;
    status_code: number;
    retry_after?: string;
    throttle_info?: {
      auto_attempts_exhausted?: boolean;
      total_auto_attempts?: number;
      can_user_retry?: boolean;
      backoff_used?: number[];
    };
    user_message?: string;
    preserved_content?: string;
    originalRequestData?: {
      messages: any[];
      question: string;
      checkedItems: string[];
      conversationId: string;
    };
  };
  onDismiss?: () => void;
}

export const ThrottlingErrorDisplay: React.FC<ThrottlingErrorDisplayProps> = ({
  error,
  onDismiss
}) => {
  const { isDarkMode } = useTheme();
  const [countdown, setCountdown] = useState(60);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isWaitingForRetry, setIsWaitingForRetry] = useState(false);
  const { send } = useSendPayload();

  const suggestedWaitTime = parseInt(error.retry_after || '60');
  const throttleInfo = error.throttle_info;

  useEffect(() => {
    setCountdown(suggestedWaitTime);
  }, [suggestedWaitTime]);

  useEffect(() => {
    if (!isWaitingForRetry || countdown <= 0) return;

    const timer = setInterval(() => {
      setCountdown(prev => Math.max(0, prev - 1));
    }, 1000);

    return () => clearInterval(timer);
  }, [isWaitingForRetry, countdown]);

  const handleRetryNow = useCallback(async () => {
    if (!error.originalRequestData || isRetrying) return;
    
    setIsRetrying(true);
    onDismiss?.();
    
    try {
      const { messages, question, checkedItems, conversationId } = error.originalRequestData;
      
      await send({
        messages,
        question,
        checkedItems,
        conversationId,
        isStreamingToCurrentConversation: true,
        includeReasoning: true,
      });
    } catch (retryError) {
      console.error('Retry failed:', retryError);
    } finally {
      setIsRetrying(false);
    }
  }, [error.originalRequestData, isRetrying, onDismiss, send]);

  const handleWaitAndRetry = useCallback(() => {
    setIsWaitingForRetry(true);
  }, []);

  // Auto-retry when countdown reaches zero after user clicked "Wait Ns then retry"
  useEffect(() => {
    if (isWaitingForRetry && countdown === 0) {
      setIsWaitingForRetry(false);
      handleRetryNow();
    }
  }, [isWaitingForRetry, countdown, handleRetryNow]);

  return (
    <Alert
      type="warning"
      showIcon
      message="AWS Bedrock Rate Limit Exceeded"
      style={{ margin: '8px 0' }}
      description={
        <Space direction="vertical" style={{ width: '100%' }}>
          <Paragraph style={{ margin: 0 }}>{error.detail}</Paragraph>
          
          {throttleInfo?.backoff_used && (
            <Text type="secondary" style={{ fontSize: '12px' }}>
              System attempted {throttleInfo.total_auto_attempts} retries with delays: {throttleInfo.backoff_used.join('s, ')}s
            </Text>
          )}

          {isWaitingForRetry && countdown > 0 && (
            <div>
              <Text><ClockCircleOutlined style={{ marginRight: 8 }} />Wait time remaining: {countdown}s</Text>
              <Progress
                percent={((suggestedWaitTime - countdown) / suggestedWaitTime) * 100}
                size="small"
                status="active"
                showInfo={false}
                style={{ marginTop: 4 }}
              />
            </div>
          )}

          <Space>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              onClick={handleRetryNow}
              loading={isRetrying}
              size="small"
            >
              Retry Now
            </Button>
            
            {!isWaitingForRetry && (
              <Button
                type="default"
                icon={<ClockCircleOutlined />}
                onClick={handleWaitAndRetry}
                size="small"
              >
                Wait {suggestedWaitTime}s then retry
              </Button>
            )}
          </Space>
        </Space>
      }
    />
  );
};

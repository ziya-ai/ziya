import React from 'react';
import { Button, Tooltip } from 'antd';
import { useChatContext } from '../context/ChatContext';

interface StopStreamButtonProps {
  conversationId: string;
  size?: 'small' | 'middle' | 'large';
  onStop?: () => void;
  style?: React.CSSProperties;
}

const StopStreamButton: React.FC<StopStreamButtonProps> = ({
  conversationId,
  size = 'small',
  onStop,
  style = {}
}) => {
  const {
    streamingConversations,
    removeStreamingConversation,
    setIsStreaming
  } = useChatContext();

  const isStreaming = streamingConversations.has(conversationId);

  if (!isStreaming) {
    return null;
  }

  const handleStopStream = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    // Use direct stop function if provided
    if (onStop) {
      onStop();
      return;
    }


    // Abort the fetch request (this will be handled in the API layer)
    const abortEvent = new CustomEvent('abortStream', {
      detail: { conversationId }
    });
    console.log('StopStreamButton: Dispatching abortStream event for conversation:', conversationId);
    document.dispatchEvent(abortEvent);

    // Also explicitly notify the server about the abort via API call
    try {
      fetch('/api/abort-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversationId }),
      })
        .then(response => console.log('Abort API response:', response.status))
        .catch(e => console.warn('Error sending abort notification to server:', e));
    } catch (e) { }

    // Also directly update the state (this should trigger UI updates)
    removeStreamingConversation(conversationId);
    console.log('StopStreamButton: Removed from streaming conversations');
    setIsStreaming(false);
  };

  // Custom stop sign icon (octagonal US stop sign)
  const StopSignIcon = () => (
    <svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Proper octagon with flat sides on top/bottom/left/right */}
      <path d="M7,1 H15 L21,7 V15 L15,21 H7 L1,15 V7 Z"
        fill="#b37882" />
      <text x="11" y="14"
        textAnchor="middle"
        fill="white"
        style={{
          font: 'bold 7px Arial',
          userSelect: 'none'
        }}>
        STOP
      </text>
    </svg>
  );

  return (
    <Tooltip title="Stop generating">
      <Button
        type="default"
        size={size}
        icon={<StopSignIcon />}
        onClick={handleStopStream}
        style={{
          border: 'none',
          background: 'transparent',
          boxShadow: 'none',
          padding: '4px',
          cursor: 'pointer',
          verticalAlign: 'middle',
          pointerEvents: 'auto',
          ...style
        }}
        aria-label="Stop generating"
      />
    </Tooltip>
  );
};

export default StopStreamButton;

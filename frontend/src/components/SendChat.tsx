/**
 * Hook for sending chat messages with context integration
 */
import { useCallback } from 'react';
import { useActiveChat } from '../context/ActiveChatContext';
import { useSendPayload } from '../hooks/useSendPayload';

export function useSendChat() {
  const {
    currentConversationId,
    addMessageToConversation,
    addStreamingConversation,
  } = useActiveChat();
  const { send } = useSendPayload();
  
  const sendMessage = useCallback(async (messageText: string) => {
    const userMessage = {
      role: 'human' as const,
      content: messageText,
      _timestamp: Date.now()
    };
    addMessageToConversation(userMessage, currentConversationId);
    addStreamingConversation(currentConversationId);
    
    return send({ question: messageText });
  }, [currentConversationId, addMessageToConversation, addStreamingConversation, send]);
  
  return { send: sendMessage };
}

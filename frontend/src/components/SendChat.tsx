/**
 * Hook for sending chat messages with context integration
 */
import { useCallback } from 'react';
import { useChatContext } from '../context/ChatContext';
import { useProject } from '../context/ProjectContext';
import { useFolderContext } from '../context/FolderContext';
import { sendPayload } from '../apis/chatApi';

export function useSendChat() {
  const {
    currentConversationId,
    currentMessages,
    addMessageToConversation,
    streamedContentMap,
    setStreamedContentMap,
    setIsStreaming,
    removeStreamingConversation,
    addStreamingConversation,
    streamingConversations,
    updateProcessingState,
  } = useChatContext();
  
  const { checkedKeys } = useFolderContext();
  const { activeSkillPrompts, currentProject } = useProject();
  
  const send = useCallback(async (messageText: string) => {
    const userMessage = {
      role: 'human' as const,
      content: messageText,
      _timestamp: Date.now()
    };
    addMessageToConversation(userMessage, currentConversationId);
    
    addStreamingConversation(currentConversationId);
    
    return sendPayload(
      currentMessages.filter(m => !m.muted),
      messageText,
      Array.from(checkedKeys).map(String),
      currentConversationId,
      activeSkillPrompts,
      undefined, // images - not supported in this hook
      streamedContentMap,
      setStreamedContentMap,
      setIsStreaming,
      removeStreamingConversation,
      addMessageToConversation,
      streamingConversations.has(currentConversationId),
      (state) => updateProcessingState(currentConversationId, state),
      undefined, // setReasoningContentMap
      undefined, // throttlingRecoveryDataRef
      currentProject
    );
  }, [
    currentConversationId,
    currentMessages,
    checkedKeys,
    activeSkillPrompts,
    addMessageToConversation,
    addStreamingConversation,
    streamedContentMap,
    setStreamedContentMap,
    setIsStreaming,
    removeStreamingConversation,
    streamingConversations,
    updateProcessingState,
    currentProject
  ]);
  
  return { send };
}

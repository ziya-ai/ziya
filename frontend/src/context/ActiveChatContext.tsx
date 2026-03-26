/**
 * ActiveChatContext — slice context for the *current* conversation's
 * message state, streaming maps, editing state, and streaming mutations.
 *
 * Components rendering the active chat (Conversation, StreamedContent,
 * SendChat, EditSection) need currentMessages and streamedContentMap,
 * but do NOT need the full conversations[] list or folder CRUD.
 *
 * Separating these prevents folder/list-level changes from forcing
 * the message rendering pipeline to re-run.
 *
 * ChatProvider owns the actual state; this context is a narrow
 * pass-through — same pattern as StreamingContext.
 */
import React, { createContext, useContext, useMemo, Dispatch, SetStateAction } from 'react';
import { Message } from '../utils/types';
import type { ProcessingState } from './ChatContext';

export interface ActiveChatContextValue {
  currentConversationId: string;
  currentMessages: Message[];
  setCurrentConversationId: (id: string) => void;
  addMessageToConversation: (message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => void;
  loadConversation: (id: string) => void;
  loadConversationAndScrollToMessage: (conversationId: string, messageIndex: number) => Promise<void>;
  startNewChat: (specificFolderId?: string | null) => Promise<void>;
  editingMessageIndex: number | null;
  setEditingMessageIndex: (index: number | null) => void;
  // Streaming mutations (write access for components that start/stop streams)
  isStreaming: boolean;
  setIsStreaming: Dispatch<SetStateAction<boolean>>;
  streamingConversations: Set<string>;
  addStreamingConversation: (id: string) => void;
  removeStreamingConversation: (id: string) => void;
  // Streaming content maps
  streamedContentMap: Map<string, string>;
  setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>;
  reasoningContentMap: Map<string, string>;
  setReasoningContentMap: Dispatch<SetStateAction<Map<string, string>>>;
  // Processing state
  getProcessingState: (conversationId: string) => ProcessingState;
  updateProcessingState: (conversationId: string, state: ProcessingState) => void;
  // Misc active-chat state
  dynamicTitleLength: number;
  setDynamicTitleLength: (length: number) => void;
  lastResponseIncomplete: boolean;
  setDisplayMode: (conversationId: string, mode: 'raw' | 'pretty') => void;
  toggleMessageMute: (conversationId: string, messageIndex: number) => void;
  setChatContexts: (chatId: string, contextIds: string[], skillIds: string[], additionalFiles: string[], additionalPrompt: string | null) => Promise<void>;
  currentDisplayMode: 'raw' | 'pretty';
  throttlingRecoveryData: Map<string, { toolResults?: any[]; partialContent?: string }>;
  setThrottlingRecoveryData: (data: Map<string, any>) => void;
}

const ActiveChatContext = createContext<ActiveChatContextValue | undefined>(undefined);

export const ActiveChatProvider: React.FC<
  ActiveChatContextValue & { children: React.ReactNode }
> = ({ children, ...value }) => {
  const memoized = useMemo(
    () => ({
      currentConversationId: value.currentConversationId,
      currentMessages: value.currentMessages,
      setCurrentConversationId: value.setCurrentConversationId,
      addMessageToConversation: value.addMessageToConversation,
      loadConversation: value.loadConversation,
      loadConversationAndScrollToMessage: value.loadConversationAndScrollToMessage,
      startNewChat: value.startNewChat,
      editingMessageIndex: value.editingMessageIndex,
      setEditingMessageIndex: value.setEditingMessageIndex,
      isStreaming: value.isStreaming,
      setIsStreaming: value.setIsStreaming,
      streamingConversations: value.streamingConversations,
      addStreamingConversation: value.addStreamingConversation,
      removeStreamingConversation: value.removeStreamingConversation,
      streamedContentMap: value.streamedContentMap,
      setStreamedContentMap: value.setStreamedContentMap,
      reasoningContentMap: value.reasoningContentMap,
      setReasoningContentMap: value.setReasoningContentMap,
      getProcessingState: value.getProcessingState,
      updateProcessingState: value.updateProcessingState,
      dynamicTitleLength: value.dynamicTitleLength,
      setDynamicTitleLength: value.setDynamicTitleLength,
      lastResponseIncomplete: value.lastResponseIncomplete,
      setDisplayMode: value.setDisplayMode,
      toggleMessageMute: value.toggleMessageMute,
      currentDisplayMode: value.currentDisplayMode,
      setChatContexts: value.setChatContexts,
      throttlingRecoveryData: value.throttlingRecoveryData,
      setThrottlingRecoveryData: value.setThrottlingRecoveryData,
    }),
    [
      value.currentConversationId,
      value.currentMessages,
      value.editingMessageIndex,
      value.isStreaming,
      value.streamingConversations,
      value.streamedContentMap,
      value.reasoningContentMap,
      value.dynamicTitleLength,
      value.lastResponseIncomplete,
      value.throttlingRecoveryData,
      value.currentDisplayMode,
      // Stable callbacks — included for correctness, won't trigger re-renders
      value.setCurrentConversationId, value.addMessageToConversation,
      value.loadConversation, value.loadConversationAndScrollToMessage,
      value.startNewChat, value.setEditingMessageIndex,
      value.setIsStreaming, value.addStreamingConversation,
      value.removeStreamingConversation,
      value.setStreamedContentMap, value.setReasoningContentMap,
      value.getProcessingState, value.updateProcessingState,
      value.setDynamicTitleLength, value.setDisplayMode,
      value.toggleMessageMute, value.setChatContexts,
      value.setThrottlingRecoveryData,
    ]
  );

  return (
    <ActiveChatContext.Provider value={memoized}>
      {children}
    </ActiveChatContext.Provider>
  );
};

export function useActiveChat(): ActiveChatContextValue {
  const ctx = useContext(ActiveChatContext);
  if (!ctx) throw new Error('useActiveChat must be used within ActiveChatProvider');
  return ctx;
}

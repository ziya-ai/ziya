/**
 * StreamingContext — lightweight context for streaming state only.
 *
 * MarkdownRenderer sub-components (DiffToken, DiffViewWrapper, CodeBlock,
 * EnhancedDiffView) previously called useChatContext() just to read
 * isStreaming / currentConversationId.  Because ChatContext includes
 * `conversations` (which changes on every poll, sync, and read-mark),
 * every sub-component re-rendered on every conversations mutation —
 * bypassing React.memo.
 *
 * Delegate messages with 20+ diffs create ~80 context subscribers.
 * Each conversations change forced all 80 to re-run their full component
 * function (diff parsing, syntax highlighting), causing 30-90s freezes.
 *
 * This context holds ONLY the values these sub-components actually need.
 * It changes only when streaming state changes — not on every conversation
 * mutation.
 */
import React, { createContext, useContext, useMemo } from 'react';

interface StreamingContextValue {
  isStreaming: boolean;
  isStreamingAny: boolean;
  currentConversationId: string;
  streamingConversations: Set<string>;
}

const StreamingContext = createContext<StreamingContextValue>({
  isStreaming: false,
  isStreamingAny: false,
  currentConversationId: '',
  streamingConversations: new Set(),
});

export const StreamingProvider: React.FC<{
  isStreaming: boolean;
  isStreamingAny: boolean;
  currentConversationId: string;
  streamingConversations: Set<string>;
  children: React.ReactNode;
}> = ({ isStreaming, isStreamingAny, currentConversationId, streamingConversations, children }) => {
  const value = useMemo(() => ({
    isStreaming,
    isStreamingAny,
    currentConversationId,
    streamingConversations,
  }), [isStreaming, isStreamingAny, currentConversationId, streamingConversations]);

  return (
    <StreamingContext.Provider value={value}>
      {children}
    </StreamingContext.Provider>
  );
};

/**
 * Drop-in replacement for useChatContext() in MarkdownRenderer sub-components
 * that only need streaming state.  Does NOT subscribe to conversations changes.
 */
export function useStreamingContext(): StreamingContextValue {
  return useContext(StreamingContext);
}

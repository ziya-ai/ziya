/**
 * StreamedContentContext — the two maps that change on every streaming flush.
 *
 * chatApi replaces streamedContentMap every 150 ms while a turn streams, and
 * reasoningContentMap on every thinking delta.  When these lived in the
 * ActiveChatContext / ChatContext values, every flush re-rendered every
 * consumer of those contexts (the sidebar, modals, every history message),
 * and App re-rendered because it read the map directly.  One 100 s stream
 * allocated 18.4 GB doing that, and the promoted garbage is what swelled
 * renderer memory into the gigabytes.
 *
 * Three ways to read, pick the cheapest that is correct:
 *   - useStreamedContent(): the live maps.  Re-renders on every flush; only
 *     for components that display streamed text (StreamedContent, thinking
 *     blocks, the raw view).
 *   - useStreamedContentPresence(): the set of conversation ids whose
 *     streamed content is non-empty.  Changes only when content appears or
 *     is cleared, not per flush.
 *   - useStreamedContentRef(): a stable ref holding the latest maps, for
 *     reads at call time (send, export).  Never re-renders the caller.
 *
 * StreamedContentLengthWatcher reports one conversation's streamed length
 * to a callback, so App's scroll manager follows the stream without App
 * itself re-rendering.
 *
 * Defaults are empty maps, so components rendered without the provider
 * (tests, print routes) read nothing rather than throwing.
 */
import React, { createContext, useContext, useEffect, useMemo, useRef } from 'react';
import type { ThinkingBlockData } from '../utils/thinkingBlocks';

export interface StreamedContentValue {
  streamedContentMap: Map<string, string>;
  /** Ephemeral thinking blocks keyed by turn id -- see thinkingBlocks.ts. */
  reasoningContentMap: Map<string, ThinkingBlockData[]>;
}

const EMPTY: StreamedContentValue = {
  streamedContentMap: new Map(),
  reasoningContentMap: new Map(),
};

const StreamedContentContext = createContext<StreamedContentValue>(EMPTY);
const StreamedContentRefContext = createContext<{ readonly current: StreamedContentValue }>({ current: EMPTY });
const StreamedPresenceContext = createContext<ReadonlySet<string>>(new Set());

/** Stable string for "which conversations have non-empty streamed content". */
export function presenceKey(map: Map<string, string>): string {
  const ids: string[] = [];
  map.forEach((v, k) => { if (v !== '') ids.push(k); });
  return ids.sort().join('\n');
}

export const StreamedContentProvider: React.FC<StreamedContentValue & { children: React.ReactNode }> = ({
  streamedContentMap, reasoningContentMap, children,
}) => {
  const value = useMemo(
    () => ({ streamedContentMap, reasoningContentMap }),
    [streamedContentMap, reasoningContentMap],
  );
  // Updated during render so a call-time reader in the same commit already
  // sees the new maps.
  const ref = useRef(value);
  ref.current = value;
  const key = presenceKey(streamedContentMap);
  const presence = useMemo<ReadonlySet<string>>(() => new Set(key ? key.split('\n') : []), [key]);
  return (
    <StreamedContentRefContext.Provider value={ref}>
      <StreamedPresenceContext.Provider value={presence}>
        <StreamedContentContext.Provider value={value}>
          {children}
        </StreamedContentContext.Provider>
      </StreamedPresenceContext.Provider>
    </StreamedContentRefContext.Provider>
  );
};

/** The live maps.  Re-renders the caller on every streaming flush. */
export function useStreamedContent(): StreamedContentValue {
  return useContext(StreamedContentContext);
}

/** Conversation ids with non-empty streamed content; changes only on presence. */
export function useStreamedContentPresence(): ReadonlySet<string> {
  return useContext(StreamedPresenceContext);
}

/** Stable ref to the latest maps; read `.current` at call time. */
export function useStreamedContentRef(): { readonly current: StreamedContentValue } {
  return useContext(StreamedContentRefContext);
}

/**
 * Calls onLength with the conversation's streamed length whenever it
 * changes (and when onLength changes).  Renders nothing; its own re-render
 * per flush is the only cost.
 */
export const StreamedContentLengthWatcher: React.FC<{
  conversationId: string;
  onLength: (length: number) => void;
}> = ({ conversationId, onLength }) => {
  const { streamedContentMap } = useStreamedContent();
  const length = (streamedContentMap.get(conversationId) || '').length;
  useEffect(() => { onLength(length); }, [length, onLength]);
  return null;
};

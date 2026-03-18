/**
 * useDelegateStreaming — Live WebSocket relay for delegate conversations.
 *
 * When the user is viewing a delegate (or orchestrator) conversation,
 * this hook connects to /ws/delegate-stream/{conversationId} and feeds
 * incoming chunks into streamedContentMap so the existing Conversation.tsx
 * streaming UI renders them identically to a normal chat stream.
 *
 * Disconnects automatically when the user navigates away or the stream ends.
 */

 import { useEffect, useRef, useMemo } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { Conversation } from '../utils/types';

interface UseDelegateStreamingArgs {
  conversationId: string;
  conversations: Conversation[];
  streamingConversations: Set<string>;
  addStreamingConversation: (id: string) => void;
  removeStreamingConversation: (id: string) => void;
  setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>;
  addMessageToConversation: (msg: any, convId: string, isNonCurrent?: boolean) => void;
}

export function useDelegateStreaming({
  conversationId,
  conversations,
  streamingConversations,
  addStreamingConversation,
  removeStreamingConversation,
  setStreamedContentMap,
  addMessageToConversation,
}: UseDelegateStreamingArgs): void {
  const wsRef = useRef<WebSocket | null>(null);
  const accumulatedRef = useRef<string>('');
  const connectedConvRef = useRef<string | null>(null);
  const siblingWsRef = useRef<Map<string, WebSocket>>(new Map());
  // Track which sibling IDs we've already attempted to connect (prevents reconnect storms)
  const siblingConnectedIdsRef = useRef<Set<string>>(new Set());

  // Derive stable keys from *only* the delegate-relevant fields so
  // effects don't re-fire on every unrelated conversation change
  // (message adds, server-sync updates, mute toggles, etc.).
  const delegateKey = useMemo(() => {
    const conv = conversations.find(c => c.id === conversationId);
    if (!conv?.delegateMeta) return 'none';
    const dm = conv.delegateMeta as any;
    return `${conversationId}:${dm.status || ''}:${dm.plan_id || ''}`;
  }, [conversationId, conversations]);

  const siblingKey = useMemo(() => {
    const conv = conversations.find(c => c.id === conversationId);
    const planId = (conv?.delegateMeta as any)?.plan_id;
    if (!planId) return 'no-plan';
    const siblings = conversations.filter(c => {
      const dm = c.delegateMeta as any;
      return dm?.plan_id === planId && dm?.role === 'delegate' && c.id !== conversationId;
    });
    return siblings.map(s => `${s.id}:${(s.delegateMeta as any)?.status}`).join('|');
  }, [conversationId, conversations]);

  useEffect(() => {
    // Only connect for delegate/orchestrator conversations
    // PERFORMANCE: Use conversationId as primary key, only scan conversations
    // array when needed (not on every conversations reference change).
    let conv: any = null;
    for (const c of conversations) {
      if (c.id === conversationId) {
        conv = c;
        break;
      }
    }
    
    if (!conv?.delegateMeta) {
      // Not a delegate — disconnect if previously connected
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
        connectedConvRef.current = null;
      }
      return;
    }

    // Clean up streaming UI when a delegate reaches a terminal state.
    // Handles the race where a delegate finishes before the user views it.
    //
    // Future: delegate re-activation (revision without restart) would
    // transition status back to 'running' server-side, causing this
    // hook to reconnect automatically on the next effect cycle.
    const status = conv.delegateMeta.status;
    const isTerminal = status === 'crystal' || status === 'failed' || status === 'interrupted';

    if (isTerminal || status === 'proposed') {
      if (isTerminal && streamingConversations.has(conversationId)) {
        console.log('📡 DELEGATE_STREAM: Status is terminal, cleaning up streaming state for', conversationId.substring(0, 8));
        removeStreamingConversation(conversationId);
      }
      if (wsRef.current && connectedConvRef.current === conversationId) {
        wsRef.current.close();
        wsRef.current = null;
        connectedConvRef.current = null;
      }
      return;
    }

    // Already connected to this conversation with an open WebSocket — skip
    if (connectedConvRef.current === conversationId && 
        wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return;
    }

    // Close previous connection
    if (wsRef.current) {
      wsRef.current.close();
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/delegate-stream/${conversationId}`;
    console.log('📡 DELEGATE_STREAM: Connecting to', wsUrl);

    let connectionTimedOut = false;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    connectedConvRef.current = conversationId;

    // Re-check status at connect time: the delegate may have finished
    // between the start of this effect and the WebSocket handshake.
    // Accumulated content is only reset if we're starting a fresh stream.
    if (!accumulatedRef.current) {
      accumulatedRef.current = '';
    }

    let receivedData = false;
    let staleTimer: ReturnType<typeof setTimeout> | null = null;

    // Hard timeout on the WebSocket connection attempt itself.
    const connectTimer = setTimeout(() => {
      if (ws.readyState === WebSocket.CONNECTING) {
        console.log('📡 DELEGATE_STREAM: Connection timeout, aborting', conversationId.substring(0, 8));
        connectionTimedOut = true;
        ws.close();
        wsRef.current = null;
        connectedConvRef.current = null;
      }
    }, 5000);

    ws.onopen = () => {
      clearTimeout(connectTimer);
      if (connectionTimedOut) return;
      console.log('📡 DELEGATE_STREAM: Connected for', conversationId.substring(0, 8));
      addStreamingConversation(conversationId);
      // Stale session detection: if no data arrives shortly after
      // connecting, the delegate is no longer active server-side.
      staleTimer = setTimeout(() => {
        if (!receivedData) {
          console.log('📡 DELEGATE_STREAM: No data received — stale session, cleaning up', conversationId.substring(0, 8));
          removeStreamingConversation(conversationId);
          try { ws.close(); } catch {}
          wsRef.current = null;
          connectedConvRef.current = null;
        }
      }, 3000);
    };

    ws.onmessage = (event) => {
      receivedData = true;
      if (staleTimer) { clearTimeout(staleTimer); staleTimer = null; }
      try {
        const chunk = JSON.parse(event.data);
        const ctype = chunk.type;

        if (ctype === 'text') {
          accumulatedRef.current += chunk.content || '';
          const snapshot = accumulatedRef.current;
          setStreamedContentMap(prev => {
            if (prev.get(conversationId) === snapshot) return prev;
            const next = new Map(prev);
            next.set(conversationId, snapshot);
            return next;
          });
        } else if (ctype === 'stream_end' || ctype === 'delegate_complete') {
          // Stream finished — persist content as a message and stop streaming UI
          // The backend already persisted this message via
          // _update_delegate_assistant_message.  Writing it again here
          // causes duplicates on the next sync/reload.  Just clear the
          // streaming UI — the server copy is authoritative.
          removeStreamingConversation(conversationId);
          accumulatedRef.current = '';
          ws.close();
        } else if (ctype === 'orchestrator_message') {
          // Backend already persisted this via _persist_delegate_message.
          // Adding it locally too creates duplicates visible until page
          // reload (the server copy and the frontend copy coexist).
          // The next delegate-polling cycle (≤3s) picks up the durable
          // server-written copy.
        }
        // tool_start, tool_display, processing — silently ignored for now
        // (could be surfaced later for richer delegate UX)
      } catch (e) {
        console.warn('📡 DELEGATE_STREAM: Parse error:', e);
      }
    };

    ws.onclose = () => {
      console.log('📡 DELEGATE_STREAM: Closed for', conversationId.substring(0, 8));
      if (connectedConvRef.current === conversationId) {
        removeStreamingConversation(conversationId);
      }
    };

    ws.onerror = (err) => {
      clearTimeout(connectTimer);
      console.warn('📡 DELEGATE_STREAM: Error:', err);
    };

    return () => {
      if (staleTimer) clearTimeout(staleTimer);
      clearTimeout(connectTimer);
      ws.close();
      connectedConvRef.current = null;
    };
    // delegateKey changes only when this conversation's delegate status changes
  }, [delegateKey]);

  // ------------------------------------------------------------------
  // Sibling delegate connections: when viewing the orchestrator (or any
  // conversation in a plan), open background WebSockets for every
  // running delegate so their chunks reach streamedContentMap.
  // ------------------------------------------------------------------
  useEffect(() => {
    const conv = conversations.find(c => c.id === conversationId);
    if (!conv?.delegateMeta) return;
    
    const planId = (conv.delegateMeta as any)?.plan_id;
    if (!planId) return;
    
    // Don't open sibling connections for terminal delegate conversations.
    // The user is reviewing completed/failed work — no live data to stream.
    const myStatus = (conv.delegateMeta as any)?.status;
    const isTerminal = myStatus === 'crystal' || myStatus === 'failed' || myStatus === 'interrupted';
    if (isTerminal && (conv.delegateMeta as any)?.role !== 'orchestrator') {
      // Close any existing sibling connections
      const siblingMap = siblingWsRef.current;
      for (const [id, ws] of siblingMap.entries()) {
        ws.close();
        siblingMap.delete(id);
      }
      return;
    }

    const siblings = conversations.filter(c => {
      const dm = c.delegateMeta as any;
      return dm?.plan_id === planId
        && dm?.role === 'delegate'
        && c.id !== conversationId
        && (dm?.status === 'running' || dm?.status === 'compacting');
    });

    const siblingMap = siblingWsRef.current;
    const desiredIds = new Set(siblings.map(s => s.id));

    // Close connections for delegates that are no longer running
    for (const [id, ws] of siblingMap.entries()) {
      if (!desiredIds.has(id)) {
        ws.close();
        siblingMap.delete(id);
        removeStreamingConversation(id);
      }
    }

    // Open connections for newly-running delegates (skip if already attempted)
    for (const sib of siblings) {
      if (siblingMap.has(sib.id) || siblingConnectedIdsRef.current.has(sib.id)) continue;
      siblingConnectedIdsRef.current.add(sib.id);

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/delegate-stream/${sib.id}`;
      const ws = new WebSocket(wsUrl);
      siblingMap.set(sib.id, ws);

      const sibId = sib.id;
      let sibAccum = '';
      let receivedData = false;

      ws.onopen = () => {
        addStreamingConversation(sibId);
        setTimeout(() => {
          if (!receivedData) {
            removeStreamingConversation(sibId);
            ws.close();
            siblingMap.delete(sibId);
          }
        }, 3000);
      };

      ws.onmessage = (event) => {
        receivedData = true;
        try {
          const chunk = JSON.parse(event.data);
          if (chunk.type === 'text') {
            sibAccum += chunk.content || '';
            const snapshot = sibAccum;
            setStreamedContentMap(prev => {
              if (prev.get(sibId) === snapshot) return prev;
              const next = new Map(prev);
              next.set(sibId, snapshot);
              return next;
            });
          } else if (chunk.type === 'stream_end' || chunk.type === 'delegate_complete') {
            removeStreamingConversation(sibId);
            ws.close();
            siblingMap.delete(sibId);
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onclose = () => {
        removeStreamingConversation(sibId);
        siblingMap.delete(sibId);
      };
    }

    return () => {
      for (const [id, ws] of siblingMap.entries()) {
        ws.close();
        removeStreamingConversation(id);
      }
      siblingMap.clear();
      siblingConnectedIdsRef.current.clear();
    };
    // siblingKey changes only when sibling delegate statuses change
  }, [siblingKey]);
}

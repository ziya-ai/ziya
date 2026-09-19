/**
 * Decide whether a conversation should reattach to a server-side chat turn.
 *
 * A chat turn is a server-owned task (app/agents/chat_turn_relay.py); the
 * HTTP response is only a subscriber.  After a tab reload, or when a second
 * browser opens a conversation another browser is driving, this tab has no
 * stream for a turn the server may still be running — or may have finished
 * while nobody was watching, in which case the relay's retained buffer is
 * the ONLY copy of the answer (the frontend persists assistant messages
 * from a live stream, not from the server).
 *
 * Pure: no fetch, no React.  The watcher supplies the status it fetched
 * and the local facts; this decides.  Kept separate so the four "do not
 * reattach" cases below are testable — each one, wrong, is a duplicated
 * or resurrected answer, which is worse than a missing one.
 */
import type { Message } from './types';

/** Shape of GET /api/chat/turn/{conversation_id}. */
export interface ChatTurnStatus {
  conversation_id: string;
  turn_id: string;
  active: boolean;
  held: boolean;
  cancelled: boolean;
  error: string | null;
  /** Epoch SECONDS (server-side time.time()). */
  started_at: number;
  finished_at: number | null;
  subscribers: number;
  buffered_frames: number;
  dropped_frames: number;
}

export type ReattachDecision =
  | { reattach: true; question: string; turnId: string }
  | { reattach: false; reason: string };

// A user message sent this much BEFORE the turn started is still "the
// question this turn answers" — covers clock skew and the client-side
// timestamp being stamped before the POST leaves.
const START_SLACK_MS = 60_000;

function lastMessage(messages: Message[]): Message | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (!messages[i].muted) return messages[i];
  }
  return undefined;
}

export function decideReattach(
  status: ChatTurnStatus | null,
  messages: Message[],
  streamingLocally: boolean,
  alreadyReattachedTurnIds: ReadonlySet<string>,
): ReattachDecision {
  if (!status) return { reattach: false, reason: 'no turn on server' };
  if (streamingLocally) {
    // Either this tab owns the turn or another tab in this browser is
    // mirroring it over BroadcastChannel.  Reattaching would render it twice.
    return { reattach: false, reason: 'already streaming in this browser' };
  }
  if (alreadyReattachedTurnIds.has(status.turn_id)) {
    return { reattach: false, reason: 'already reattached to this turn' };
  }

  const last = lastMessage(messages);
  if (!last) return { reattach: false, reason: 'conversation has no messages' };

  if (!status.active) {
    // Finished while unwatched.  Only worth replaying if the answer never
    // landed: the last visible message is still the human's.  If an
    // assistant message is already there, some tab persisted it and a
    // replay would append a duplicate.
    if (last.role !== 'human') {
      return { reattach: false, reason: 'answer already persisted' };
    }
  }

  // A turn that started before the latest human message was sent answered
  // an EARLIER question (e.g. the user stopped it, then asked something
  // else while offline).  Replaying it under the new question would be a
  // resurrected answer.  Only checkable when the message carries a time.
  if (typeof last._timestamp === 'number' && last.role === 'human') {
    const startedMs = status.started_at * 1000;
    if (startedMs + START_SLACK_MS < last._timestamp) {
      return { reattach: false, reason: 'turn predates the latest question' };
    }
  }

  const question = last.role === 'human' ? last.content : '';
  return { reattach: true, question, turnId: status.turn_id };
}

/** Fetch the relay's view of a conversation; null when the relay has no turn
 *  for it (200 with ``turn_id: null`` -- the routine answer on a switch, no
 *  longer a 404 the browser logs as an error) or on network error. */
export async function fetchChatTurnStatus(conversationId: string): Promise<ChatTurnStatus | null> {
  try {
    const res = await fetch(`/api/chat/turn/${encodeURIComponent(conversationId)}`);
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<ChatTurnStatus> | null;
    if (!body || !body.turn_id) return null;
    return body as ChatTurnStatus;
  } catch {
    return null;
  }
}

/**
 * Ask the other tabs in this browser whether one of them owns the turn.
 * Resolves after ``waitMs``; the caller re-reads its streaming set, which
 * the owner's 'streaming-state' reply will have populated via ChatContext.
 */
export function probeOtherTabs(
  post: (type: 'streaming-probe', payload: { conversationId: string }) => void,
  conversationId: string,
  waitMs = 250,
): Promise<void> {
  post('streaming-probe', { conversationId });
  return new Promise(resolve => setTimeout(resolve, waitMs));
}

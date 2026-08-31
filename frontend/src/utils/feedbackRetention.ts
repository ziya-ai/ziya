/**
 * Retention store for mid-stream feedback that has been handed to the backend
 * but not yet confirmed injected into the model's conversation.
 *
 * Feedback typed while a turn is streaming goes out over the feedback
 * WebSocket, which queues it per conversation and injects it into that
 * conversation's model context. That path is already independent of what the
 * user is looking at. This store is the browser-side mirror of it: the text is
 * kept until a `feedback_delivered` ack names it, so a turn that ends without
 * consuming it can be recovered as an ordinary new turn.
 *
 * Deliberately a module-level Map rather than component state or context:
 *
 *  - Keyed by conversation, so feedback typed into one conversation is never
 *    replaced or resubmitted by activity in another. The single slot this
 *    replaces discarded conversation A's retained text as soon as feedback was
 *    sent in conversation B.
 *  - Independent of mount and of the viewed conversation. Recovery must fire
 *    when the turn it belongs to ends, not when the user next happens to
 *    navigate back to that conversation.
 *  - `take` removes before it returns, so two observers of the same turn end
 *    (StrictMode's double-invoked effects, or a remount mid-flight) cannot
 *    both resubmit the same text.
 *
 * Scope is the browser window, which is the correct boundary: the window that
 * sent the feedback holds the only copy, so it owns the recovery.
 */

/**
 * The executor caps its ack at 80 characters
 * (`{'type': 'feedback_delivered', 'message': fb_msg[:80]}`), so an ack at or
 * above this length cannot be matched against the text that produced it.
 */
export const EXECUTOR_ACK_CAP = 80;

/** Dispatched on `document` after retained feedback is resubmitted. */
export const FEEDBACK_RESUBMITTED_EVENT = 'feedbackResubmitted';

export interface FeedbackResubmittedDetail {
  conversationId: string;
  texts: string[];
}

const retained = new Map<string, string[]>();

/** Record feedback accepted by the WebSocket, pending a delivery ack. */
export function recordSentFeedback(conversationId: string, text: string): void {
  if (!conversationId || !text) return;
  const existing = retained.get(conversationId);
  if (existing) existing.push(text);
  else retained.set(conversationId, [text]);
}

/**
 * Retire whatever a delivery ack names, for the conversation the ack belongs
 * to. Callers must pass the ACK's conversation, not the viewed one: gating
 * this on what was on screen meant an ack arriving while the user was
 * elsewhere retired nothing, and the turn-end recovery then resubmitted
 * feedback the model had already been given.
 *
 * The backend joins a drained batch into one injection, so an untruncated ack
 * retires exactly the texts it names. A truncated one cannot be matched, so
 * everything is cleared — over-clearing loses a recovery, under-clearing
 * duplicates a send, and a duplicate reaches the model twice.
 */
export function retireDeliveredFeedback(conversationId: string, ackMessage: string): void {
  const texts = retained.get(conversationId);
  if (!texts) return;
  const ack = ackMessage || '';
  const remaining = ack.length >= EXECUTOR_ACK_CAP
    ? []
    : texts.filter(t => !ack.includes(t));
  if (remaining.length === 0) retained.delete(conversationId);
  else retained.set(conversationId, remaining);
}

/** Read without consuming — for the composer's status chip. */
export function peekRetainedFeedback(conversationId: string): readonly string[] {
  return retained.get(conversationId) ?? [];
}

export function hasRetainedFeedback(conversationId: string): boolean {
  return (retained.get(conversationId)?.length ?? 0) > 0;
}

/**
 * Claim a conversation's retained feedback for recovery. Atomic: the entry is
 * removed before the texts are returned, so a repeated observation of the same
 * turn end gets nothing.
 */
export function takeRetainedFeedback(conversationId: string): string[] {
  const texts = retained.get(conversationId);
  retained.delete(conversationId);
  return texts ? [...texts] : [];
}

/** Drop retained feedback that cannot be recovered (conversation is gone). */
export function discardRetainedFeedback(conversationId: string): void {
  retained.delete(conversationId);
}

export function __resetFeedbackRetentionForTests(): void {
  retained.clear();
}

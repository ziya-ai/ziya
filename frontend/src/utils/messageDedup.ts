/**
 * The DUPLICATE_GUARD decision in ChatContext.addMessageToConversation.
 *
 * The guard exists to break feedback loops that replay the whole message
 * history back through addMessageToConversation.  Its original test —
 * "any message with the same role and content already exists" — was too
 * wide: a user who legitimately types the same short prompt twice in one
 * conversation ("applied", "yes", "continue") had the second one dropped
 * from state while it was still sent to the model, so the record read
 * assistant → assistant with the human turn missing (2026-09-28, conv
 * 8063d5b2).
 *
 * A replayed message carries the identity of the original — its `id`, or
 * at least its `_timestamp` — while a fresh submission carries neither.
 * So: match by id when the incoming message has one; otherwise treat an
 * identical role+content as a duplicate only when it is plausibly the same
 * event (same timestamp, or the existing copy landed within
 * DOUBLE_SUBMIT_WINDOW_MS of this one).  A retype minutes later passes.
 */

export interface DedupMessage {
  id?: string;
  role: string;
  content: string;
  _timestamp?: number;
}

/** Two submissions of identical text this close together are one click. */
export const DOUBLE_SUBMIT_WINDOW_MS = 5_000;

export function isDuplicateMessage(
  incoming: DedupMessage,
  existing: readonly DedupMessage[],
  incomingId?: string,
  now: number = Date.now(),
): boolean {
  // `incomingId` is the id the caller ASSIGNED on the way in; only an id the
  // message arrived with proves it was seen before.
  const arrivedWithId = incoming.id && incoming.id !== incomingId ? incoming.id : undefined;
  if (arrivedWithId) {
    return existing.some(m => m.id === arrivedWithId);
  }
  const ts = incoming._timestamp ?? now;
  return existing.some(m => {
    if (m.role !== incoming.role || m.content !== incoming.content) return false;
    // Same event replayed: identical stamp.
    if (m._timestamp != null && m._timestamp === incoming._timestamp) return true;
    // Double-submit: the existing copy is seconds old.
    if (m._timestamp != null && Math.abs(ts - m._timestamp) <= DOUBLE_SUBMIT_WINDOW_MS) return true;
    // A legacy message with no timestamp cannot be told apart from a
    // replay; keep the old conservative behaviour for those only when the
    // incoming message is not human-authored (a human retype is the case
    // this helper exists to allow).
    return m._timestamp == null && incoming.role !== 'human';
  });
}

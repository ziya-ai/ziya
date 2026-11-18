/**
 * Whether a failed background drafting turn failed because the conversation
 * is larger than the model's context window.  That case falls back to a
 * drafter conversation (app/utils/handoff_drafter.py); other failures are
 * reported as they are.
 */
const OVERFLOW_RE =
  /prompt is too long|input is too long|too large for this model|tokens\s*>\s*\d|maximum context length|context_size_error|context (window|limit) exceeded|failed: 413\b/i;

export function isContextOverflowError(err: unknown): boolean {
  if (err == null) return false;
  const text = typeof err === 'string'
    ? err
    : (err as any)?.message ?? String(err);
  return OVERFLOW_RE.test(String(text));
}

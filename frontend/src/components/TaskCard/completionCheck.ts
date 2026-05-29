/**
 * Frontend mirror of the backend ``app/utils/completion_check.py``
 * helper: strips the ``<self_assessment .../>`` meta tag from
 * model-emitted text so the user-facing UI never displays it.
 *
 * The backend strips the tag from ``Artifact.summary`` before
 * persistence, but live-streamed text and per-iteration buckets
 * arrive at the inspector with the tag intact.  Without a frontend
 * strip, the user sees the literal XML in the Live Output tab —
 * exactly what the model was prompted to emit, but not what we
 * want to show.
 *
 * Pure function, no I/O — kept symmetrical with the Python
 * regex/replacement so behavior is consistent across both surfaces.
 */

// Self-closing form, with or without attributes.  Matches the
// Python equivalent: ``<\s*self_assessment\b[^>]*/?>``.
// Case-insensitive because models occasionally emit ``<Self_Assessment ...>``.
const SELF_CLOSING_TAG = /<\s*self_assessment\b[^>]*\/?>/gi;

// Paired form: ``<self_assessment ...>...</self_assessment>``.
// Body content is also stripped because it's metadata, not UI text.
// ``[\s\S]`` rather than ``.`` so the body can span newlines.
const PAIRED_TAG = /<\s*self_assessment\b[^>]*>[\s\S]*?<\s*\/\s*self_assessment\s*>/gi;

/**
 * Remove any ``<self_assessment .../>`` tag (self-closing or paired)
 * from ``text`` and return the trimmed result.
 *
 * Idempotent: text without the tag is returned unchanged (modulo
 * trailing whitespace from a trailing tag).  Non-string inputs
 * pass through as-is so callers can pass ``streamText`` from
 * partially-parsed payloads without type-checking first.
 */
export function stripAssessmentTag(text: unknown): string {
  if (typeof text !== 'string' || text.length === 0) {
    return typeof text === 'string' ? text : '';
  }
  // Paired first (more specific) so a paired tag isn't half-matched
  // as self-closing then leaving a dangling closing tag.
  const out = text
    .replace(PAIRED_TAG, '')
    .replace(SELF_CLOSING_TAG, '');
  // Trim whitespace that the tag's removal may have left behind at
  // the end (a common case: model emits the tag at end of stream).
  return out.trimEnd();
}

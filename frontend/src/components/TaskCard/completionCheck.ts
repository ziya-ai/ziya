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

// ``<progress note="..."/>`` — mid-stream model-authored progress
// markers (backend mirror: app/utils/completion_check.py).  The
// executor consumes them into the run's live-progress surface; they
// are metadata, never display text.
const PROGRESS_TAG = /<\s*progress\b[^>]*\/?>/gi;
// A tag still streaming in (no closing ``>`` yet) at the very end of
// the text.  Hidden so the user doesn't see raw tag characters
// flicker while the tag completes.
const PARTIAL_TRAILING_PROGRESS = /<\s*progress\b[^>]*$/i;

/**
 * Remove complete <progress .../> tags anywhere in ``text`` plus an
 * incomplete one at the tail (mid-stream).  Idempotent.
 *
 * Tags are sometimes emitted with no surrounding whitespace (e.g.
 * ``...test gate.<progress note="..."/>Test gate passes...``).  A
 * bare removal would glue the sentence before the tag directly to
 * the sentence after it, with no space between them.  Only insert a
 * replacement space when BOTH the character immediately before and
 * immediately after the tag are non-whitespace — tags that already
 * have surrounding whitespace (the common, well-formatted case) are
 * still stripped to nothing rather than doubled up.
 */
export function stripProgressTags(text: unknown): string {
  if (typeof text !== 'string' || text.length === 0) {
    return typeof text === 'string' ? text : '';
  }
  const fillGap = (match: string, offset: number, str: string): string => {
    const before = offset > 0 ? str[offset - 1] : '';
    const after = offset + match.length < str.length ? str[offset + match.length] : '';
    return before && after && !/\s/.test(before) && !/\s/.test(after) ? ' ' : '';
  };
  return text
    .replace(PROGRESS_TAG, fillGap)
    .replace(PARTIAL_TRAILING_PROGRESS, (match: string, offset: number, str: string) => {
      const before = offset > 0 ? str[offset - 1] : '';
      return before && !/\s/.test(before) ? ' ' : '';
    });
}

/**
 * Strip ALL task meta tags (<self_assessment>, <progress>) from
 * model text bound for display.  The single entry point render
 * sites should use so new meta tags only need wiring here.
 */
export function stripTaskMetaTags(text: unknown): string {
  return stripProgressTags(stripAssessmentTag(text)).trimEnd();
}
const RECORDING_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
];

/**
 * Select the first recording format supported by the current browser.
 * An empty result lets MediaRecorder choose its browser default.
 */
export function chooseRecordingMimeType(
  mediaRecorder: typeof MediaRecorder | undefined = globalThis.MediaRecorder,
): string {
  if (!mediaRecorder || typeof mediaRecorder.isTypeSupported !== 'function') {
    return '';
  }
  return RECORDING_MIME_TYPES.find(type => mediaRecorder.isTypeSupported(type)) || '';
}

/**
 * Capture a selection range only when it belongs to the composer.
 */
export function captureEditorRange(editor: HTMLElement): Range | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;

  const range = selection.getRangeAt(0);
  return editor.contains(range.commonAncestorContainer) ? range.cloneRange() : null;
}

/**
 * Insert transcript text at a saved composer position. If the saved position
 * is no longer valid, append at the end rather than writing outside the editor.
 */
export function insertTranscript(
  editor: HTMLElement,
  transcript: string,
  preferredRange?: Range | null,
): boolean {
  // Reject whitespace-only transcripts, but insert the transcript verbatim:
  // the caller's trailing space is what separates the dictated text from
  // whatever already follows the cursor.
  if (!transcript.trim()) return false;
  const text = transcript;

  let range: Range;
  if (
    preferredRange
    && editor.contains(preferredRange.commonAncestorContainer)
  ) {
    range = preferredRange.cloneRange();
  } else {
    range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
  }

  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);

  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  editor.focus();
  return true;
}

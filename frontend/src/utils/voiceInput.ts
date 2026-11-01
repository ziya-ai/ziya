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

// Audio files a user can drop or pick for local transcription, keyed by
// extension.  Browsers disagree on the MIME type of these: Chrome and Safari
// report .m4a as audio/x-m4a, Firefox as audio/mp4 or nothing at all, and
// .wav arrives as audio/wav, audio/x-wav, audio/wave or audio/vnd.wave.  The
// extension decides, and the upload carries one canonical type that
// /api/transcribe accepts.  A Map, not an object literal, so a name like
// "x.constructor" cannot match an inherited key.
const TRANSCRIBABLE_AUDIO_TYPES = new Map<string, string>([
  ['m4a', 'audio/mp4'],
  ['wav', 'audio/wav'],
]);

/**
 * Fallback upload ceiling, matching the ZIYA_TRANSCRIBE_MAX_MB default.  The
 * server reports the configured value as max_bytes in /api/transcribe/status;
 * this is used only until that answer arrives.
 */
export const MAX_TRANSCRIPTION_BYTES = 1024 * 1024 * 1024;

/** "340 MB", "1 GB": for size warnings. */
export function formatByteSize(bytes: number): string {
  const megabytes = bytes / (1024 * 1024);
  return megabytes >= 1024
    ? `${Number((megabytes / 1024).toFixed(1))} GB`
    : `${Number(megabytes.toFixed(1))} MB`;
}

/**
 * Return the canonical upload type for a transcribable audio file, or null
 * when the file is not one.
 */
export function transcribableAudioType(file: File): string | null {
  const dot = file.name.lastIndexOf('.');
  if (dot < 0) return null;
  return TRANSCRIBABLE_AUDIO_TYPES.get(file.name.slice(dot + 1).toLowerCase()) ?? null;
}

/**
 * Split incoming files into audio for local transcription and everything
 * else, preserving arrival order within each group.
 */
export function partitionAudioFiles(
  files: FileList | File[],
): { audio: File[]; other: File[] } {
  const audio: File[] = [];
  const other: File[] = [];
  for (const file of Array.from(files)) {
    (transcribableAudioType(file) ? audio : other).push(file);
  }
  return { audio, other };
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
 * One-line header naming a file transcript's source, e.g.
 * "[Transcribed from M4A audio file: standup.m4a]".  Several dropped files
 * would otherwise run together with nothing saying where each came from.
 */
export function transcriptHeader(filename: string): string {
  const dot = filename.lastIndexOf('.');
  const type = dot > 0 ? filename.slice(dot + 1).toUpperCase() : '';
  return `[Transcribed from ${type ? `${type} audio file` : 'audio file'}: ${filename}]`;
}

/** A file's transcript as inserted into the composer: header, then the text. */
export function formatFileTranscript(filename: string, transcript: string): string {
  return `${transcriptHeader(filename)}\n${transcript.trim()}`;
}

/**
 * The composer's text before the range, with line breaks as "\n", the way
 * serializeEditorContent reads it.  Only the end matters: whether a block
 * inserted at the range already starts on a fresh line.
 */
function textBefore(editor: HTMLElement, range: Range): string {
  const before = document.createRange();
  before.selectNodeContents(editor);
  before.setEnd(range.startContainer, range.startOffset);
  let out = '';
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent || '';
    } else if (node.nodeName === 'BR') {
      out += '\n';
    } else if (node.nodeName === 'IMG') {
      out += '\uFFFC';
    } else {
      // A browser-made line <div> starts a new line.  No trailing break: a
      // range ending inside a div clones it partially, and the cursor at its
      // end is still on that line.
      if ((node.nodeName === 'DIV' || node.nodeName === 'P') && out && !out.endsWith('\n')) {
        out += '\n';
      }
      node.childNodes.forEach(walk);
    }
  };
  before.cloneContents().childNodes.forEach(walk);
  return out;
}

/**
 * Insert transcript text at a saved composer position. If the saved position
 * is no longer valid, append at the end rather than writing outside the editor.
 *
 * "\n" in the transcript becomes a <br>: a newline inside a text node is
 * collapsed by the contentEditable composer.  With the block option, the text
 * is put on its own lines -- a break before it unless it already starts a
 * line, and one after it -- so consecutive file transcripts stay separate.
 */
export function insertTranscript(
  editor: HTMLElement,
  transcript: string,
  preferredRange?: Range | null,
  options: { block?: boolean } = {},
): boolean {
  // Reject whitespace-only transcripts, but insert the transcript verbatim:
  // the caller's trailing space is what separates the dictated text from
  // whatever already follows the cursor.
  if (!transcript.trim()) return false;

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
  const fragment = document.createDocumentFragment();
  if (options.block) {
    const preceding = textBefore(editor, range);
    if (preceding && !preceding.endsWith('\n')) {
      fragment.appendChild(document.createElement('br'));
    }
  }
  transcript.split('\n').forEach((line, index) => {
    if (index > 0) fragment.appendChild(document.createElement('br'));
    if (line) fragment.appendChild(document.createTextNode(line));
  });
  if (options.block) fragment.appendChild(document.createElement('br'));
  const last = fragment.lastChild;
  range.insertNode(fragment);
  if (last) range.setStartAfter(last);
  range.collapse(true);

  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  editor.focus();
  return true;
}

/**
 * Composer input history: Up/Down recall of messages previously sent in
 * the current conversation, matching the CLI's prompt_toolkit history.
 *
 * The history is derived from the conversation's own human messages, so
 * it is per-conversation by construction and needs no separate storage.
 */
import type { Message } from './types';

/** Human message texts, oldest first, consecutive duplicates collapsed. */
export function buildComposerHistory(messages: Message[]): string[] {
  const out: string[] = [];
  for (const m of messages) {
    if (m.role !== 'human' || typeof m.content !== 'string') continue;
    const text = m.content
      .replace(/\r\n?/g, '\n')
      .replace(/<image\d+>/g, '')
      .replace(/\s+$/, '');
    if (!text.trim()) continue;
    if (out.length && out[out.length - 1] === text) continue;
    out.push(text);
  }
  return out;
}

export interface HistoryStep {
  /** New browse position; null means "not browsing" (editor empty). */
  index: number | null;
  text: string;
}

/**
 * One Up/Down step. `index` null = not currently browsing.
 * Returns null when the key should fall through to default behaviour
 * (no history, already at the oldest entry, or Down while not browsing).
 */
export function stepComposerHistory(
  history: string[],
  index: number | null,
  direction: 'up' | 'down',
): HistoryStep | null {
  if (history.length === 0) return null;
  if (direction === 'up') {
    if (index === null) return { index: history.length - 1, text: history[history.length - 1] };
    if (index <= 0) return null;
    return { index: index - 1, text: history[index - 1] };
  }
  if (index === null) return null;
  if (index >= history.length - 1) return { index: null, text: '' };
  return { index: index + 1, text: history[index + 1] };
}

/**
 * True when the caret is on the editor's first ('first') or last ('last')
 * logical line, i.e. no line break lies between the caret and that edge.
 * Soft-wrapped lines count as one line.
 */
export function caretOnBoundaryLine(editor: HTMLElement, which: 'first' | 'last'): boolean {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return false;
  const range = sel.getRangeAt(0);
  if (!editor.contains(range.startContainer)) return false;
  const probe = document.createRange();
  probe.selectNodeContents(editor);
  if (which === 'first') {
    probe.setEnd(range.startContainer, range.startOffset);
  } else {
    probe.setStart(range.endContainer, range.endOffset);
  }
  const frag = probe.cloneContents();
  if ((frag.textContent || '').includes('\n')) return false;
  return !frag.querySelector('br,div,p');
}

/**
 * True when the caret is collapsed at the end of the editor's content.
 * Trailing line breaks may follow it (serialization trims them); text or
 * an inline image may not.
 */
export function caretAtEnd(editor: HTMLElement): boolean {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return false;
  const range = sel.getRangeAt(0);
  if (!editor.contains(range.endContainer)) return false;
  const probe = document.createRange();
  probe.selectNodeContents(editor);
  probe.setStart(range.endContainer, range.endOffset);
  const frag = probe.cloneContents();
  if ((frag.textContent || '').replace(/\n/g, '') !== '') return false;
  return !frag.querySelector('img');
}

/** Replace editor content with plain text (newlines as <br>), caret at end. */
export function setEditorPlainText(editor: HTMLElement, text: string): void {
  editor.textContent = '';
  text.split('\n').forEach((line, i) => {
    if (i > 0) editor.appendChild(document.createElement('br'));
    if (line) editor.appendChild(document.createTextNode(line));
  });
  editor.focus();
  const sel = window.getSelection();
  if (sel) {
    const r = document.createRange();
    r.selectNodeContents(editor);
    r.collapse(false);
    sel.removeAllRanges();
    sel.addRange(r);
  }
}

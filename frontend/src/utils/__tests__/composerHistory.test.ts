/**
 * Composer Up/Down input history (CLI-style recall of messages previously
 * sent in the current conversation).
 *
 * Covers the pure stepping logic, the two DOM helpers, and the seam: that
 * SendChatContainer's keydown handler actually reaches the helpers and
 * derives history from the live conversation's messages.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  buildComposerHistory,
  stepComposerHistory,
  caretOnBoundaryLine,
  setEditorPlainText,
} from '../composerHistory';
import type { Message } from '../types';

const msg = (role: any, content: string, extra: Partial<Message> = {}): Message =>
  ({ role, content, ...extra } as Message);

describe('buildComposerHistory', () => {
  it('keeps human messages oldest-first and drops assistant/system', () => {
    const h = buildComposerHistory([
      msg('human', 'first'), msg('assistant', 'reply'),
      msg('system', 'note'), msg('human', 'second'),
    ]);
    expect(h).toEqual(['first', 'second']);
  });

  it('collapses consecutive duplicates but keeps non-adjacent repeats', () => {
    const h = buildComposerHistory([
      msg('human', 'a'), msg('human', 'a'), msg('human', 'b'), msg('human', 'a'),
    ]);
    expect(h).toEqual(['a', 'b', 'a']);
  });

  it('strips image markers, trailing whitespace, and empty entries', () => {
    const h = buildComposerHistory([
      msg('human', 'look <image1> here\r\nnext line  \n'),
      msg('human', '<image1>'),
      msg('human', '   '),
    ]);
    expect(h).toEqual(['look  here\nnext line']);
  });
});

describe('stepComposerHistory', () => {
  const hist = ['one', 'two', 'three'];

  it('Up from not-browsing recalls the newest entry', () => {
    expect(stepComposerHistory(hist, null, 'up')).toEqual({ index: 2, text: 'three' });
  });

  it('Up walks backward and falls through at the oldest', () => {
    expect(stepComposerHistory(hist, 2, 'up')).toEqual({ index: 1, text: 'two' });
    expect(stepComposerHistory(hist, 0, 'up')).toBeNull();
  });

  it('Down walks forward and clears past the newest', () => {
    expect(stepComposerHistory(hist, 0, 'down')).toEqual({ index: 1, text: 'two' });
    expect(stepComposerHistory(hist, 2, 'down')).toEqual({ index: null, text: '' });
  });

  it('Down while not browsing, and any key with no history, fall through', () => {
    expect(stepComposerHistory(hist, null, 'down')).toBeNull();
    expect(stepComposerHistory([], null, 'up')).toBeNull();
  });
});

describe('DOM helpers', () => {
  let editor: HTMLDivElement;
  beforeEach(() => {
    editor = document.createElement('div');
    editor.contentEditable = 'true';
    document.body.appendChild(editor);
  });
  afterEach(() => editor.remove());

  const placeCaret = (node: Node, offset: number) => {
    const r = document.createRange();
    r.setStart(node, offset);
    r.collapse(true);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(r);
  };

  it('setEditorPlainText renders newlines as <br> and puts the caret at the end', () => {
    setEditorPlainText(editor, 'a\n\nb');
    expect(editor.innerHTML).toBe('a<br><br>b');
    const sel = window.getSelection()!;
    expect(sel.isCollapsed).toBe(true);
    expect(caretOnBoundaryLine(editor, 'last')).toBe(true);
    expect(caretOnBoundaryLine(editor, 'first')).toBe(false);
  });

  it('single-line content is both first and last line', () => {
    setEditorPlainText(editor, 'only line');
    expect(caretOnBoundaryLine(editor, 'first')).toBe(true);
    expect(caretOnBoundaryLine(editor, 'last')).toBe(true);
  });

  it('caret on the first line of multi-line content is first, not last', () => {
    setEditorPlainText(editor, 'top\nbottom');
    placeCaret(editor.firstChild!, 1);
    expect(caretOnBoundaryLine(editor, 'first')).toBe(true);
    expect(caretOnBoundaryLine(editor, 'last')).toBe(false);
  });

  it('newline characters inside a text node also count as line breaks', () => {
    editor.textContent = 'x\ny';
    placeCaret(editor.firstChild!, 3);
    expect(caretOnBoundaryLine(editor, 'first')).toBe(false);
    expect(caretOnBoundaryLine(editor, 'last')).toBe(true);
  });

  it('a caret outside the editor is not on a boundary line', () => {
    const other = document.createElement('span');
    other.textContent = 'elsewhere';
    document.body.appendChild(other);
    placeCaret(other.firstChild!, 0);
    expect(caretOnBoundaryLine(editor, 'first')).toBe(false);
    other.remove();
  });
});

describe('SendChatContainer wiring', () => {
  const SRC = fs.readFileSync(
    path.join(__dirname, '../../components/SendChatContainer.tsx'), 'utf8');
  const start = SRC.indexOf('const handleKeyDown = useCallback(');
  const end = SRC.indexOf('}, [', start);
  const body = SRC.slice(start, end);
  const deps = SRC.slice(end, SRC.indexOf(']);', end));

  it('keydown handler steps history built from the live conversation messages', () => {
    expect(start).toBeGreaterThan(-1);
    expect(body).toMatch(/ArrowUp/);
    expect(body).toMatch(/ArrowDown/);
    expect(body).toMatch(/stepComposerHistory\(\s*buildComposerHistory\(currentMessages\)/);
    expect(body).toMatch(/setEditorPlainText\(editor/);
    expect(body).toMatch(/caretOnBoundaryLine\(editor/);
  });

  it('handler deps include currentMessages so history is not stale', () => {
    expect(deps).toMatch(/currentMessages/);
    expect(deps).toMatch(/serializeEditorContent/);
  });

  it('browse state resets on conversation switch', () => {
    expect(SRC).toMatch(
      /useEffect\(\(\) => \{\s*historyNavRef\.current = \{ index: null, recalled: '' \};\s*\}, \[currentConversationId\]\);/);
  });
});

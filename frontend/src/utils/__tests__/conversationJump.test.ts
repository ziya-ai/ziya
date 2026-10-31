/**
 * Composer Left/Right arrows jump the conversation: Left in an empty
 * composer to the first message, Right (empty, or caret at the end of the
 * input) to the latest.
 *
 * Covers the pure edge mapping, the event channel, the caret-at-end probe,
 * and the seams on both sides: SendChatContainer dispatches, App listens
 * and drives the scroll manager.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  CONVERSATION_JUMP_EVENT,
  conversationJumpEdge,
  dispatchConversationJump,
} from '../conversationJump';
import { caretAtEnd } from '../composerHistory';

describe('conversationJumpEdge', () => {
  it('top-down: beginning is the DOM start, end is the DOM end', () => {
    expect(conversationJumpEdge('beginning', true)).toBe('start');
    expect(conversationJumpEdge('end', true)).toBe('end');
  });

  it('bottom-up (reversed display): beginning is the DOM end', () => {
    expect(conversationJumpEdge('beginning', false)).toBe('end');
    expect(conversationJumpEdge('end', false)).toBe('start');
  });
});

describe('dispatchConversationJump', () => {
  it('fires the window event with the target', () => {
    const seen: string[] = [];
    const h = (e: Event) => seen.push((e as CustomEvent).detail.target);
    window.addEventListener(CONVERSATION_JUMP_EVENT, h);
    dispatchConversationJump('beginning');
    dispatchConversationJump('end');
    window.removeEventListener(CONVERSATION_JUMP_EVENT, h);
    expect(seen).toEqual(['beginning', 'end']);
  });
});

describe('caretAtEnd', () => {
  let editor: HTMLDivElement;
  beforeEach(() => {
    editor = document.createElement('div');
    editor.contentEditable = 'true';
    document.body.appendChild(editor);
  });
  afterEach(() => editor.remove());

  const select = (sn: Node, so: number, en: Node = sn, eo: number = so) => {
    const r = document.createRange();
    r.setStart(sn, so);
    r.setEnd(en, eo);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(r);
  };

  it('true at the end of the text, false in the middle', () => {
    editor.textContent = 'hello';
    select(editor.firstChild!, 5);
    expect(caretAtEnd(editor)).toBe(true);
    select(editor.firstChild!, 2);
    expect(caretAtEnd(editor)).toBe(false);
  });

  it('trailing line breaks after the caret still count as the end', () => {
    editor.innerHTML = 'hi<br><br>';
    select(editor, 1);
    expect(caretAtEnd(editor)).toBe(true);
  });

  it('an inline image after the caret is not the end', () => {
    editor.innerHTML = 'hi<img data-image-id="x">';
    select(editor, 1);
    expect(caretAtEnd(editor)).toBe(false);
  });

  it('a non-collapsed selection is not "at the end"', () => {
    editor.textContent = 'hello';
    select(editor.firstChild!, 0, editor.firstChild!, 5);
    expect(caretAtEnd(editor)).toBe(false);
  });

  it('a caret outside the editor is not at its end', () => {
    const other = document.createElement('span');
    other.textContent = 'x';
    document.body.appendChild(other);
    select(other.firstChild!, 1);
    expect(caretAtEnd(editor)).toBe(false);
    other.remove();
  });
});

describe('wiring', () => {
  const read = (rel: string) => fs.readFileSync(path.join(__dirname, rel), 'utf8');

  it('SendChatContainer keydown dispatches both jumps under the right conditions', () => {
    const SRC = read('../../components/SendChatContainer.tsx');
    const start = SRC.indexOf('const handleKeyDown = useCallback(');
    const body = SRC.slice(start, SRC.indexOf('}, [', start));
    expect(body).toMatch(/ArrowLeft/);
    expect(body).toMatch(/ArrowRight/);
    expect(body).toMatch(/caretAtEnd\(editor\)/);
    expect(body).toMatch(/dispatchConversationJump\(/);
    expect(body).toMatch(/'beginning'/);
    expect(body).toMatch(/'end'/);
  });

  it('App listens and drives the scroll manager', () => {
    const SRC = read('../../components/App.tsx');
    const start = SRC.indexOf('addEventListener(CONVERSATION_JUMP_EVENT');
    expect(start).toBeGreaterThan(-1);
    const effect = SRC.slice(SRC.lastIndexOf('useEffect(', start), SRC.indexOf('}, [', start) + 80);
    expect(effect).toMatch(/scrollToActiveEnd\(\)/);
    expect(effect).toMatch(/conversationJumpEdge\('beginning', isTopToBottom\)/);
    expect(effect).toMatch(/removeEventListener\(CONVERSATION_JUMP_EVENT/);
    expect(effect).toMatch(/isTopToBottom\]/);
  });
});

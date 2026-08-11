import {
  captureEditorRange,
  chooseRecordingMimeType,
  insertTranscript,
} from '../voiceInput';

describe('chooseRecordingMimeType', () => {
  test('selects the first supported browser format', () => {
    const recorder = {
      isTypeSupported: (type: string) => type === 'audio/webm',
      // `as unknown as` because the stub supplies only the one static
      // method under test, while `typeof MediaRecorder` also demands a
      // constructor signature and `prototype` — TS rejects the direct
      // cast as insufficiently overlapping.  Matches the sibling test
      // below, which already casts this way.
    } as unknown as typeof MediaRecorder;

    expect(chooseRecordingMimeType(recorder)).toBe('audio/webm');
  });

  test('returns an empty string when the browser must choose', () => {
    const recorder = {
      isTypeSupported: () => false,
    } as unknown as typeof MediaRecorder;

    expect(chooseRecordingMimeType(recorder)).toBe('');
    expect(chooseRecordingMimeType(undefined)).toBe('');
  });
});

describe('composer transcript insertion', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    window.getSelection()?.removeAllRanges();
  });

  test('captures a selection belonging to the editor', () => {
    const editor = document.createElement('div');
    const text = document.createTextNode('hello');
    editor.appendChild(text);
    document.body.appendChild(editor);

    const range = document.createRange();
    range.setStart(text, 2);
    range.collapse(true);
    window.getSelection()?.addRange(range);

    const captured = captureEditorRange(editor);
    expect(captured).not.toBeNull();
    expect(captured?.startOffset).toBe(2);
  });

  test('rejects a selection outside the editor', () => {
    const editor = document.createElement('div');
    const outside = document.createTextNode('outside');
    document.body.append(editor, outside);

    const range = document.createRange();
    range.selectNode(outside);
    window.getSelection()?.addRange(range);

    expect(captureEditorRange(editor)).toBeNull();
  });

  test('inserts at the captured cursor and restores the cursor', () => {
    const editor = document.createElement('div');
    const text = document.createTextNode('hello world');
    editor.appendChild(text);
    document.body.appendChild(editor);

    const range = document.createRange();
    range.setStart(text, 6);
    range.collapse(true);

    expect(insertTranscript(editor, 'local voice ', range)).toBe(true);
    expect(editor.textContent).toBe('hello local voice world');
    expect(window.getSelection()?.isCollapsed).toBe(true);
  });

  test('appends safely when the saved range is outside the editor', () => {
    const editor = document.createElement('div');
    editor.textContent = 'existing ';
    const outside = document.createElement('div');
    document.body.append(editor, outside);

    const range = document.createRange();
    range.selectNodeContents(outside);

    expect(insertTranscript(editor, '<spoken text>', range)).toBe(true);
    expect(editor.textContent).toBe('existing <spoken text>');
    expect(editor.querySelector('spoken')).toBeNull();
  });

  test('does not insert an empty transcript', () => {
    const editor = document.createElement('div');
    expect(insertTranscript(editor, '   ')).toBe(false);
  });
});

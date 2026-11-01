import {
  captureEditorRange,
  chooseRecordingMimeType,
  formatByteSize,
  formatFileTranscript,
  insertTranscript,
  transcriptHeader,
  MAX_TRANSCRIPTION_BYTES,
  partitionAudioFiles,
  transcribableAudioType,
} from '../voiceInput';

describe('transcribable audio files', () => {
  const file = (name: string, type = '') => new File(['x'], name, { type });

  test('m4a and wav map to one canonical upload type whatever the browser reported', () => {
    expect(transcribableAudioType(file('memo.m4a', 'audio/x-m4a'))).toBe('audio/mp4');
    expect(transcribableAudioType(file('memo.M4A'))).toBe('audio/mp4');
    expect(transcribableAudioType(file('take.wav', 'audio/wave'))).toBe('audio/wav');
    expect(transcribableAudioType(file('take.WAV', 'audio/vnd.wave'))).toBe('audio/wav');
  });

  test('other files, extensionless names, and inherited keys are not audio', () => {
    expect(transcribableAudioType(file('song.mp3', 'audio/mpeg'))).toBeNull();
    expect(transcribableAudioType(file('notes.md'))).toBeNull();
    expect(transcribableAudioType(file('wav'))).toBeNull();
    expect(transcribableAudioType(file('x.constructor'))).toBeNull();
    expect(transcribableAudioType(file('x.wav.txt'))).toBeNull();
  });

  test('size limits read naturally in warnings', () => {
    expect(formatByteSize(1024 * 1024 * 1024)).toBe('1 GB');
    expect(formatByteSize(345 * 1024 * 1024)).toBe('345 MB');
    expect(formatByteSize(1.5 * 1024 * 1024)).toBe('1.5 MB');
  });

  test('the fallback ceiling fits a 30-minute 48 kHz stereo WAV', () => {
    expect(MAX_TRANSCRIPTION_BYTES).toBeGreaterThan(30 * 60 * 48_000 * 2 * 2);
  });

  test('partition separates audio and keeps arrival order in each group', () => {
    const batch = [file('a.wav'), file('b.png', 'image/png'), file('c.m4a'), file('d.md')];
    const { audio, other } = partitionAudioFiles(batch);
    expect(audio.map(f => f.name)).toEqual(['a.wav', 'c.m4a']);
    expect(other.map(f => f.name)).toEqual(['b.png', 'd.md']);
  });
});


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

describe('file transcript headers', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    window.getSelection()?.removeAllRanges();
  });

  test('the header names the transcription, the source type and the file', () => {
    expect(transcriptHeader('standup.m4a')).toBe('[Transcribed from M4A audio file: standup.m4a]');
    expect(transcriptHeader('Take 2.WAV')).toBe('[Transcribed from WAV audio file: Take 2.WAV]');
    expect(transcriptHeader('recording')).toBe('[Transcribed from audio file: recording]');
    expect(formatFileTranscript('a.wav', '  hello world ')).toBe(
      '[Transcribed from WAV audio file: a.wav]\nhello world',
    );
  });

  test('consecutive file transcripts land as separate blocks with real line breaks', () => {
    const editor = document.createElement('div');
    document.body.appendChild(editor);

    expect(insertTranscript(editor, formatFileTranscript('a.wav', 'first'), null, { block: true })).toBe(true);
    // The next file is inserted at the cursor the first insertion left behind,
    // exactly as transcribeAudioFiles captures it.
    const next = captureEditorRange(editor);
    expect(next).not.toBeNull();
    expect(insertTranscript(editor, formatFileTranscript('b.m4a', 'second'), next, { block: true })).toBe(true);

    expect(editor.innerHTML).toBe(
      '[Transcribed from WAV audio file: a.wav]<br>first<br>'
      + '[Transcribed from M4A audio file: b.m4a]<br>second<br>',
    );
  });

  test('a block after existing text starts on its own line', () => {
    const editor = document.createElement('div');
    editor.textContent = 'notes';
    document.body.appendChild(editor);

    insertTranscript(editor, formatFileTranscript('a.wav', 'spoken'), null, { block: true });
    expect(editor.innerHTML).toBe('notes<br>[Transcribed from WAV audio file: a.wav]<br>spoken<br>');
  });

  test('dictation without block mode is still inserted inline', () => {
    const editor = document.createElement('div');
    editor.textContent = 'say ';
    document.body.appendChild(editor);

    insertTranscript(editor, 'hello');
    expect(editor.innerHTML).toBe('say hello');
  });
});

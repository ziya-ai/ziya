/**
 * Dropped / picked audio files reach local transcription.
 *
 * Seam under test: the helpers in utils/voiceInput are unit-tested, but the
 * defect that matters is a missing connection -- a drop still handed to
 * ingestFiles (which reports .m4a/.wav as "Unsupported file type"), or an
 * audio branch that never reaches /api/transcribe.  Static wiring
 * assertions, same pattern as composeAnchorGuard.test.ts.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '../SendChatContainer.tsx'), 'utf8');

function section(start: string, end: string): string {
  const s = SRC.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = SRC.indexOf(end, s + start.length);
  expect(e).toBeGreaterThan(s);
  return SRC.slice(s, e);
}

describe('audio file transcription wiring', () => {
  test('drop and file picker both go through routeIncomingFiles, not ingestFiles', () => {
    const drop = section('const handleDrop = useCallback', '}, [routeIncomingFiles]);');
    expect(drop).toMatch(/routeIncomingFiles\(e\.dataTransfer\.files\)/);
    expect(drop).not.toMatch(/ingestFiles/);

    const pick = section('const handleFileSelect = useCallback', '}, [routeIncomingFiles]);');
    expect(pick).toMatch(/routeIncomingFiles\(files\)/);
    expect(pick).not.toMatch(/ingestFiles/);
  });

  test('the router partitions audio to transcription and the rest to ingestFiles', () => {
    const router = section('const routeIncomingFiles = useCallback', '}, [ingestFiles, transcribeAudioFiles]);');
    expect(router).toMatch(/partitionAudioFiles\(files\)/);
    expect(router).toMatch(/ingestFiles\(other\)/);
    expect(router).toMatch(/transcribeAudioFiles\(audio\)/);
  });

  test('audio files are uploaded through transcribeRecording with a canonical type', () => {
    const fn = section('const transcribeAudioFiles = useCallback', 'const routeIncomingFiles');
    expect(fn).toMatch(/transcribableAudioType\(file\)/);
    expect(fn).toMatch(/transcribeRecording\(/);
    expect(fn).toMatch(/MAX_TRANSCRIPTION_BYTES/);
    // First use installs the backend, as the microphone does.
    expect(fn).toMatch(/ensureTranscriptionBackend\(\)/);
  });

  test('transcribeRecording runs a background job using the given filename', () => {
    const fn = section('const transcribeRecording = useCallback', 'const ensureTranscriptionBackend');
    expect(fn).toMatch(/filename\?: string/);
    expect(fn).toMatch(/runTranscriptionJob\(blob, filename \|\|/);
    // The toast carries a Cancel control wired to the job.
    expect(fn).toMatch(/cancelTranscriptionJob\(jobId\)/);
    // An abort handle exists so unmount can stop polling.
    expect(fn).toMatch(/transcriptionAbortRef\.current = controller/);
  });

  test('a transcript finishing after a conversation switch is kept, not dropped', () => {
    const fn = section('const transcribeRecording = useCallback', 'const ensureTranscriptionBackend');
    // Compared against the LIVE id: a closure value is the id at call time,
    // so it could never differ from the target.
    expect(fn).toMatch(/liveConversationIdRef\.current !== targetConversationId/);
    expect(fn).toMatch(/dispatchComposerInject\(targetConversationId, text, \{ preserveExisting: true \}\)/);
    expect(SRC).toMatch(/liveConversationIdRef\.current = currentConversationId/);
  });

  test('a file transcript gets a source header and is inserted as a block; dictation does not', () => {
    const fn = section('const transcribeRecording = useCallback', 'const ensureTranscriptionBackend');
    expect(fn).toMatch(/filename \? formatFileTranscript\(filename, spoken\) : spoken/);
    expect(fn).toMatch(/insertTranscript\(editorRef\.current, text, recordingRangeRef\.current, \{ block: Boolean\(filename\) \}\)/);
    // The empty-speech check runs on the raw transcript, not on the header.
    expect(fn).toMatch(/if \(!spoken\.trim\(\)\)/);
  });

  test('the size ceiling comes from the server status, with a 1 GB fallback', () => {
    const fn = section('const transcribeAudioFiles = useCallback', 'const routeIncomingFiles');
    expect(fn).toMatch(/voiceStatus\?\.max_bytes \|\| MAX_TRANSCRIPTION_BYTES/);
  });

  test('a long job only blocks Send in the conversation it belongs to', () => {
    const disabled = section('const isDisabled = useMemo', ');');
    expect(disabled).toMatch(/voiceConversationId === currentConversationId/);
  });

  test('unmount aborts an in-flight transcription job', () => {
    const cleanup = section('useEffect(() => () => {', '}, []);');
    expect(cleanup).toMatch(/transcriptionAbortRef\.current\?\.abort\(\)/);
  });

  test('the microphone still installs through the shared helper', () => {
    const fn = section('const startVoiceRecording = useCallback', 'const stopVoiceRecording');
    expect(fn).toMatch(/ensureTranscriptionBackend\(\)/);
    expect(fn).not.toMatch(/\/api\/transcribe\/install/);
  });
});

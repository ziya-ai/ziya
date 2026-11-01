/**
 * Client for the local transcription job API (/api/transcribe/jobs).
 *
 * A 30-minute recording takes minutes to transcribe on CPU, so the server
 * runs it as a background job: the upload returns a job id at once and the
 * job is polled for progress and, at the end, the transcript.
 */

export type TranscriptionJobState = 'queued' | 'running' | 'done' | 'error' | 'cancelled';

export interface TranscriptionJobSnapshot {
  job_id: string;
  filename: string;
  state: TranscriptionJobState;
  /** 0..1, by audio position of the last decoded segment. */
  progress: number;
  /** Text so far while running; the full transcript once done. */
  text: string;
  error?: string | null;
  cancel_requested?: boolean;
}

const TERMINAL_STATES: ReadonlySet<TranscriptionJobState> = new Set(['done', 'error', 'cancelled']);

export function isTerminalJobState(state: TranscriptionJobState): boolean {
  return TERMINAL_STATES.has(state);
}

/** Consecutive failed polls tolerated before giving up: a blip, not an outage. */
const MAX_POLL_FAILURES = 5;

const defaultFetch: typeof fetch = (input, init) => fetch(input, init);

function abortError(): Error {
  return new DOMException('Transcription polling aborted', 'AbortError');
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** FastAPI errors are JSON {detail}; the request-size middleware's are plain text. */
async function errorDetail(response: Response, fallback: string): Promise<string> {
  const raw = await response.text().catch(() => '');
  try {
    const parsed = JSON.parse(raw);
    if (parsed?.detail) return String(parsed.detail);
  } catch { /* not JSON */ }
  return raw.trim() || fallback;
}

export async function cancelTranscriptionJob(
  jobId: string,
  fetchImpl: typeof fetch = defaultFetch,
): Promise<void> {
  try {
    await fetchImpl(`/api/transcribe/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
  } catch { /* the poll loop reports the outcome */ }
}

/**
 * Upload audio, then poll its job until it finishes, is cancelled, or fails.
 * Resolves with the terminal snapshot (check `state`).  Aborting `signal`
 * stops polling, asks the server to cancel, and rejects with an AbortError.
 */
export async function runTranscriptionJob(
  audio: Blob,
  filename: string,
  options: {
    onProgress?: (snapshot: TranscriptionJobSnapshot) => void;
    signal?: AbortSignal;
    pollMs?: number;
    fetchImpl?: typeof fetch;
  } = {},
): Promise<TranscriptionJobSnapshot> {
  const { onProgress, signal, pollMs = 1000, fetchImpl = defaultFetch } = options;

  const form = new FormData();
  form.append('file', audio, filename);
  const started = await fetchImpl('/api/transcribe/jobs', { method: 'POST', body: form, signal });
  if (!started.ok) {
    throw new Error(await errorDetail(started, `Transcription upload failed (HTTP ${started.status})`));
  }
  let snapshot = await started.json() as TranscriptionJobSnapshot;
  onProgress?.(snapshot);

  const jobId = snapshot.job_id;
  const url = `/api/transcribe/jobs/${encodeURIComponent(jobId)}`;
  const onAbort = () => { void cancelTranscriptionJob(jobId, fetchImpl); };
  signal?.addEventListener('abort', onAbort, { once: true });
  try {
    let failures = 0;
    while (!isTerminalJobState(snapshot.state)) {
      await sleep(pollMs, signal);
      let response: Response;
      try {
        response = await fetchImpl(url, { signal });
      } catch (error) {
        if (signal?.aborted) throw error;
        if (++failures >= MAX_POLL_FAILURES) {
          throw new Error('Lost contact with the local transcription server');
        }
        continue;
      }
      if (response.status === 404) {
        throw new Error('The transcription job is gone; the Ziya server may have restarted');
      }
      if (!response.ok) {
        if (++failures >= MAX_POLL_FAILURES) {
          throw new Error(await errorDetail(response, `Transcription status failed (HTTP ${response.status})`));
        }
        continue;
      }
      failures = 0;
      snapshot = await response.json() as TranscriptionJobSnapshot;
      onProgress?.(snapshot);
    }
    return snapshot;
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}

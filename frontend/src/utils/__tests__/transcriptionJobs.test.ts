import { runTranscriptionJob, TranscriptionJobSnapshot } from '../transcriptionJobs';

const snap = (over: Partial<TranscriptionJobSnapshot>): TranscriptionJobSnapshot => ({
  job_id: 'j1', filename: 'meeting.m4a', state: 'queued', progress: 0, text: '', ...over,
});

// jsdom here has no fetch Response; the client only reads ok/status/json/text.
const fakeResponse = (raw: string, status: number) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => raw,
  json: async () => JSON.parse(raw),
}) as unknown as Response;

const json = (body: unknown, status = 200) => fakeResponse(JSON.stringify(body), status);

/** fetch stub answering from a queue of responses, recording each call. */
function scripted(responses: Array<Response | Error>) {
  const calls: Array<{ url: string; method: string }> = [];
  const fetchImpl = jest.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: init?.method || 'GET' });
    const next = responses.shift();
    if (!next) throw new Error(`unexpected fetch ${String(input)}`);
    if (next instanceof Error) throw next;
    return next;
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

describe('runTranscriptionJob', () => {
  test('uploads once, polls to completion, and reports every snapshot', async () => {
    const { fetchImpl, calls } = scripted([
      json(snap({ state: 'queued' }), 202),
      json(snap({ state: 'running', progress: 0.4, text: 'hello' })),
      json(snap({ state: 'done', progress: 1, text: 'hello world' })),
    ]);
    const progress: number[] = [];
    const result = await runTranscriptionJob(new Blob(['a']), 'meeting.m4a', {
      fetchImpl, pollMs: 0, onProgress: s => progress.push(s.progress),
    });
    expect(result.text).toBe('hello world');
    expect(progress).toEqual([0, 0.4, 1]);
    expect(calls[0]).toEqual({ url: '/api/transcribe/jobs', method: 'POST' });
    expect(calls.slice(1).every(c => c.url === '/api/transcribe/jobs/j1')).toBe(true);
  });

  test('surfaces the server detail when the upload is refused', async () => {
    const { fetchImpl } = scripted([json({ detail: 'Audio file exceeds the 1 GB limit' }, 413)]);
    await expect(runTranscriptionJob(new Blob(['a']), 'x.wav', { fetchImpl, pollMs: 0 }))
      .rejects.toThrow('Audio file exceeds the 1 GB limit');
  });

  test('a plain-text 413 from the request-size middleware is still readable', async () => {
    const { fetchImpl } = scripted([fakeResponse('Request size 2 exceeds limit 1', 413)]);
    await expect(runTranscriptionJob(new Blob(['a']), 'x.wav', { fetchImpl, pollMs: 0 }))
      .rejects.toThrow('Request size 2 exceeds limit 1');
  });

  test('a transient poll failure is tolerated', async () => {
    const { fetchImpl } = scripted([
      json(snap({}), 202),
      new TypeError('Failed to fetch'),
      json(snap({ state: 'done', text: 'ok' })),
    ]);
    const result = await runTranscriptionJob(new Blob(['a']), 'x.wav', { fetchImpl, pollMs: 0 });
    expect(result.text).toBe('ok');
  });

  test('a vanished job (server restart) fails with an explanation', async () => {
    const { fetchImpl } = scripted([json(snap({}), 202), json({ detail: 'Unknown' }, 404)]);
    await expect(runTranscriptionJob(new Blob(['a']), 'x.wav', { fetchImpl, pollMs: 0 }))
      .rejects.toThrow(/server may have restarted/);
  });

  test('aborting stops polling and asks the server to cancel the job', async () => {
    const controller = new AbortController();
    const { fetchImpl, calls } = scripted([
      json(snap({}), 202),
      json(snap({ state: 'cancelled' })), // answers the cancel POST
    ]);
    const pending = runTranscriptionJob(new Blob(['a']), 'x.wav', {
      fetchImpl, pollMs: 60_000, signal: controller.signal,
    });
    await new Promise(r => setTimeout(r, 0));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(calls.map(c => `${c.method} ${c.url}`)).toEqual([
      'POST /api/transcribe/jobs',
      'POST /api/transcribe/jobs/j1/cancel',
    ]);
  });
});

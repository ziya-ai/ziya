/**
 * Wiring for job chips in the open conversation's TaskRail.  Static source
 * assertions, as in runStatusIndexWiring.test.ts: the hook, the rail and
 * the chip can each be right while nothing hands the jobs across.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');

const CONV = () => read('components/Conversation.tsx');
const HOOK = () => read('hooks/useConversationJobs.ts');
const RAIL = () => read('components/TaskCard/TaskRail.tsx');
const CHIP = () => read('components/TaskCard/JobChip.tsx');

describe('conversation', () => {
  it('loads this chat\'s jobs and hands them, with the jump handler, to the rail', () => {
    const s = CONV();
    expect(s).toMatch(/useConversationJobs\(/);
    expect(s).toMatch(/<TaskRail bindings=\{visibleBindings\} projectId=\{[^}]+\}\s*\n?\s*jobs=\{conversationJobs\} onJobsChanged=\{refreshJobs\} onJumpToJobStart=\{jumpToJobStart\}/);
    expect(s).toMatch(/turnIndexForTime\(/);
  });
  it('posts exit cards from the SETTLED list, not the rail list', () => {
    const s = CONV();
    expect(s).toMatch(/import JobExitFeed from '\.\/TaskCard\/JobExitFeed'/);
    expect(s).toMatch(/settledJobs: settledConversationJobs/);
    expect(s).toMatch(/<JobExitFeed[\s\S]*?jobs=\{settledConversationJobs\}[\s\S]*?onJumpToJobStart=\{jumpToJobStart\}/);
    expect(s).not.toMatch(/<JobExitFeed[^>]*jobs=\{conversationJobs\}/);
  });
});

describe('poll hook', () => {
  it('polls while a visible job runs OR the turn streams (job_start has no client event)', () => {
    const h = HOOK();
    expect(h).toMatch(/const POLL_MS = 5000/);
    expect(h).toMatch(/if \(!\(anyRunning \|\| isStreaming\)\) return;/);
    expect(h).toMatch(/document\.hidden/);
  });
  it('re-reads when a stream ends, which is when a job_start result has landed', () => {
    expect(HOOK()).toMatch(/prevStreamingRef/);
  });
  it('rail reads liveJobs (current-turn jobs admitted); exit feed reads settledJobs', () => {
    const h = HOOK();
    expect(h).toMatch(/streamStartRef/);
    expect(h).toMatch(/const visible = liveJobs\(jobs, isStreaming, streamStartRef\.current\)/);
    expect(h).toMatch(/const settled = settledJobs\(jobs, isStreaming, streamStartRef\.current\)/);
    expect(h).toMatch(/return \{ jobs: stableVisible, settledJobs: stableSettled, refresh \}/);
  });
});

describe('rail and chip', () => {
  it('the rail takes jobs and shows a chip per running job', () => {
    expect(RAIL()).toMatch(/jobs\?: ReadonlyArray<JobRecord>/);
    expect(RAIL()).toMatch(/railJobs\(jobs\)/);
    expect(RAIL()).toMatch(/<JobChip/);
  });
  it('the chip kills through the jobs API and jumps via started_at', () => {
    expect(CHIP()).toMatch(/killJob\(projectId, job\.job_id\)/);
    expect(CHIP()).toMatch(/onJumpToJobStart\?\.\(job\.started_at!?\)/);
  });
});

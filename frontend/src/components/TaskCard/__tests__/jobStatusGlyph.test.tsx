/**
 * @jest-environment jsdom
 *
 * Sidebar indicator for chat-started managed jobs (job_start).
 *
 * A job lived only in the tool results of the turn that started it, which
 * scroll away.  The sidebar now draws one terminal glyph per job state on
 * the conversation's row, fed by the ``jobs`` field of the run-status index:
 *
 *   running   teal, pulsing          — the only state that animates
 *   exited 0  green, ✓ badge
 *   exited ≠0 red,   ✗ badge
 *   lost      amber, ! badge
 *
 * Finished states CLEAR ON VISIT: a job that ended before the user last
 * opened the conversation is not shown, and the open conversation never
 * shows finished jobs at all (the rail and the exit card cover it there).
 * Compact rows drop the badge; colour and pulse carry the state.
 *
 * FAILS before the feature: none of these modules exist.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import {
    jobClusters, jobKind, jobsSignature, JOB_GLYPH_COLOR,
} from '../jobStatusGlyph';
import JobStatusGlyphs from '../JobStatusGlyphs';
import { readJobsSeen, markJobsSeen } from '../../../utils/jobSeen';
import type { JobSummary } from '../../../services/taskRunApi';

const running = (id = 'j-run'): JobSummary =>
    ({ job_id: id, label: 'embed corpus', state: 'running', started_at: 1000, runtime_s: 4320 });
const ok = (ended = 2000, id = 'j-ok'): JobSummary =>
    ({ job_id: id, label: 'pytest', state: 'exited', exit_code: 0, started_at: 1000, ended_at: ended, duration_s: 841 });
const failed = (ended = 2000): JobSummary =>
    ({ job_id: 'j-fail', label: 'build', state: 'exited', exit_code: 1, started_at: 1000, ended_at: ended });
const signalled = (): JobSummary =>
    // The wrapper records the signal as a NUMBER (-returncode), not a name.
    ({ job_id: 'j-sig', label: 'killed', state: 'exited', exit_code: null, signal: 15, started_at: 1000, ended_at: 2000 });
const lost = (): JobSummary =>
    ({ job_id: 'j-lost', label: 'scan', state: 'lost', started_at: 1500, reason: 'wrapper exited without writing a status' });

describe('jobKind', () => {
    it('maps the four states; a signal exit is a failure, not a success', () => {
        expect(jobKind(running())).toBe('running');
        expect(jobKind(ok())).toBe('ok');
        expect(jobKind(failed())).toBe('failed');
        expect(jobKind(signalled())).toBe('failed');
        expect(jobKind(lost())).toBe('lost');
    });
});

describe('jobClusters', () => {
    it('one cluster per kind with a count, running first then failures before successes', () => {
        const cs = jobClusters([ok(), failed(), running('a'), running('b'), lost()]);
        expect(cs.map(c => [c.kind, c.count])).toEqual([
            ['running', 2], ['failed', 1], ['lost', 1], ['ok', 1],
        ]);
        expect(cs[0].animate).toBe(true);
        expect(cs.slice(1).every(c => !c.animate)).toBe(true);
        expect(cs.map(c => c.badge)).toEqual([null, '✗', '!', '✓']);
        expect(cs.map(c => c.color)).toEqual([
            JOB_GLYPH_COLOR.running, JOB_GLYPH_COLOR.failed, JOB_GLYPH_COLOR.lost, JOB_GLYPH_COLOR.ok,
        ]);
    });

    it('clear on visit: finished jobs that ended before the last visit are dropped, running never is', () => {
        const jobs = [running(), ok(2000), failed(3000), lost()];
        // Visited at t=2500 (seconds): the ok job (ended 2000) and the lost
        // job (started 1500, no end) are seen; the failure at 3000 is new.
        const cs = jobClusters(jobs, { seenAt: 2500 });
        expect(cs.map(c => c.kind)).toEqual(['running', 'failed']);
        // Never visited: everything shows.
        expect(jobClusters(jobs).map(c => c.kind)).toEqual(['running', 'failed', 'lost', 'ok']);
    });

    it('the open conversation shows only running jobs', () => {
        const cs = jobClusters([running(), ok(), failed()], { hideFinished: true });
        expect(cs.map(c => c.kind)).toEqual(['running']);
        expect(jobClusters([ok()], { hideFinished: true })).toEqual([]);
    });

    it('hints carry what a glance cannot: label, elapsed or exit code', () => {
        // Elapsed is taken from the clock, not the poll's runtime_s, so it
        // does not jump by the poll interval; pin "now" at 1000 + 4320 s.
        const [run] = jobClusters([running()], { nowS: 5320 });
        expect(run.hint).toMatch(/embed corpus/);
        expect(run.hint).toMatch(/1h 12m/);
        const [fail] = jobClusters([failed()]);
        expect(fail.hint).toMatch(/exit 1/);
        const [sig] = jobClusters([signalled()]);
        expect(sig.hint).toMatch(/signal 15/);
        const [two] = jobClusters([ok(2000, 'a'), ok(2000, 'b')]);
        expect(two.hint).toMatch(/^2 jobs finished/);
    });
});

describe('jobsSignature', () => {
    it('changes when a job changes state, so the poll hook does not swallow a finish as "no change"', () => {
        const before = { c1: [running()] };
        const after = { c1: [{ ...running(), state: 'exited' as const, exit_code: 0, ended_at: 5 }] };
        expect(jobsSignature(before)).not.toBe(jobsSignature(after));
        expect(jobsSignature(before)).toBe(jobsSignature({ c1: [running()] }));
        expect(jobsSignature(undefined)).toBe('');
    });
});

describe('jobSeen marker', () => {
    beforeEach(() => localStorage.clear());
    it('is per project, round-trips, and survives corrupt storage', () => {
        expect(readJobsSeen('P')).toEqual({});
        const m = markJobsSeen('P', 'c1', 1234);
        expect(m).toEqual({ c1: 1234 });
        expect(readJobsSeen('P')).toEqual({ c1: 1234 });
        expect(readJobsSeen('Q')).toEqual({});
        localStorage.setItem('ZIYA_JOBS_SEEN:P', '{nope');
        expect(readJobsSeen('P')).toEqual({});
        expect(readJobsSeen(undefined)).toEqual({});
    });
});

describe('<JobStatusGlyphs>', () => {
    it('renders one glyph per cluster with badge, count from 2, and an accessible name', () => {
        render(<JobStatusGlyphs jobs={[running('a'), running('b'), failed()]} />);
        const glyphs = screen.getAllByTestId('job-glyph');
        expect(glyphs).toHaveLength(2);
        expect(glyphs[0]).toHaveAttribute('data-kind', 'running');
        expect(glyphs[0]).toHaveAttribute('aria-label', expect.stringMatching(/2 jobs running/));
        expect(glyphs[0]).toHaveTextContent('2');
        expect(glyphs[0].querySelector('[data-testid="job-glyph-pulse"]')).not.toBeNull();
        expect(glyphs[1]).toHaveAttribute('data-kind', 'failed');
        expect(glyphs[1]).toHaveTextContent('✗');
        expect(glyphs[1].querySelector('[data-testid="job-glyph-pulse"]')).toBeNull();
    });

    it('compact drops the badge but keeps colour, pulse and the accessible name', () => {
        render(<JobStatusGlyphs jobs={[ok(), running()]} compact />);
        const glyphs = screen.getAllByTestId('job-glyph');
        expect(glyphs.map(g => g.getAttribute('data-kind'))).toEqual(['running', 'ok']);
        expect(screen.queryByText('✓')).toBeNull();
        expect(glyphs[1]).toHaveAttribute('aria-label', expect.stringMatching(/finished/));
        expect(glyphs[1]).toHaveStyle({ color: JOB_GLYPH_COLOR.ok });
    });

    it('renders nothing when every job has been seen', () => {
        const { container } = render(<JobStatusGlyphs jobs={[ok(2000)]} seenAt={3000} />);
        expect(container).toBeEmptyDOMElement();
    });
});

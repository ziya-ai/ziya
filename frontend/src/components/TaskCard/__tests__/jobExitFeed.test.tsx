/**
 * @jest-environment jsdom
 *
 * Exit card for a managed job, posted at the bottom of the open thread.
 *
 * Only a job the user could already see in the rail — settled (its
 * starting turn had ended) and RUNNING on one read — posts a card when a
 * later read finds it exited or lost.  So:
 *
 *   - a job that started and exited inside one turn never posts (it was
 *     never in the settled list as running), which is what keeps a turn
 *     running several short commands from churning the thread;
 *   - a job already finished when the conversation opens never posts
 *     (no previous state — same rule as TaskUpdateFeed);
 *   - a conversation switch clears the feed.
 *
 * FAILS before the feature: jobExitTransitions / JobExitFeed do not exist.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { jobExitTransitions, describeJobExit } from '../jobRail';
import JobExitFeed from '../JobExitFeed';
import type { JobRecord } from '../../../services/jobsApi';

jest.mock('../../../services/jobsApi', () => ({
    getJob: jest.fn(async () => ({ tail: 'last line' })),
    killJob: jest.fn(),
    listConversationJobs: jest.fn(async () => []),
}));

const running = (id = 'j1'): JobRecord =>
    ({ job_id: id, label: 'embed corpus', state: 'running', started_at: 1000 });
const failed = (id = 'j1'): JobRecord =>
    ({ job_id: id, label: 'embed corpus', state: 'exited', exit_code: 1, started_at: 1000, ended_at: 1842, duration_s: 842 });
const ok = (id = 'j1'): JobRecord =>
    ({ job_id: id, label: 'embed corpus', state: 'exited', exit_code: 0, started_at: 1000, ended_at: 1041, duration_s: 41 });
const killed = (id = 'j1'): JobRecord =>
    ({ job_id: id, label: 'embed corpus', state: 'exited', exit_code: null, signal: 15, started_at: 1000, ended_at: 1100, duration_s: 100 });
const lost = (id = 'j1'): JobRecord =>
    ({ job_id: id, label: 'embed corpus', state: 'lost', started_at: 1000, reason: 'wrapper exited without writing a status' });

describe('jobExitTransitions', () => {
    it('reports jobs that were running on the previous read and are finished now', () => {
        expect(jobExitTransitions([running('a'), running('b')], [failed('a'), running('b')]).map(j => j.job_id))
            .toEqual(['a']);
    });
    it('a job first seen already finished is not a transition', () => {
        expect(jobExitTransitions([], [ok('a')])).toEqual([]);
        expect(jobExitTransitions([running('b')], [ok('a'), running('b')])).toEqual([]);
    });
    it('a job that is still running, or has disappeared, is not a transition', () => {
        expect(jobExitTransitions([running('a')], [running('a')])).toEqual([]);
        expect(jobExitTransitions([running('a')], [])).toEqual([]);
    });
});

describe('describeJobExit', () => {
    it('names the outcome and carries exit code, signal or reason with the duration', () => {
        expect(describeJobExit(ok())).toMatchObject({ kind: 'ok', word: 'finished', detail: 'exit 0 after 41s' });
        expect(describeJobExit(failed())).toMatchObject({ kind: 'failed', word: 'failed', detail: 'exit 1 after 14m 02s' });
        expect(describeJobExit(killed())).toMatchObject({ kind: 'failed', word: 'stopped', detail: 'signal 15 after 1m 40s' });
        expect(describeJobExit(lost())).toMatchObject({ kind: 'lost', word: 'lost', detail: /without writing a status/ });
    });
});

describe('<JobExitFeed>', () => {
    const base = { conversationId: 'c1', projectId: 'P' };

    it('posts a card when a running job is next read as exited, with the jump action', () => {
        const jump = jest.fn();
        const { rerender } = render(<JobExitFeed {...base} jobs={[running()]} onJumpToJobStart={jump} />);
        expect(screen.queryByTestId('job-exit-card')).toBeNull();
        rerender(<JobExitFeed {...base} jobs={[failed()]} onJumpToJobStart={jump} />);
        const card = screen.getByTestId('job-exit-card');
        expect(card).toHaveAttribute('data-job-id', 'j1');
        expect(card).toHaveAttribute('data-kind', 'failed');
        expect(card).toHaveTextContent('embed corpus');
        expect(card).toHaveTextContent(/exit 1 after 14m 02s/);
        fireEvent.click(screen.getByRole('button', { name: /turn that started it/i }));
        expect(jump).toHaveBeenCalledWith(1000);
    });

    it('a job already finished on the first read posts nothing', () => {
        render(<JobExitFeed {...base} jobs={[ok()]} />);
        expect(screen.queryByTestId('job-exit-card')).toBeNull();
    });

    it('a job that was never seen running (started and exited inside one turn) posts nothing', () => {
        const { rerender } = render(<JobExitFeed {...base} jobs={[running('long')]} />);
        rerender(<JobExitFeed {...base} jobs={[ok('short'), running('long')]} />);
        expect(screen.queryByTestId('job-exit-card')).toBeNull();
    });

    it('dismiss removes the card; a conversation switch clears the feed', () => {
        const { rerender } = render(<JobExitFeed {...base} jobs={[running('a'), running('b')]} />);
        rerender(<JobExitFeed {...base} jobs={[ok('a'), lost('b')]} />);
        expect(screen.getAllByTestId('job-exit-card')).toHaveLength(2);
        fireEvent.click(screen.getAllByRole('button', { name: /dismiss/i })[0]);
        expect(screen.getAllByTestId('job-exit-card')).toHaveLength(1);
        rerender(<JobExitFeed {...base} conversationId="c2" jobs={[]} />);
        expect(screen.queryByTestId('job-exit-card')).toBeNull();
    });
});

/**
 * @jest-environment jsdom
 *
 * Job chips in the TaskRail: the open conversation's view of its running
 * managed jobs (job_start), beside the task chips.
 *
 *   - Only RUNNING jobs earn a chip.  The rail is "what is happening now";
 *     a finished job is the exit card's business (next step), and the
 *     sidebar glyph already covers conversations you are not in.
 *   - "Jump to the turn that started it" needs no new server state: the
 *     job's ``started_at`` (epoch SECONDS) is resolved against message
 *     ``_timestamp`` (epoch MILLISECONDS) by the same rule that places
 *     unanchored task tiles, landing on the reply of the turn.
 *   - The rail renders when there are jobs and no tasks at all.
 *
 * FAILS before the feature: the modules do not exist.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { railJobs, settledJobs, liveJobs, INTRA_TURN_MIN_RUNTIME_S, turnIndexForTime, scrollToMessage, MESSAGE_INDEX_ATTR } from '../jobRail';
import TaskRail from '../TaskRail';
import type { JobRecord } from '../../../services/jobsApi';

jest.mock('../../../services/jobsApi', () => ({
    getJob: jest.fn(async () => ({ tail: '' })),
    killJob: jest.fn(async () => ({ killed: true, state: 'exited' })),
    listConversationJobs: jest.fn(async () => []),
}));

const running = (id = 'j-run', started = 1000): JobRecord =>
    ({ job_id: id, label: 'embed corpus', state: 'running', started_at: started, runtime_s: 5, command: 'python3 x.py' });
const exited = (): JobRecord =>
    ({ job_id: 'j-ok', label: 'pytest', state: 'exited', exit_code: 0, started_at: 900, ended_at: 950 });

describe('railJobs', () => {
    it('keeps running jobs only, newest first', () => {
        expect(railJobs([exited(), running('a', 10), running('b', 20)]).map(j => j.job_id)).toEqual(['b', 'a']);
        expect(railJobs(undefined)).toEqual([]);
    });
});

describe('settledJobs', () => {
    // A job is shown only once the turn that started it has ended.  A job
    // that starts and exits inside one turn never reaches the rail, so a
    // turn running several short commands does not churn the strip.
    it('holds back jobs started during the turn still streaming', () => {
        const older = running('old', 100);
        const fresh = running('new', 250);
        expect(settledJobs([fresh, older], true, 200).map(j => j.job_id)).toEqual(['old']);
    });
    it('shows everything once the turn has ended', () => {
        const older = running('old', 100);
        const fresh = running('new', 250);
        expect(settledJobs([fresh, older], false, 200).map(j => j.job_id)).toEqual(['new', 'old']);
    });
    it('streaming with no known start holds back nothing from earlier turns', () => {
        expect(settledJobs([running('old', 100)], true, null).map(j => j.job_id)).toEqual(['old']);
    });
});

describe('liveJobs (the rail)', () => {
    // The window that sent the turn is still streaming while the model
    // waits on its job; the chip (and its kill button) must show there.
    const withRuntime = (id: string, started: number, runtime_s: number): JobRecord =>
        ({ ...running(id, started), runtime_s });
    it('shows a current-turn job once it has run the minimum', () => {
        const long = withRuntime('long', 250, INTRA_TURN_MIN_RUNTIME_S + 400);
        expect(liveJobs([long], true, 200).map(j => j.job_id)).toEqual(['long']);
        // ...while the exit-card list still holds it back.
        expect(settledJobs([long], true, 200)).toEqual([]);
    });
    it('keeps a just-started current-turn job off the rail', () => {
        const quick = withRuntime('quick', 250, 1);
        expect(liveJobs([quick], true, 200)).toEqual([]);
    });
    it('a current-turn job that already exited is not admitted', () => {
        const done: JobRecord = { ...withRuntime('done', 250, 60), state: 'exited', exit_code: 0 };
        expect(liveJobs([done], true, 200)).toEqual([]);
    });
    it('earlier-turn jobs and the idle case match settledJobs', () => {
        const older = running('old', 100);
        const fresh = withRuntime('new', 250, 0);
        expect(liveJobs([fresh, older], true, 200).map(j => j.job_id)).toEqual(['old']);
        expect(liveJobs([fresh, older], false, 200).map(j => j.job_id)).toEqual(['new', 'old']);
    });
});

describe('turnIndexForTime', () => {
    // _timestamp is ms; the job time is seconds.
    const msgs = [
        { role: 'human', _timestamp: 100_000 },      // 0
        { role: 'assistant', _timestamp: 101_000 },  // 1
        { role: 'human', _timestamp: 200_000 },      // 2
        { role: 'assistant', _timestamp: 260_000 },  // 3  (job started at 230 s, mid-turn)
        { role: 'human', _timestamp: 300_000 },      // 4  (reply still streaming)
    ];
    it('lands on the reply of the turn in which the job started', () => {
        expect(turnIndexForTime(msgs, 230)).toBe(3);
        expect(turnIndexForTime(msgs, 100.5)).toBe(1);
    });
    it('a job older than every message has no turn', () => {
        expect(turnIndexForTime(msgs, 50)).toBeNull();
    });
    it('a job in the turn still streaming lands on its prompt', () => {
        expect(turnIndexForTime(msgs, 305)).toBe(4);
    });
});

describe('scrollToMessage', () => {
    it('scrolls to and flashes the message element', () => {
        const el = document.createElement('div');
        el.setAttribute(MESSAGE_INDEX_ATTR, '3');
        el.scrollIntoView = jest.fn();
        document.body.appendChild(el);
        scrollToMessage(3);
        expect(el.scrollIntoView).toHaveBeenCalled();
        expect(el.className).toMatch(/flash/);
        el.remove();
    });
});

describe('<TaskRail> with jobs', () => {
    it('renders a job chip with no tasks present, with label and live dot', () => {
        render(<TaskRail bindings={[]} projectId="P" jobs={[running()]} />);
        const rail = screen.getByTestId('task-rail');
        expect(rail).toHaveTextContent(/JOBS · 1/);
        expect(rail).not.toHaveTextContent(/TASKS/);
        const chip = screen.getByTestId('job-chip');
        expect(chip).toHaveAttribute('data-job-id', 'j-run');
        expect(chip).toHaveTextContent('embed corpus');
        expect(chip.querySelector('.tc-rail-chip__dot--live')).not.toBeNull();
    });

    it('renders nothing when there are neither tasks nor running jobs', () => {
        const { container } = render(<TaskRail bindings={[]} projectId="P" jobs={[exited()]} />);
        expect(container).toBeEmptyDOMElement();
    });
});

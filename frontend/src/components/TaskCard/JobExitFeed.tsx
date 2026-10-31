/**
 * JobExitFeed — the exit card for a managed job, posted at the bottom of
 * the open thread when a job the rail was showing finishes.
 *
 * The job counterpart of TaskUpdateFeed, with the same shape and the same
 * reasons: a chip quietly vanishing from the rail is the failure mode this
 * whole feature exists to fix, so the exit is announced once, next to the
 * composer, with the outcome and a way back to the turn that started it.
 * Not persisted and not a message; the model sees the exit through its own
 * job_wait.  Cleared on conversation switch.
 *
 * Gating lives in jobRail.jobExitTransitions: only a job seen RUNNING in
 * the settled list posts, so a job that started and exited inside one turn
 * (whose result is in the tool output already on screen) never does.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { CloseOutlined, VerticalAlignTopOutlined } from '@ant-design/icons';
import { getJob, type JobRecord } from '../../services/jobsApi';
import { JOB_GLYPH_COLOR } from './jobStatusGlyph';
import { describeJobExit, jobExitTransitions } from './jobRail';
import './task-surfacing.css';

interface Props {
    conversationId: string;
    projectId: string;
    /** The settled job list the rail reads (useConversationJobs). */
    jobs: ReadonlyArray<JobRecord>;
    onJumpToJobStart?: (startedAtS: number) => void;
}

interface Card { job: JobRecord; at: number }

const fmtTime = (ms: number) =>
    new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export const JobExitFeed: React.FC<Props> = ({ conversationId, projectId, jobs, onJumpToJobStart }) => {
    const [feed, setFeed] = useState<Card[]>([]);
    const [tails, setTails] = useState<Record<string, string>>({});
    const prevRef = useRef<ReadonlyArray<JobRecord>>([]);

    useEffect(() => {
        setFeed([]);
        setTails({});
        prevRef.current = [];
    }, [conversationId]);

    useEffect(() => {
        const exited = jobExitTransitions(prevRef.current, jobs);
        prevRef.current = jobs;
        if (exited.length === 0) return;
        const now = Date.now();
        setFeed(prev => [
            ...prev.filter(c => !exited.some(j => j.job_id === c.job.job_id)),
            ...exited.map(job => ({ job, at: now })),
        ]);
    }, [jobs]);

    const dismiss = useCallback((jobId: string) =>
        setFeed(prev => prev.filter(c => c.job.job_id !== jobId)), []);

    const showTail = useCallback((jobId: string) => {
        if (!projectId) return;
        getJob(projectId, jobId, 30)
            .then(d => setTails(prev => ({ ...prev, [jobId]: d.tail || '(no output)' })))
            .catch(() => setTails(prev => ({ ...prev, [jobId]: '(tail unavailable)' })));
    }, [projectId]);

    if (feed.length === 0) return null;

    return (
        <div className="tc-update-feed" data-testid="job-exit-feed">
            {feed.map(({ job, at }) => {
                const x = describeJobExit(job);
                const name = job.label || job.command || job.job_id;
                const tail = tails[job.job_id];
                return (
                    <div key={job.job_id} className={`tc-update tc-update--job-${x.kind}`}
                        style={{ ['--tc-chip' as any]: JOB_GLYPH_COLOR[x.kind] }} role="status"
                        data-testid="job-exit-card" data-job-id={job.job_id} data-kind={x.kind}>
                        <div className="tc-update__head">
                            <span className="tc-update__kicker">JOB</span>
                            <span className="tc-update__name tc-rail-chip__name--mono">{name}</span>
                            <span className="tc-update__status">{x.word}</span>
                            <span className="tc-update__time">{fmtTime(at)}</span>
                            <Button type="text" size="small" icon={<CloseOutlined />}
                                aria-label="Dismiss update" onClick={() => dismiss(job.job_id)} />
                        </div>
                        <div className="tc-update__body">
                            {x.detail}
                            {tail != null && (
                                <pre className="tc-job-popover__tail" style={{ marginTop: 6 }}>{tail}</pre>
                            )}
                        </div>
                        <div className="tc-update__actions">
                            <Button size="small" icon={<VerticalAlignTopOutlined />}
                                disabled={job.started_at == null}
                                onClick={() => onJumpToJobStart?.(job.started_at!)}>
                                Jump to the turn that started it
                            </Button>
                            {tail == null && job.state === 'exited' && (
                                <Button size="small" onClick={() => showTail(job.job_id)}>Show tail</Button>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};

export default JobExitFeed;

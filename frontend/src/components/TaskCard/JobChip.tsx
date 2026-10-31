/**
 * JobChip — one running managed job in the TaskRail, with a popover
 * carrying the journal tail and the two actions a person needs: stop it,
 * and find where in the thread it was started.
 *
 * Distinguished from task chips by colour (teal, the job palette of
 * jobStatusGlyph) and a monospace label, so the two kinds of work read
 * differently in the same strip.  Elapsed time ticks from ``started_at``
 * on the client clock rather than from the poll's ``runtime_s``, so it
 * does not jump by the poll interval.
 *
 * Kill is a human stop of a process the model may be job_wait-ing on; the
 * model then sees ``reason=exit`` with a signal, as for a max_runtime stop.
 */
import React, { useEffect, useState } from 'react';
import { Button, Popconfirm, Popover, Tooltip, message } from 'antd';
import { getJob, killJob, type JobDetail, type JobRecord } from '../../services/jobsApi';
import { JOB_GLYPH_COLOR } from './jobStatusGlyph';
import { formatElapsed } from './jobRail';

interface Props {
    job: JobRecord;
    projectId: string;
    /** Called after a kill so the owner re-reads its list. */
    onChanged?: () => void;
    onJumpToJobStart?: (startedAtS: number) => void;
}

function useNowS(active: boolean): number {
    const [now, setNow] = useState(() => Date.now() / 1000);
    useEffect(() => {
        if (!active) return;
        const t = setInterval(() => setNow(Date.now() / 1000), 1000);
        return () => clearInterval(t);
    }, [active]);
    return now;
}

export const JobChip: React.FC<Props> = ({ job, projectId, onChanged, onJumpToJobStart }) => {
    const [open, setOpen] = useState(false);
    const [detail, setDetail] = useState<JobDetail | null>(null);
    const [killing, setKilling] = useState(false);
    const nowS = useNowS(true);
    const elapsed = formatElapsed(job.started_at != null ? nowS - job.started_at : job.runtime_s);
    const name = job.label || job.command || job.job_id;

    // Tail while the popover is open: on open, then every 2 s.
    useEffect(() => {
        if (!open || !projectId) return;
        let cancelled = false;
        const load = () => getJob(projectId, job.job_id, 30)
            .then(d => { if (!cancelled) setDetail(d); })
            .catch(() => { /* keep the last tail */ });
        load();
        const t = setInterval(load, 2000);
        return () => { cancelled = true; clearInterval(t); };
    }, [open, projectId, job.job_id]);

    const kill = async () => {
        setKilling(true);
        try {
            await killJob(projectId, job.job_id);
            message.success(`Stopped ${name}`);
            setOpen(false);
            onChanged?.();
        } catch (e: any) {
            message.error(e?.message || 'Kill failed');
        } finally {
            setKilling(false);
        }
    };

    const content = (
        <div style={{ width: 520, fontSize: 12 }} data-testid="job-popover">
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 6 }}>
                <strong>{name}</strong>
                <span style={{ opacity: .7 }}>running · {elapsed}{job.pid ? ` · pid ${job.pid}` : ''}</span>
                <code style={{ marginLeft: 'auto', opacity: .6, fontSize: 11 }}>{job.job_id}</code>
            </div>
            <pre className="tc-job-popover__meta">
                {`$ ${job.command ?? ''}`}
                {job.cwd ? `\ncwd  ${job.cwd}` : ''}
                {detail?.declared_outputs?.length ? `\noutputs  ${detail.declared_outputs.join('  ·  ')}` : ''}
                {detail?.max_runtime_s ? `\nmax runtime  ${formatElapsed(detail.max_runtime_s)}` : ''}
                {detail?.keep_awake ? `  ·  keep_awake ${detail.keep_awake}` : ''}
            </pre>
            <pre className="tc-job-popover__tail" data-testid="job-tail">
                {detail ? (detail.tail || '(no output yet)') : 'loading…'}
            </pre>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
                <Button size="small" disabled={job.started_at == null}
                    onClick={() => { setOpen(false); onJumpToJobStart?.(job.started_at!); }}>
                    Jump to the turn that started it
                </Button>
                <Button size="small" onClick={() => {
                    navigator.clipboard?.writeText(job.job_id).then(() => message.success('Job id copied'));
                }}>Copy job id</Button>
                <Popconfirm title={`Stop ${name}? TERM, then KILL after 10 s.`} okText="Stop" okButtonProps={{ danger: true }}
                    onConfirm={kill}>
                    <Button size="small" danger loading={killing} style={{ marginLeft: 'auto' }} data-testid="job-kill">
                        Kill
                    </Button>
                </Popconfirm>
            </div>
        </div>
    );

    return (
        <Popover content={content} trigger="click" open={open} onOpenChange={setOpen} placement="bottomLeft">
            <Tooltip title={`${job.command ?? name} — running ${elapsed}; click for the tail`}>
                <button type="button"
                    className="tc-rail-chip tc-rail-chip--job"
                    style={{ ['--tc-chip' as any]: JOB_GLYPH_COLOR.running }}
                    data-testid="job-chip"
                    data-job-id={job.job_id}>
                    <span className="tc-rail-chip__dot tc-rail-chip__dot--live" />
                    <span className="tc-rail-chip__name tc-rail-chip__name--mono">{name}</span>
                    <span className="tc-rail-chip__status">running</span>
                    <span className="tc-rail-chip__progress">{elapsed}</span>
                </button>
            </Tooltip>
        </Popover>
    );
};

export default JobChip;

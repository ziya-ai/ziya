/**
 * TaskRail — sticky strip under the chat header: the tasks of THIS
 * conversation that are live or waiting on the user, one chip each.
 *
 * The inline tile stays where it was launched; the chip is the tile's
 * second home while the run is in flight, and clicking it scrolls the
 * thread back to the tile.  Nothing finished is shown here — a done or
 * failed run is announced once by TaskUpdateFeed instead, so the rail
 * stays a reading of "what is happening now" and disappears entirely
 * when the answer is nothing.  See taskSurfacing.ts for the policy.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Tooltip } from 'antd';
import { DownOutlined, UpOutlined } from '@ant-design/icons';
import type { TaskBinding } from '../../types/task_binding';
import type { RunStatus } from '../../types/task_run';
import { taskCardApi } from '../../services/taskCardApi';
import {
    RUN_STATUS_ANIMATES, RUN_STATUS_FG, RUN_STATUS_HINT, RUN_STATUS_LABEL,
} from './runStatusVocabulary';
import { cardName, railEntries, scrollToTile } from './taskSurfacing';
import {
    ChipProgress, TASK_PROGRESS_EVENT, TaskProgressDetail,
    formatChipProgress, latestProgress,
} from './taskProgress';
import './task-surfacing.css';

interface Props {
    bindings: ReadonlyArray<TaskBinding>;
    projectId: string;
}

const COLLAPSED_KEY = 'ziya.taskRail.collapsed';

export const TaskRail: React.FC<Props> = ({ bindings, projectId }) => {
    const entries = useMemo(() => railEntries(bindings), [bindings]);
    const [names, setNames] = useState<Record<string, string>>({});
    const [progress, setProgress] = useState<Record<string, ChipProgress>>({});
    const [collapsed, setCollapsed] = useState<boolean>(() => {
        try { return localStorage.getItem(COLLAPSED_KEY) === '1'; } catch { return false; }
    });

    // One name lookup per distinct card; the binding carries only the id.
    useEffect(() => {
        let cancelled = false;
        for (const e of entries) {
            const pid = e.binding.project_id ?? projectId;
            const cid = e.binding.card_id;
            if (!pid || !cid || names[cid]) continue;
            cardName(pid, cid, taskCardApi.get).then(n => {
                if (!cancelled) setNames(prev => (prev[cid] ? prev : { ...prev, [cid]: n }));
            });
        }
        return () => { cancelled = true; };
    }, [entries, projectId, names]);

    // Progress readings published by the tiles (see taskProgress.ts).
    useEffect(() => {
        const handler = (ev: Event) => {
            const d = (ev as CustomEvent<TaskProgressDetail>).detail;
            if (!d?.bindingId) return;
            setProgress(prev => {
                const next = { ...prev };
                if (d.progress) next[d.bindingId] = d.progress; else delete next[d.bindingId];
                return next;
            });
        };
        window.addEventListener(TASK_PROGRESS_EVENT, handler);
        return () => window.removeEventListener(TASK_PROGRESS_EVENT, handler);
    }, []);

    if (entries.length === 0) return null;

    const attention = entries.filter(e => e.kind === 'attention').length;
    const toggle = () => {
        const next = !collapsed;
        setCollapsed(next);
        try { localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0'); } catch { /* advisory */ }
    };

    return (
        <div className={`tc-rail-strip${collapsed ? ' tc-rail-strip--collapsed' : ''}`}
            role="region" aria-label="Tasks in this conversation" data-testid="task-rail">
            <span className="tc-rail-strip__label">
                TASKS · {entries.length}
                {attention > 0 && (
                    <span className="tc-rail-strip__attn"> · {attention} waiting on you</span>
                )}
            </span>
            {!collapsed && entries.map(({ binding, kind, status }) => {
                const s = status as RunStatus;
                const color = RUN_STATUS_FG[s] ?? RUN_STATUS_FG.queued;
                const label = RUN_STATUS_LABEL[s] ?? status;
                const name = names[binding.card_id] ?? 'Task card';
                const p = progress[binding.id] ?? latestProgress(binding.id);
                const ptext = formatChipProgress(p);
                return (
                    <Tooltip key={binding.id}
                        title={`${RUN_STATUS_HINT[s] ?? status}${p?.note ? ` · ${p.note}` : ''}`
                            + ' — click to jump to the task'}>
                        <button type="button"
                            className={`tc-rail-chip tc-rail-chip--${kind}`}
                            style={{ ['--tc-chip' as any]: color }}
                            onClick={() => scrollToTile(binding.id)}
                            data-binding-id={binding.id}
                            data-status={status}>
                            <span className={
                                `tc-rail-chip__dot${RUN_STATUS_ANIMATES[s] ? ' tc-rail-chip__dot--live' : ''}`} />
                            <span className="tc-rail-chip__name">{name}</span>
                            <span className="tc-rail-chip__status">{label}</span>
                            {ptext && (
                                <span className="tc-rail-chip__progress">{ptext}</span>
                            )}
                            {(binding.attempt ?? 1) > 1 && (
                                <span className="tc-rail-chip__attempt">#{binding.attempt}</span>
                            )}
                        </button>
                    </Tooltip>
                );
            })}
            <button type="button" className="tc-rail-strip__toggle" onClick={toggle}
                aria-label={collapsed ? 'Show task chips' : 'Collapse task rail'}>
                {collapsed ? <DownOutlined /> : <UpOutlined />}
            </button>
        </div>
    );
};

export default TaskRail;

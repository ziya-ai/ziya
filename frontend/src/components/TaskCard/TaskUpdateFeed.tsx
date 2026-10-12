/**
 * TaskUpdateFeed — "the tile comes to you".
 *
 * When a task bound to this conversation changes state while the user is
 * here (finishes, fails, stops on a fault, or asks a question), an update
 * card is posted at the BOTTOM of the thread, next to the composer, with
 * a link back to the original tile.  The thread stays chronological; the
 * task speaks up when it matters, where the user is looking.
 *
 * Not persisted and not a chat message: the model already receives a
 * synthetic status line for every bound run (app/server.py
 * _inject_task_results), so nothing here needs to reach it.  The feed is
 * a reading aid for the person.  Cleared on conversation switch.
 *
 * Source of truth for transitions is TASK_RUN_STATUS_EVENT, announced by
 * useTaskRunStream from the tile that observed the change.  Policy —
 * which transitions post, which withdraw, and the "tile is already at
 * the bottom" suppression — lives in taskSurfacing.ts.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Button } from 'antd';
import { CloseOutlined, VerticalAlignTopOutlined } from '@ant-design/icons';
import type { TaskBinding } from '../../types/task_binding';
import type { RunStatus } from '../../types/task_run';
import { taskCardApi } from '../../services/taskCardApi';
import {
    TASK_RUN_STATUS_EVENT, TaskRunStatusChangeDetail,
} from '../../hooks/taskRunEvents';
import { RUN_STATUS_FG, RUN_STATUS_HINT, RUN_STATUS_LABEL } from './runStatusVocabulary';
import { applyTransition, cardName, scrollToTile, TaskUpdate } from './taskSurfacing';
import './task-surfacing.css';

interface Props {
    conversationId: string;
    projectId: string;
    /** Every non-superseded binding of this conversation. */
    bindings: ReadonlyArray<TaskBinding>;
    /** Binding ids whose tile already renders at the thread's bottom. */
    atBottomIds: ReadonlySet<string>;
}

const fmtTime = (ms: number) =>
    new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export const TaskUpdateFeed: React.FC<Props> = ({
    conversationId, projectId, bindings, atBottomIds,
}) => {
    const [feed, setFeed] = useState<TaskUpdate[]>([]);
    const [names, setNames] = useState<Record<string, string>>({});

    // A switch to another conversation starts with an empty feed; the
    // rail carries any standing state across.
    useEffect(() => { setFeed([]); }, [conversationId]);

    useEffect(() => {
        const handler = (ev: Event) => {
            const detail = (ev as CustomEvent<TaskRunStatusChangeDetail>).detail;
            if (!detail?.runId) return;
            const binding = bindings.find(b => b.run_id === detail.runId);
            setFeed(prev => applyTransition(prev, detail, binding, atBottomIds));
        };
        window.addEventListener(TASK_RUN_STATUS_EVENT, handler);
        return () => window.removeEventListener(TASK_RUN_STATUS_EVENT, handler);
    }, [bindings, atBottomIds]);

    useEffect(() => {
        let cancelled = false;
        for (const u of feed) {
            const b = bindings.find(x => x.id === u.bindingId);
            if (!b || names[b.card_id]) continue;
            cardName(b.project_id ?? projectId, b.card_id, taskCardApi.get).then(n => {
                if (!cancelled) setNames(prev => (prev[b.card_id] ? prev : { ...prev, [b.card_id]: n }));
            });
        }
        return () => { cancelled = true; };
    }, [feed, bindings, projectId, names]);

    const dismiss = useCallback((runId: string) =>
        setFeed(prev => prev.filter(u => u.runId !== runId)), []);

    if (feed.length === 0) return null;

    return (
        <div className="tc-update-feed" data-testid="task-update-feed">
            {feed.map(u => {
                const b = bindings.find(x => x.id === u.bindingId);
                const s = u.status as RunStatus;
                const color = RUN_STATUS_FG[s] ?? RUN_STATUS_FG.queued;
                const name = b ? (names[b.card_id] ?? 'Task card') : 'Task card';
                return (
                    <div key={u.runId} className={`tc-update tc-update--${u.status}`}
                        style={{ ['--tc-chip' as any]: color }} role="status"
                        data-run-id={u.runId} data-status={u.status}>
                        <div className="tc-update__head">
                            <span className="tc-update__kicker">TASK UPDATE</span>
                            <span className="tc-update__name">{name}</span>
                            <span className="tc-update__status">{RUN_STATUS_LABEL[s] ?? u.status}</span>
                            <span className="tc-update__time">{fmtTime(u.at)}</span>
                            <Button type="text" size="small" icon={<CloseOutlined />}
                                aria-label="Dismiss update" onClick={() => dismiss(u.runId)} />
                        </div>
                        <div className="tc-update__body">
                            {RUN_STATUS_HINT[s] ?? u.status}
                            {u.previous && <span className="tc-update__prev"> (was {u.previous})</span>}
                        </div>
                        <div className="tc-update__actions">
                            <Button size="small" icon={<VerticalAlignTopOutlined />}
                                onClick={() => scrollToTile(u.bindingId)}>
                                Go to task
                            </Button>
                        </div>
                    </div>
                );
            })}
        </div>
    );
};

export default TaskUpdateFeed;

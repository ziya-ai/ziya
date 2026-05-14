/**
 * TaskCardInlineTile — renders at a binding's anchor point in the chat.
 *
 * Three visual states:
 * - Live (running): pulsing border, spinner, cancel button
 * - Complete (done/failed/cancelled): summary with metrics
 * - Receipt (collapsed): one-liner, click to expand
 *
 * Polls run status while active; stops on terminal state.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Spin, Tag, Tooltip } from 'antd';
import {
  CaretRightOutlined, CaretDownOutlined, StopOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined,
  ClockCircleOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useProject } from '../../context/ProjectContext';
import type { TaskBinding } from '../../types/task_binding';
import type { TaskRun, RunStatus, IterationsResponse } from '../../types/task_run';
import type { TaskCard, Block, ArtifactPart } from '../../types/task_card';
import { cancelTaskRun, listIterations } from '../../services/taskRunApi';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { taskCardApi } from '../../services/taskCardApi';
import './task-card-inline-tile.css';

interface Props {
  binding: TaskBinding;
  /**
   * When true, render nothing once the run reaches a terminal state.
   * Used at the tail-of-chat fallback render site so finished
   * unanchored runs don't linger below the last message.
   */
  hideWhenTerminal?: boolean;
}

const STATUS_COLORS: Record<RunStatus, string> = {
  queued: '#7d8590',
  running: '#1f6feb',
  done: '#3fb950',
  failed: '#f85149',
  cancelled: '#d29922',
};

const STATUS_ICONS: Record<RunStatus, React.ReactNode> = {
  queued: <ClockCircleOutlined />,
  running: <ThunderboltOutlined />,
  done: <CheckCircleOutlined />,
  failed: <CloseCircleOutlined />,
  cancelled: <ExclamationCircleOutlined />,
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

/** Summaries longer than this get an expand/collapse affordance. */
const SUMMARY_COLLAPSE_THRESHOLD = 280;

/**
 * Render the summary.  Short summaries are shown inline; long ones are
 * collapsed behind a <details> element so the tile stays compact.
 */
const ArtifactSummary: React.FC<{ summary: string }> = ({ summary }) => {
  if (summary.length <= SUMMARY_COLLAPSE_THRESHOLD) {
    return <div className="tc-tile__summary">{summary}</div>;
  }
  const preview = summary.slice(0, SUMMARY_COLLAPSE_THRESHOLD).trimEnd() + '…';
  return (
    <details className="tc-tile__summary-expandable">
      <summary className="tc-tile__summary-preview">{preview}</summary>
      <div className="tc-tile__summary-full">{summary}</div>
    </details>
  );
};

/**
 * Render one typed output part from a task's artifact.  The design
 * doc (§Artifacts) defines three part types: text, file, data.  No
 * block type populates outputs today (Task leaves them empty; Repeat
 * and Parallel aggregate from children who likewise leave them empty),
 * but rendering is wired now so the path is exercised once leaves
 * start producing them.
 */
const OutputPart: React.FC<{ part: ArtifactPart; idx: number }> = ({ part, idx }) => {
  if (part.part_type === 'text' && part.text) {
    return <div className="tc-tile__output-text">{part.text}</div>;
  }
  if (part.part_type === 'file' && part.file_uri) {
    return (
      <div className="tc-tile__output-file">
        📎 <a href={part.file_uri} target="_blank" rel="noreferrer">
          {part.file_uri.split('/').pop() || part.file_uri}
        </a>
        {part.media_type && <span className="tc-tile__output-meta"> · {part.media_type}</span>}
      </div>
    );
  }
  if (part.part_type === 'data' && part.data) {
    return (
      <details className="tc-tile__output-data">
        <summary>data (part {idx + 1})</summary>
        <pre>{JSON.stringify(part.data, null, 2)}</pre>
      </details>
    );
  }
  return null;
};

/** Walk a block tree and return the first leaf Task's instructions. */
function findPrimaryTaskInstructions(block: Block | undefined | null): string | null {
  if (!block) return null;
  if (block.block_type === 'task') return block.instructions?.trim() || null;
  for (const child of block.body ?? []) {
    const found = findPrimaryTaskInstructions(child);
    if (found) return found;
  }
  return null;
}

export const TaskCardInlineTile: React.FC<Props> = ({ binding, hideWhenTerminal = false }) => {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? '';

  // Live-streamed run state.  Hook handles initial REST fetch, WS
  // subscription, and terminal refetch for the final artifact.
  const { run, error: streamError, refresh } = useTaskRunStream(
    projectId, binding.run_id,
  );
  const [card, setCard] = useState<TaskCard | null>(null);
  const [iterations, setIterations] = useState<IterationsResponse['items']>([]);
  const [expanded, setExpanded] = useState(true);
  const [cancelError, setCancelError] = useState<string | null>(null);

  // Fetch the card once — it's immutable from the tile's POV.
  useEffect(() => {
    if (!projectId || !binding.card_id) return;
    let cancelled = false;
    taskCardApi.get(projectId, binding.card_id)
      .then(c => { if (!cancelled) setCard(c); })
      .catch(() => { /* non-fatal — title falls back to "Task Run" */ });
    return () => { cancelled = true; };
  }, [projectId, binding.card_id]);

  const isTerminal = run != null && ['done', 'failed', 'cancelled'].includes(run.status);
  const isRunning = run != null && ['queued', 'running'].includes(run.status);

  // Auto-collapse after terminal (8s reveal)
  useEffect(() => {
    if (isTerminal && expanded) {
      const timer = setTimeout(() => setExpanded(false), 8000);
      return () => clearTimeout(timer);
    }
  }, [isTerminal]);

  // Fetch per-iteration artifacts when expanded.  Refetch when iteration
  // count grows (live updates) and once more at terminal state.
  const iterTotal = useMemo(() => {
    if (!run) return 0;
    let t = 0;
    for (const s of Object.values(run.block_states)) t += s.iteration_summaries.length;
    return t;
  }, [run?.updated_at]);
  useEffect(() => {
    if (!projectId || !expanded || !run || iterTotal === 0) return;
    let cancelled = false;
    listIterations(projectId, run.id, { include_artifact: true, limit: 100 })
      .then(resp => { if (!cancelled) setIterations(resp.items); })
      .catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [projectId, run?.id, expanded, iterTotal, run?.status]);

  const handleCancel = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!projectId || !run) return;
    try {
      await cancelTaskRun(projectId, run.id);
      // Hook will observe the run_completed event and refetch;
      // prompt a refresh in case the WS is slow to deliver.
      refresh();
    } catch (e) {
      setCancelError(String(e));
    }
  }, [projectId, run, refresh]);

  const toggleExpand = useCallback(() => setExpanded(v => !v), []);

  // Iteration counts from block_states
  const iterCounts = useMemo(() => {
    if (!run) return null;
    let passed = 0, failed = 0, total = 0;
    for (const state of Object.values(run.block_states)) {
      for (const s of state.iteration_summaries) {
        total++;
        if (s.status === 'passed') passed++;
        if (s.status === 'failed') failed++;
      }
    }
    return total > 0 ? { passed, failed, total } : null;
  }, [run?.updated_at]);

  // At-tail fallback: once the run is terminal, render nothing so a
  // finished ghost tile doesn't linger below the last message.
  if (hideWhenTerminal && run && ['done', 'failed', 'cancelled'].includes(run.status)) {
    return null;
  }

  if (streamError && !run) {
    return (
      <div className="tc-tile tc-tile--error">
        <span>⚠️ Task binding failed to load</span>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="tc-tile tc-tile--loading">
        <Spin size="small" />
        <span>Loading task…</span>
      </div>
    );
  }

  const statusColor = STATUS_COLORS[run.status];
  const title = card?.name || 'Task Run';
  const instructions = findPrimaryTaskInstructions(card?.root);

  // Collapsed receipt view
  if (!expanded) {
    return (
      <div
        className={`tc-tile tc-tile--receipt tc-tile--${run.status}`}
        onClick={toggleExpand}
        title="Click to expand"
      >
        <CaretRightOutlined className="tc-tile__chevron" />
        <span className="tc-tile__status-icon" style={{ color: statusColor }}>
          {STATUS_ICONS[run.status]}
        </span>
        <span className="tc-tile__text">
          {title}
          {run.artifact?.summary
            ? ` — ${run.artifact.summary.slice(0, 80)}${run.artifact.summary.length > 80 ? '…' : ''}`
            : ` (${run.status})`}
        </span>
        {run.artifact?.duration_ms ? (
          <span className="tc-tile__meta">{formatDuration(run.artifact.duration_ms)}</span>
        ) : null}
      </div>
    );
  }

  // Expanded view
  return (
    <div className={`tc-tile tc-tile--expanded tc-tile--${run.status}`}>
      <div className="tc-tile__header" onClick={toggleExpand}>
        <CaretDownOutlined className="tc-tile__chevron" />
        <span className="tc-tile__status-icon" style={{ color: statusColor }}>
          {STATUS_ICONS[run.status]}
        </span>
        <span className="tc-tile__title">{title}</span>
        <Tag color={statusColor} style={{ marginLeft: 'auto', fontSize: 10 }}>
          {run.status}
        </Tag>
        {isRunning && (
          <Tooltip title="Cancel run">
            <button className="tc-tile__cancel" onClick={handleCancel}>
              <StopOutlined />
            </button>
          </Tooltip>
        )}
      </div>

      <div className="tc-tile__body">
        {card?.description && (
          <div className="tc-tile__description">{card.description}</div>
        )}

        {instructions && (
          <details className="tc-tile__instructions">
            <summary>Instructions</summary>
            <pre>{instructions}</pre>
          </details>
        )}

        {isRunning && (
          <div className="tc-tile__progress">
            <Spin size="small" />
            <span>
              {run.status === 'queued' ? 'Waiting to start…' : 'Executing…'}
              {iterCounts && ` (${iterCounts.passed + iterCounts.failed} iterations)`}
            </span>
          </div>
        )}

        {run.artifact && (
          <div className="tc-tile__artifact">
            {run.artifact.summary ? (
              <ArtifactSummary summary={run.artifact.summary} />
            ) : (
              // Surface the empty-artifact case.  Without this fallback,
              // a run that produced a result but no summary text looks
              // identical to a run that hasn't started yet.
              <div className="tc-tile__summary tc-tile__summary--empty">
                (No summary produced)
              </div>
            )}
            {run.artifact.decisions && run.artifact.decisions.length > 0 && (
              <ul className="tc-tile__decisions">
                {run.artifact.decisions.slice(0, 8).map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
                {run.artifact.decisions.length > 8 && (
                  <li className="tc-tile__decisions-more">
                    …{run.artifact.decisions.length - 8} more
                  </li>
                )}
              </ul>
            )}
            {run.artifact.outputs && run.artifact.outputs.length > 0 && (
              <div className="tc-tile__outputs">
                {run.artifact.outputs.map((p, i) => (
                  <OutputPart key={i} part={p} idx={i} />
                ))}
              </div>
            )}
            <div className="tc-tile__metrics">
              {run.artifact.duration_ms > 0 && <span>⏱ {formatDuration(run.artifact.duration_ms)}</span>}
              {run.artifact.tokens > 0 && <span>🔤 {run.artifact.tokens.toLocaleString()} tokens</span>}
              {run.artifact.tool_calls > 0 && <span>🔧 {run.artifact.tool_calls} tool calls</span>}
            </div>
          </div>
        )}

        {run.error && <div className="tc-tile__error-msg">{run.error}</div>}

        {iterCounts && iterCounts.total > 0 && (
          <div className="tc-tile__iterations">
            <span className="tc-tile__iter-passed">{iterCounts.passed} passed</span>
            {iterCounts.failed > 0 && (
              <span className="tc-tile__iter-failed">{iterCounts.failed} failed</span>
            )}
          </div>
        )}

        {iterations.length > 0 && (
          <details className="tc-tile__iter-list" open={isTerminal && iterations.length <= 5}>
            <summary>Iterations ({iterations.length})</summary>
            <ol className="tc-tile__iter-items">
              {iterations.map((it, idx) => (
                <li key={`${it.block_id}-${it.summary.index}-${idx}`}
                    className={`tc-tile__iter tc-tile__iter--${it.summary.status}`}>
                  <span className="tc-tile__iter-num">#{it.summary.index}</span>
                  <span className="tc-tile__iter-status">{it.summary.status}</span>
                  {it.artifact?.summary && (
                    <span className="tc-tile__iter-summary">{it.artifact.summary}</span>
                  )}
                  <span className="tc-tile__iter-dur">{formatDuration(it.summary.duration_ms)}</span>
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
    </div>
  );
};

export default TaskCardInlineTile;

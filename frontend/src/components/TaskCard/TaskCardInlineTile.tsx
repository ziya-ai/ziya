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
import { Button, Spin, Tag, Tooltip } from 'antd';
import {
  CaretRightOutlined, CaretDownOutlined, StopOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined,
  ClockCircleOutlined, ThunderboltOutlined, ReloadOutlined, EditOutlined,
} from '@ant-design/icons';
import { useProject } from '../../context/ProjectContext';
import type { TaskBinding } from '../../types/task_binding';
import type { TaskRun, RunStatus, IterationsResponse } from '../../types/task_run';
import type { TaskCard, Block, ArtifactPart } from '../../types/task_card';
import { cancelTaskRun, listIterations } from '../../services/taskRunApi';
import { createBinding, deleteBinding, launchStagedBinding } from '../../services/taskBindingApi';
import { TASK_BINDING_EVENT, TASK_CARD_OPEN_EVENT } from '../../hooks/useTaskBindings';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { taskCardApi } from '../../services/taskCardApi';
import { TaskRunInspector } from './TaskRunInspector';
import { MarkdownRenderer } from '../MarkdownRenderer';
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
 *
 * Summary text is interpreted as markdown — task outputs routinely
 * include code fences, lists, and inline formatting.  The bottom
 * (live) inspector already does this; this brings the persisted
 * artifact summary view to parity.
 */
const ArtifactSummary: React.FC<{ summary: string }> = ({ summary }) => {
  const body = (
    <MarkdownRenderer
      markdown={summary}
      enableCodeApply={false}
      isStreaming={false}
      isSubRender={true}
    />
  );
  if (summary.length <= SUMMARY_COLLAPSE_THRESHOLD) {
    return <div className="tc-tile__summary">{body}</div>;
  }
  // Preview stays plain text — it's a truncated teaser, not full content.
  const preview = summary.slice(0, SUMMARY_COLLAPSE_THRESHOLD).trimEnd() + '…';
  return (
    <details className="tc-tile__summary-expandable">
      <summary className="tc-tile__summary-preview">{preview}</summary>
      <div className="tc-tile__summary-full">{body}</div>
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

/**
 * Render a single wrapper block as a one-line plain-language summary.
 * Returns `null` for Task blocks (those carry the actual instructions
 * shown below the wrapper chain).
 */
function describeWrapper(block: Block): string | null {
  if (block.block_type === 'task') return null;

  if (block.block_type === 'repeat') {
    const mode = block.repeat_mode || 'count';
    const parallel = block.repeat_parallel ? ' in parallel' : '';
    if (mode === 'count') {
      const n = block.repeat_count ?? 1;
      return `Repeat ${n} time${n === 1 ? '' : 's'}${parallel}`;
    }
    if (mode === 'until') {
      const max = block.repeat_max ?? 1;
      const cond = (block.repeat_until || '').trim();
      return cond
        ? `Repeat until summary contains "${cond}" (max ${max})${parallel}`
        : `Repeat until first success (max ${max})${parallel}`;
    }
    if (mode === 'for_each') {
      const src = (block.repeat_for_each_source || '').trim();
      return src
        ? `For each item in: ${src.length > 60 ? src.slice(0, 60) + '…' : src}${parallel}`
        : `For each item${parallel}`;
    }
  }

  if (block.block_type === 'until') {
    const max = block.until_max ?? 5;
    const cond = (block.until_condition || '').trim();
    return cond
      ? `Loop until: ${cond} (max ${max})`
      : `Loop until first success (max ${max})`;
  }

  if (block.block_type === 'parallel') {
    return `Run all branches in parallel`;
  }

  if (block.block_type === 'schedule') {
    const mode = block.schedule_mode || 'interval';
    if (mode === 'interval') {
      const n = block.schedule_interval_value ?? 1;
      const u = block.schedule_interval_unit || 'hours';
      return `Schedule: every ${n} ${u}`;
    }
    if (mode === 'at') return `Schedule: once at ${block.schedule_at_iso || '?'}`;
    if (mode === 'daily_at') return `Schedule: daily at ${block.schedule_daily_at || '?'}`;
    if (mode === 'cron') return `Schedule: cron ${block.schedule_cron || '?'}`;
  }

  return null;
}

/**
 * Walk a block tree and return both the wrapper-condition chain and the
 * first leaf Task's instructions.  The chain is top-down (outermost
 * first) so users can read it like a sentence: "Repeat 100 times → For
 * each file → <task instructions>".
 */
function findInstructionsAndWrappers(
  block: Block | undefined | null,
): { wrappers: string[]; instructions: string | null } {
  if (!block) return { wrappers: [], instructions: null };
  const wrap = describeWrapper(block);
  if (block.block_type === 'task') {
    return { wrappers: [], instructions: block.instructions?.trim() || null };
  }
  for (const child of block.body ?? []) {
    const inner = findInstructionsAndWrappers(child);
    if (inner.instructions) {
      return {
        wrappers: wrap ? [wrap, ...inner.wrappers] : inner.wrappers,
        instructions: inner.instructions,
      };
    }
  }
  return { wrappers: wrap ? [wrap] : [], instructions: null };
}

export const TaskCardInlineTile: React.FC<Props> = ({ binding, hideWhenTerminal = false }) => {
  // Dispatch on staged vs launched.  React's rules-of-hooks forbid an
  // early return between hook calls, so we split into two sibling
  // components and render whichever the binding shape demands.  The
  // chosen component then owns its own hook order without conditions.
  if (!binding.run_id) {
    return <StagedCardTile binding={binding} />;
  }
  return <LaunchedCardTile binding={binding} hideWhenTerminal={hideWhenTerminal} />;
};

const LaunchedCardTile: React.FC<Props> = ({ binding, hideWhenTerminal = false }) => {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? '';

  // Live-streamed run state.  Hook handles initial REST fetch, WS
  // subscription, and terminal refetch for the final artifact.
  const { run, error: streamError, refresh, live, clearLive } = useTaskRunStream(
    projectId, binding.run_id ?? '',
  );
  const [card, setCard] = useState<TaskCard | null>(null);
  const [iterations, setIterations] = useState<IterationsResponse['items']>([]);
  const [expanded, setExpanded] = useState(true);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);

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

  /**
   * Re-launch the same card against the same anchor message.  The
   * server creates a new binding + run; the existing one is
   * preserved so the user can still see what happened.  The
   * task-binding-created event causes ``useTaskBindings`` to
   * re-fetch and the new tile renders alongside this one.
   */
  const handleRerun = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!projectId || !binding.card_id || rerunning) return;
    setRerunning(true);
    try {
      const resp = await createBinding(projectId, binding.chat_id, {
        card_id: binding.card_id,
        anchor_message_id: binding.anchor_message_id ?? null,
      });
      window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT, {
        detail: { bindingId: resp.binding.id, runId: resp.run.id },
      }));
    } catch (err) {
      // Surface as a soft error — keep the existing tile intact.
      console.error('Task rerun failed', err);
    } finally {
      setRerunning(false);
    }
  }, [projectId, binding.card_id, binding.chat_id, binding.anchor_message_id, rerunning]);

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
  const { wrappers, instructions } = findInstructionsAndWrappers(card?.root);

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
        <Tooltip title="Edit this card in the deck">
          <button
            className="tc-tile__edit"
            onClick={(e) => {
              e.stopPropagation();  // header onClick toggles expand
              window.dispatchEvent(new CustomEvent(TASK_CARD_OPEN_EVENT, {
                detail: { cardId: binding.card_id },
              }));
            }}
          >
            <EditOutlined />
          </button>
        </Tooltip>
        {isRunning && (
          <Tooltip title="Cancel run">
            <button className="tc-tile__cancel" onClick={handleCancel}>
              <StopOutlined />
            </button>
          </Tooltip>
        )}
        {isTerminal && (
          <Tooltip
            title={
              run.status === 'done'
                ? 'Rerun this task'
                : run.status === 'cancelled'
                  ? 'Restart cancelled task'
                  : 'Restart failed task'
            }
          >
            <button
              className={
                'tc-tile__rerun' +
                (run.status === 'done' ? '' : ' tc-tile__rerun--restart')
              }
              onClick={handleRerun}
              disabled={rerunning}
            >
              <ReloadOutlined />
              <span>{run.status === 'done' ? 'Rerun' : 'Restart'}</span>
            </button>
          </Tooltip>
        )}
      </div>

      <div className="tc-tile__body">
        {card?.description && (
          <div className="tc-tile__description">{card.description}</div>
        )}

        {(wrappers.length > 0 || instructions) && (
          <details className="tc-tile__instructions">
            <summary>Instructions</summary>
            {wrappers.length > 0 && (
              <ul className="tc-tile__wrappers">
                {wrappers.map((w, i) => (
                  <li key={i}>
                    <span className="tc-tile__wrapper-arrow">{i === 0 ? '▸' : '↳'}</span> {w}
                  </li>
                ))}
              </ul>
            )}
            {instructions && wrappers.length > 0 && (
              <div className="tc-tile__wrapper-divider">Task instructions:</div>
            )}
            <pre>{instructions}</pre>
          </details>
        )}

        {Object.keys(live.variables).length > 0 && (
          <div className="tc-tile__vars">
            <div className="tc-tile__vars-label">State variables</div>
            <ul className="tc-tile__vars-list">
              {Object.entries(live.variables).map(([k, v]) => (
                <li key={k} className="tc-tile__var">
                  <code className="tc-tile__var-name">{k}</code>
                  <span className="tc-tile__var-eq">=</span>
                  <code className="tc-tile__var-val">
                    {typeof v === 'string' ? v : JSON.stringify(v)}
                  </code>
                </li>
              ))}
            </ul>
          </div>
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
            <summary>
              Results ({iterations.length})
              <span className="tc-tile__iter-list-hint"> · persisted summaries</span>
            </summary>
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

        <TaskRunInspector
          live={live}
          onClear={clearLive}
          defaultOpen={isRunning}
          persistedIterations={iterations}
          runStatus={run.status}
        />
      </div>
    </div>
  );
};

const StagedCardTile: React.FC<{ binding: TaskBinding }> = ({ binding }) => {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? '';
  const [card, setCard] = useState<TaskCard | null>(null);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    taskCardApi.get(projectId, binding.card_id)
      .then(c => { if (!cancelled) setCard(c); })
      .catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [projectId, binding.card_id]);

  const handleRun = async () => {
    if (!projectId) return;
    setLaunching(true);
    setError(null);
    try {
      await launchStagedBinding(projectId, binding.chat_id, binding.id);
      window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT));
    } catch (e: any) {
      setError(String(e));
      setLaunching(false);
    }
  };

  const handleDiscard = async () => {
    if (!projectId) return;
    try {
      await deleteBinding(projectId, binding.chat_id, binding.id);
      window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT));
    } catch (e: any) {
      setError(String(e));
    }
  };

  const instructions = useMemo(() => {
    if (!card) return '';
    const root: any = card.root;
    return (root.instructions || root.body?.[0]?.instructions || '').trim();
  }, [card]);

  return (
    <div className="task-card-inline-tile staged">
      <div className="header">
        <span>🎯</span>
        <strong>{card?.name ?? 'Goal'}</strong>
        <Tag color="default">staged</Tag>
      </div>
      {instructions && (
        <details>
          <summary><strong>Instructions</strong></summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{instructions}</pre>
        </details>
      )}
      <div className="actions" style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <Button type="primary" loading={launching} onClick={handleRun}>
          Run
        </Button>
        <Button onClick={handleDiscard} disabled={launching}>
          Discard
        </Button>
      </div>
      {error && <div className="error" style={{ color: '#f85149', marginTop: 4 }}>{error}</div>}
    </div>
  );
};

export default TaskCardInlineTile;

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
  PauseOutlined, PlayCircleOutlined,
} from '@ant-design/icons';
import { useProject } from '../../context/ProjectContext';
import type { TaskBinding } from '../../types/task_binding';
import type { TaskRun, RunStatus, IterationsResponse } from '../../types/task_run';
import type { TaskCard, Block, Artifact } from '../../types/task_card';
import { cancelTaskRun, pauseTaskRun, resumeTaskRun, listIterations, getIterationArtifact } from '../../services/taskRunApi';
import { createBinding, deleteBinding, launchStagedBinding } from '../../services/taskBindingApi';
import { TASK_BINDING_EVENT, TASK_CARD_OPEN_EVENT } from '../../hooks/useTaskBindings';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { taskCardApi } from '../../services/taskCardApi';
import { TaskRunInspector } from './TaskRunInspector';
import { TaskRunMap } from './TaskRunMap';
import { BlockDetailPanel } from './BlockDetailPanel';
import { ArtifactViewer } from './ArtifactViewer';
import { findBlockById, resolveBlockStatus } from './runMapModel';
import FailureClusters from './FailureClusters';
import { analyzeFailures } from '../../utils/iterationClusters';
import { formatLastActivity } from './liveActivity';
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
  paused: '#8957e5',
  done: '#3fb950',
  failed: '#f85149',
  cancelled: '#d29922',
};

// Icon/text foreground variant. STATUS_COLORS.running (#1f6feb) is tuned as
// a *filled* Tag background (white text on top reads fine at ~4.6:1), but
// used directly as a foreground glyph color against the dark tile
// background (#303a46) it drops to ~2.5:1 contrast — barely readable in
// dark mode. Swap in a lighter accent for icon/text foreground use only.
const STATUS_ICON_COLORS: Record<RunStatus, string> = {
  ...STATUS_COLORS,
  running: '#58a6ff',
};

const STATUS_ICONS: Record<RunStatus, React.ReactNode> = {
  queued: <ClockCircleOutlined />,
  running: <ThunderboltOutlined />,
  paused: <PauseOutlined />,
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
* One metric in the run's summary strip: value on top, label beneath.
*
* Equal-width cells so runtime / tokens / tool calls read as a scannable
* row of figures rather than a sentence of inline spans.
 */
const MetricCell: React.FC<{ value: string; label: string }> = ({ value, label }) => (
 <div className="tc-tile__metric">
   <div className="tc-tile__metric-value">{value}</div>
   <div className="tc-tile__metric-label">{label}</div>
 </div>
);

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

/**
 * Choose the card definition to DISPLAY for a run.  Prefers the
 * snapshot captured at launch (run.card_snapshot) over the live card,
 * so editing the card afterward does not retroactively rewrite what a
 * completed run is shown to have executed.  The snapshot's block ids
 * also match this run's block_states (a card edit reassigns ids), so
 * driving the run map from it stays consistent.  Falls back to the
 * live card for runs created before snapshotting existed.
 */
export function resolveDisplayCard(
  run: TaskRun | null | undefined,
  liveCard: TaskCard | null,
): TaskCard | null {
  if (run?.card_snapshot) {
    return {
      ...(liveCard ?? {}),
      name: run.card_snapshot.name,
      description: run.card_snapshot.description,
      root: run.card_snapshot.root,
    } as TaskCard;
  }
  return liveCard;
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
  // Focus: which block (and optional loop iteration) the output region
  // shows detail for.  null = the whole-run artifact (default).  This
  // is the "uplevel": the run map navigates, the region below reflects
  // whatever is focused.
  const [focus, setFocus] = useState<{ blockId: string; index: number | null } | null>(null);
  const [iterArtifact, setIterArtifact] = useState<Artifact | null>(null);
  const [iterLoading, setIterLoading] = useState(false);
  const [iterError, setIterError] = useState<string | null>(null);

  // Fetch the card once — it's immutable from the tile's POV.
  useEffect(() => {
    if (!projectId || !binding.card_id) return;
    let cancelled = false;
    taskCardApi.get(projectId, binding.card_id)
      .then(c => { if (!cancelled) setCard(c); })
      .catch(() => { /* non-fatal — title falls back to "Task Run" */ });
    return () => { cancelled = true; };
  }, [projectId, binding.card_id]);

  // Prefer the launch-time snapshot over the live card so later card
  // edits don't retroactively rewrite this run's displayed definition.
  const displayCard = useMemo(
    () => resolveDisplayCard(run, card), [run?.card_snapshot, card],
  );

  // Focus toggle: clicking the focused element again clears focus back
  // to the whole run.  index=null focuses the block; index=N a loop
  // iteration of it.
  const onFocus = useCallback((blockId: string, index: number | null) => {
    setFocus(prev =>
      prev && prev.blockId === blockId && prev.index === index
        ? null
        : { blockId, index });
    setIterArtifact(null);
    setIterError(null);
  }, []);
  const clearFocus = useCallback(() => {
    setFocus(null);
    setIterArtifact(null);
    setIterError(null);
  }, []);

  // Fetch a focused loop iteration's artifact on demand.  Block-level
  // focus needs no fetch (config is in the card; output is in the run
  // snapshot / live text).
  useEffect(() => {
    if (!focus || focus.index == null || !run) return;
    let cancelled = false;
    setIterLoading(true);
    getIterationArtifact(projectId, run.id, focus.blockId, focus.index)
      .then(a => { if (!cancelled) setIterArtifact(a); })
      .catch(e => { if (!cancelled) setIterError(String(e)); })
      .finally(() => { if (!cancelled) setIterLoading(false); });
    return () => { cancelled = true; };
  }, [focus, projectId, run?.id]);

  const isTerminal = run != null && ['done', 'failed', 'cancelled'].includes(run.status);
  const isRunning = run != null && ['queued', 'running'].includes(run.status);
  const isPaused = run != null && run.status === 'paused';
  // "Pausing…" — flag set but the executor hasn't reached a boundary yet.
  const isPausing = isRunning && !!run?.pause_requested;

  // Live-progress surface: prefer the WS stream (freshest), fall back
  // to the persisted run fields for REST-only clients.  A 5s tick
  // keeps the "Ns ago" label moving while the run is live.
  const progressNote = live.progressNote ?? run?.progress_note ?? null;
  const lastActivityTs = live.lastActivityTs ?? run?.last_activity_at ?? null;
  const [, setActivityTick] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(() => setActivityTick(x => x + 1), 5000);
    return () => clearInterval(t);
  }, [isRunning]);
  const activity = (isRunning && lastActivityTs != null)
    ? formatLastActivity(lastActivityTs)
    : null;

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

  const handlePause = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!projectId || !run) return;
    try {
      await pauseTaskRun(projectId, run.id);
      refresh();
    } catch (err) {
      setCancelError(String(err));
    }
  }, [projectId, run, refresh]);

  const handleResume = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!projectId || !run) return;
    try {
      await resumeTaskRun(projectId, run.id);
      refresh();
    } catch (err) {
      setCancelError(String(err));
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

  // Failure-signature clustering ("10,000 runs, 4 error patterns").
  // analyzeFailures is pure over block_states; shouldCluster gates it.
  const clusterAnalysis = useMemo(
    () => (run ? analyzeFailures(run.block_states) : null),
    [run?.updated_at],
  );

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
  const statusIconColor = STATUS_ICON_COLORS[run.status];
  const title = displayCard?.name || 'Task Run';
  const { wrappers, instructions } = findInstructionsAndWrappers(displayCard?.root);

  // Collapsed receipt view
  if (!expanded) {
    return (
      <div
        className={`tc-tile tc-tile--receipt tc-tile--${run.status}`}
        onClick={toggleExpand}
        title="Click to expand"
      >
        <CaretRightOutlined className="tc-tile__chevron" />
        <span className="tc-tile__status-icon" style={{ color: statusIconColor }}>
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
        <span className="tc-tile__status-icon" style={{ color: statusIconColor }}>
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
          <Tooltip title={isPausing ? 'Pausing at next boundary…' : 'Pause at next boundary'}>
            <button
              className="tc-tile__pause"
              onClick={handlePause}
              disabled={isPausing}
            >
              <PauseOutlined />
            </button>
          </Tooltip>
        )}
        {isPaused && (
          <Tooltip title="Resume run">
            <button className="tc-tile__resume" onClick={handleResume}>
              <PlayCircleOutlined />
            </button>
          </Tooltip>
        )}
        {(isRunning || isPaused) && (
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
        {displayCard?.description && (
          <div className="tc-tile__description">{displayCard.description}</div>
        )}

        {displayCard && (
          <TaskRunMap
            projectId={projectId} card={displayCard} run={run} live={live}
            focusedId={focus?.blockId ?? null}
            focusedIndex={focus?.index ?? null}
            onFocus={onFocus}
          />
        )}

        {focus && displayCard && (() => {
          const fb = findBlockById(displayCard.root, focus.blockId);
          if (!fb) return null;
          return (
            <div className="tc-focus">
              <button className="tc-focus__crumb" onClick={clearFocus}>
                ‹ Whole run
              </button>
              <BlockDetailPanel
                block={fb}
                status={resolveBlockStatus(fb.id, live.blockStatuses, run)}
                blockState={run.block_states?.[fb.id]}
                liveText={live.text[fb.id]}
                projectId={projectId}
                runId={run.id}
                iterationIndex={focus.index}
                iterationArtifact={iterArtifact}
                iterationLoading={iterLoading}
                iterationError={iterError}
              />
            </div>
          );
        })()}

        {(isRunning || isPaused) && (
          <div className="tc-tile__progress">
            {isPaused ? <PauseOutlined style={{ color: '#8957e5' }} /> : <Spin size="small" />}
            <span>
              {isPaused
                ? 'Paused — resume to continue'
                : isPausing
                ? 'Pausing at next boundary…'
                : run.status === 'queued'
                ? 'Waiting to start…'
                : (progressNote || 'Executing…')}
              {iterCounts && ` (${iterCounts.passed + iterCounts.failed} iterations)`}
            </span>
            {activity && (
              <span
                style={{
                  marginLeft: 'auto',
                  fontSize: 11,
                  opacity: 0.75,
                  color: activity.stale ? '#d48806' : undefined,
                }}
                title="Time since the task's last tool call or output"
              >{activity.stale ? '⚠ ' : ''}{activity.label}</span>
            )}
          </div>
        )}

        {run.artifact && !focus && (
          <div className="tc-tile__artifact">
            <div className="tc-tile__artifact-label">Result</div>
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
                <div className="tc-tile__outputs-head">
                  OUTPUT ARTIFACTS
                  <span className="tc-tile__outputs-count">
                    {run.artifact.outputs.length}
                  </span>
                </div>
                <ArtifactViewer
                  parts={run.artifact.outputs}
                  projectId={projectId}
                  runId={run.id}
                />
              </div>
            )}
            {/* Metrics strip: equal-width cells with value-over-label so
                run cost reads at a glance instead of as a text run-on. */}
            <div className="tc-tile__metrics tc-tile__metrics--strip">
              <MetricCell value={formatDuration(run.artifact.duration_ms)} label="⏱ runtime" />
              <MetricCell value={run.artifact.tokens.toLocaleString()} label="🔤 tokens" />
              <MetricCell value={String(run.artifact.tool_calls)} label="🔧 tool calls" />
              {iterCounts && iterCounts.total > 0 && (
                <MetricCell
                  value={`${iterCounts.passed} / ${iterCounts.total}`}
                  label="🎯 iterations"
                />
              )}
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

        {clusterAnalysis?.shouldCluster && (
          <FailureClusters
            projectId={projectId}
            runId={run.id}
            analysis={clusterAnalysis}
          />
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
          runStatus={run.status as RunStatus}
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

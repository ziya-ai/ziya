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

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';import { Button, Spin, Tag, Tooltip } from 'antd';
import {
  CaretRightOutlined, CaretDownOutlined, StopOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined,
  ClockCircleOutlined, ThunderboltOutlined, ReloadOutlined, EditOutlined,
  PauseOutlined, PlayCircleOutlined, StepForwardOutlined, ApiOutlined,
} from '@ant-design/icons';
import { useProject } from '../../context/ProjectContext';
import type { TaskBinding } from '../../types/task_binding';
import type {
  TaskRun, RunStatus, IterationsResponse, ProgressNote,
} from '../../types/task_run';import type { TaskCard, Block, Artifact } from '../../types/task_card';
import { cancelTaskRun, pauseTaskRun, resumeTaskRun, stepTaskRun, resumeRunFromBlock, resumeRunFromIteration, listIterations, getIterationArtifact, getRunLineage } from '../../services/taskRunApi';
import type { IterationResumeMode, ResumeMode } from '../../services/taskRunApi';import { createBinding, deleteBinding, launchStagedBinding } from '../../services/taskBindingApi';
import { TASK_BINDING_EVENT, TASK_CARD_OPEN_EVENT } from '../../hooks/useTaskBindings';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { taskCardApi } from '../../services/taskCardApi';
import { TaskRunInspector } from './TaskRunInspector';
import { TaskRunMap } from './TaskRunMap';
import { BlockDetailPanel } from './BlockDetailPanel';
import { ArtifactViewer } from './ArtifactViewer';
import { blockLabel, findBlockById, resolveBlockStatus } from './runMapModel';
import { deriveRunControls, heldLabel } from './runControls';
import {
  attemptSummary, firstFailedBlock, isPartial, progressCounts, progressPhrase,
  provenance, resumeKindLabel, sideEffectSummary,
} from './partialOutcome';
import FailureClusters from './FailureClusters';
import { analyzeFailures } from '../../utils/iterationClusters';
import { formatLastActivity } from './liveActivity';
import { awaitsUser, decideAutoCollapse } from './autoCollapse';
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
  partial: '#d29922',
  failed: '#f85149',
  cancelled: '#d29922',
  // Violet, matching 'paused': both mean "stopped, not broken".  A red
  // or amber here would read as a verdict on the work, which is exactly
  // the misreading 'held' exists to prevent.
  held: '#8957e5',
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
  // A half-filled disc rather than another Ant glyph: partial's whole
  // point is "neither success nor failure", and every warning-shaped
  // icon in the set already reads as one or the other.
  partial: <span aria-hidden>◐</span>,
  failed: <CloseCircleOutlined />,
  cancelled: <ExclamationCircleOutlined />,
  // A plug, not a warning triangle: the fault is in the connection to
  // the outside world, not in the run.
  held: <ApiOutlined />,
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
 * The amber banner shown on a partial run.
 *
 * Answers, in order, the two things a user needs and could not get from
 * a flat red "Failed": how far it got, and whether it changed anything.
 * The side-effect line is deliberately hedged when no files were
 * declared — an undeclared write is invisible to us, so claiming
 * "nothing changed" would be worse than the status it replaces.
 */
const PartialBanner: React.FC<{
  run: TaskRun;
  failedBlockLabel: string | null;
}> = ({ run, failedBlockLabel }) => {
  const p = progressCounts(run);
  const hazard = sideEffectSummary(run);
  const failed = firstFailedBlock(run);
  // progressPhrase, not the raw stage count: a loop card's body blocks
  // never reach 'done' (their status is not persisted mid-iteration),
  // so a stage-only figure said "0 of 5 stages completed" directly
  // beside a dot strip reading "5 passed".
  const phrase = progressPhrase(p);
  return (
    <div className="tc-partial" role="status">
      <div className="tc-partial__head">
        Stopped after partial progress
        {phrase && ` — ${phrase}`}
      </div>
      <div className="tc-partial__body">
        {(p.completed > 0 || p.passedIterations > 0) && (
          <>Completed stages kept their results. </>
        )}
        {failedBlockLabel
          ? <>Stopped at <strong>{failedBlockLabel}</strong>.</>
          : <>The run did not reach the end of the card.</>}
        {failed?.error && (
          <div className="tc-partial__error">{failed.error}</div>
        )}
      </div>
      {hazard && (
        <div className="tc-partial__hazard">
          ⚠ <strong>This run may have changed your workspace.</strong> {hazard}
        </div>
      )}
    </div>
  );
};

/**
 * "Where the work came from" — shown only on a resumed attempt.
 *
 * This is the direct answer to the question the old two-tile behaviour
 * left open ("is prior state preserved? I think it is?").  It IS
 * preserved — the resume gate replays every completed block — and
 * stating the split makes that answerable by looking.
 */
/**
 * The run's progress narrative, oldest first.
 *
 * The live progress line is a single slot, overwritten on every update,
 * so the story of a long run was destroyed as it was told: a rich
 * model-authored note ("reviewed 12/30 diffs; grouping into 3 commits")
 * survived only until the next tool call, and a finished run had no
 * progress history at all.  This renders the durable trail the server
 * now keeps.
 *
 * Collapsed by default and capped in the preview: on a long run this is
 * reference material you open when you want it, not something that
 * should push the result off screen.
 */
const ProgressTrail: React.FC<{ notes: ProgressNote[] }> = ({ notes }) => {
  if (!notes.length) return null;
  const modelCount = notes.filter(n => n.source === 'model').length;
  return (
    <details className="tc-trail">
      <summary className="tc-trail__summary">
        Progress trail ({notes.length})
        {modelCount > 0 && (
          <span className="tc-trail__hint">
            {' '}· {modelCount} authored by the model
          </span>
        )}
      </summary>
      <ol className="tc-trail__list">
        {notes.map((n, i) => (
          <li
            key={`${n.at}-${i}`}
            className={
              'tc-trail__item'
              + (n.source === 'model' ? ' tc-trail__item--model' : '')
            }
          >
            <span className="tc-trail__at">
              {(() => {
                try {
                  const d = new Date(n.at * 1000);
                  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString();
                } catch { return ''; }
              })()}
            </span>
            <span className="tc-trail__note">{n.note}</span>
          </li>
        ))}
      </ol>
    </details>
  );
};

const ProvenanceBlock: React.FC<{ run: TaskRun }> = ({ run }) => {
  const p = provenance(run);
  if (!p) return null;
  return (
    <div className="tc-prov">
      <div className="tc-prov__head">Where the work came from</div>
      <ul className="tc-prov__list">
        {p.replayed > 0 && (
          <li>
            <strong>{p.replayed}</strong> stage{p.replayed === 1 ? '' : 's'}
            {' '}replayed from an earlier attempt — not re-executed
          </li>
        )}
        {p.executed > 0 && (
          <li>
            <strong>{p.executed}</strong> stage{p.executed === 1 ? '' : 's'}
            {' '}executed fresh in this attempt
          </li>
        )}
      </ul>
    </div>
  );
};

/**
 * The attempt rail: every run in this lineage, newest highlighted.
 *
 * Replaces the old behaviour of a second tile silently appearing beside
 * the first, which stated no relationship between them.  Rendered only
 * for a lineage of two or more — a single attempt has no history to
 * show and the rail would be pure noise.
 */
const AttemptRail: React.FC<{
  lineage: TaskRun[];
  currentRunId: string;
  onSelect: (runId: string) => void;
}> = ({ lineage, currentRunId, onSelect }) => {
  if (lineage.length < 2) return null;
  return (
    <div className="tc-rail">
      <div className="tc-rail__head">
        ATTEMPT HISTORY
        <span className="tc-rail__hint">
          {' '}· one card, one thread — nothing discarded
        </span>
      </div>
      {lineage.map(r => {
        const isCurrent = r.id === currentRunId;
        return (
          <button
            key={r.id}
            className={
              'tc-rail__row' + (isCurrent ? ' tc-rail__row--current' : '')
            }
            onClick={() => onSelect(r.id)}
            title={isCurrent ? 'Showing this attempt' : 'View this attempt'}
          >
            <span className="tc-rail__num">{r.attempt ?? 1}</span>
            <span
              className="tc-rail__icon"
              style={{ color: STATUS_ICON_COLORS[r.status] }}
            >
              {STATUS_ICONS[r.status]}
            </span>
            <span className="tc-rail__label">
              <span className="tc-rail__kind">{resumeKindLabel(r)}</span>
              {' '}{attemptSummary(r)}
            </span>
            {isCurrent && <span className="tc-rail__shown">shown</span>}
          </button>
        );
      })}
    </div>
  );
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

  /**
   * Which attempt in the lineage the body shows.  Defaults to the
   * binding's own run; the rail can switch it.  Held here rather than
   * derived so a user inspecting attempt 1 is not yanked to attempt 3
   * when a resume lands.
   */
  const [shownRunId, setShownRunId] = useState<string>(binding.run_id ?? '');
  useEffect(() => {
    setShownRunId(binding.run_id ?? '');
  }, [binding.run_id]);

  // Live-streamed run state.  Hook handles initial REST fetch, WS
  // subscription, and terminal refetch for the final artifact.
  const { run, error: streamError, refresh, live, clearLive } = useTaskRunStream(
    projectId, shownRunId,
  );
  /** Whole attempt lineage, oldest first.  Empty until fetched. */
  const [lineage, setLineage] = useState<TaskRun[]>([]);
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
  /** Block id whose resume-from request is in flight. */
  const [resumingBlockId, setResumingBlockId] = useState<string | null>(null);
  /** Iteration index whose mid-loop resume is in flight. */
  const [resumingIteration, setResumingIteration] = useState<number | null>(null);

  // Fetch the card once — it's immutable from the tile's POV.
  useEffect(() => {
    if (!projectId || !binding.card_id) return;
    let cancelled = false;
    taskCardApi.get(projectId, binding.card_id)
      .then(c => { if (!cancelled) setCard(c); })
      .catch(() => { /* non-fatal — title falls back to "Task Run" */ });
    return () => { cancelled = true; };
  }, [projectId, binding.card_id]);

  // Attempt lineage.  Keyed on the run's terminal status as well as its
  // id, because a resume launched from THIS tile adds an attempt that
  // the rail must pick up without a reload.
  //
  // Non-fatal on failure: the rail is additive, so an error degrades to
  // the single-attempt view rather than breaking the tile — the run
  // itself is unaffected either way.
  useEffect(() => {
    if (!projectId || !shownRunId) return;
    let cancelled = false;
    getRunLineage(projectId, shownRunId)
      .then(l => { if (!cancelled) setLineage(l); })
      .catch(() => { if (!cancelled) setLineage([]); });
    return () => { cancelled = true; };
  }, [projectId, shownRunId, run?.status]);

  /**
   * Switch the tile to another attempt in the lineage.
   *
   * Clears everything scoped to the attempt being LEFT.  Live buffers
   * especially: they accumulate per-block text and tool calls keyed by
   * block id, and the ids are shared across attempts (a resumed run
   * executes the source run's snapshot tree), so carrying them over
   * would attribute one attempt's output to another — the precise
   * confusion this whole change removes.
   */
  const selectAttempt = useCallback((runId: string) => {
    setShownRunId(runId);
    clearLive();
    setFocus(null);
    setIterations([]);
    setIterArtifact(null);
    setIterError(null);
  }, [clearLive]);

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

  // Control state is derived from pause_requested / step_budget rather
  // than from status: a stepped run's status blips paused → running →
  // paused per step, so status-keyed booleans made the Resume button
  // vanish mid-step.  See runControls.ts.
  const controls = deriveRunControls(run);
  const isTerminal = controls.isTerminal;
  const isLive = run != null && !isTerminal;
  /**
   * Whether the user's most recent request was a step.  Not derivable
   * from the run record — a spent credit leaves step_budget at 0 and
   * status at 'running', identical to a pause in flight — so it is
   * tracked here purely to word the progress line honestly.  Cleared
   * once the run actually holds again.
   */
  const [stepping, setStepping] = useState(false);
  useEffect(() => {
    if (controls.isAtBoundary || !controls.isHeld || isTerminal) {
      setStepping(false);
    }
  }, [controls.isAtBoundary, controls.isHeld, isTerminal]);

  // Live-progress surface.  Both sources carry a server-clock activity
  // timestamp, so pick whichever is genuinely newer instead of always
  // preferring the WS stream.  A nullish-coalescing preference consults
  // the run snapshot only while the live value has NEVER been set, so a
  // single WS event pins the note permanently and later, fresher REST
  // snapshots are ignored — which is what leaves the line showing a
  // tool call from minutes ago once WS delivery falls behind the poll.
  // A 5s tick keeps the "Ns ago" label moving while the run is live.
  const liveTs = live.lastActivityTs ?? null;
  const runTs = run?.last_activity_at ?? null;
  const preferRun = liveTs == null
    || (runTs != null && runTs > liveTs);
  const latestProgressNote = (preferRun
    ? (run?.progress_note ?? live.progressNote)
    : (live.progressNote ?? run?.progress_note)) ?? null;
  // B8a: a model-authored note ("reviewed 12/30 diffs; grouping into 3
  // commits") is semantically richer than a tool-derived one ("ran grep:
  // ...") and was previously overwritten by the very next tool call —
  // usually within a second or two — even though the model may still be
  // mid-phase.  Keep the latest model note as the sticky headline for as
  // long as this WS session has one; tool activity still moves the
  // "Ns ago" age label below, so staleness is still visible.  Only the
  // REST snapshot's undifferentiated ``progress_note`` is available
  // before a model note has ever streamed (e.g. right after attaching).
  const progressNote = live.modelProgressNote ?? latestProgressNote;
  const lastActivityTs = (preferRun ? (runTs ?? liveTs) : (liveTs ?? runTs));
  const [, setActivityTick] = useState(0);
  useEffect(() => {
    if (!isLive) return;
    const t = setInterval(() => setActivityTick(x => x + 1), 5000);
    return () => clearInterval(t);
  }, [isLive]);
  const activity = (isLive && lastActivityTs != null)
    ? formatLastActivity(lastActivityTs)
    : null;

  /**
   * Epoch ms of the last interaction anywhere inside this tile, or null
   * while untouched.  A ref rather than state: it is read only when the
   * collapse timer is (re)armed, so storing it in state would re-render
   * the whole tile on every click for no visible effect.
   *
   * ``collapseTick`` is the deliberate, cheap re-render that DOES rearm
   * the timer — bumped once per interaction so the effect below re-runs
   * without the ref itself being a dependency (refs don't trigger
   * effects, which is the trap that makes engagement tracking silently
   * not work).
   */
  const lastInteractionRef = useRef<number | null>(null);
  const [collapseTick, setCollapseTick] = useState(0);
  const noteInteraction = useCallback(() => {
    lastInteractionRef.current = Date.now();
    setCollapseTick(t => t + 1);
  }, []);
  /**
   * True while the tile's open state was reached by the user clicking
   * the chevron or the receipt, rather than by the tile rendering open.
   * Suppresses auto-collapse entirely — see rule 3 in autoCollapse.ts.
   *
   * A ref for the same reason as ``lastInteractionRef``: it is read only
   * when the timer is armed, and ``collapseTick`` (bumped by the same
   * toggle that writes this) is what re-runs the effect.
   */
  const manuallyExpandedRef = useRef<boolean>(false);

  // Auto-collapse after terminal.  Deferred while the user is engaged,
  // and suppressed outright for a run that is waiting on them — see
  // autoCollapse.ts for why the policy lives in a pure helper.
  useEffect(() => {
    const { arm, delayMs } = decideAutoCollapse(
      run, isTerminal, expanded, lastInteractionRef.current,
      Date.now(), manuallyExpandedRef.current,
    );
    if (!arm) return;
    const timer = setTimeout(() => setExpanded(false), delayMs);
    return () => clearTimeout(timer);
    // ``expanded`` is included so re-expanding a collapsed tile re-arms
    // the timer; without it a user who reopened a finished tile got no
    // further auto-collapse, which is a different bug in the other
    // direction.  ``collapseTick`` is what carries interaction through.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTerminal, expanded, collapseTick, run?.status]);
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
      setStepping(false);
      refresh();
    } catch (err) {
      setCancelError(String(err));
    }
  }, [projectId, run, refresh]);

  /**
   * Advance one block boundary and hold again.  Offered on a running
   * run too, not just a held one: the server sets the pause flag as
   * part of the step, so this is how you take control of a card that
   * is already in flight.
   */
  const handleStep = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!projectId || !run) return;
    setStepping(true);
    try {
      await stepTaskRun(projectId, run.id, 1);
      refresh();
    } catch (err) {
      // Leave `stepping` set only on success — a rejected step never
      // advanced anything, so claiming "advancing…" would be a lie.
      setStepping(false);
      setCancelError(String(err));
    }
  }, [projectId, run, refresh]);

  /**
  * Launch a NEW attempt that replays this run's completed blocks.
   *
  * ``retry`` re-executes ``blockId``; ``continue`` accepts its recorded
  * outcome and starts at the block after it.  Either way every earlier
  * block replays from record, so prior deck state is preserved — the
  * provenance block states the split so the user need not take that on
  * faith.
  *
  * The source run is left untouched as a record, and the new attempt
  * joins its lineage, so it appears on THIS tile's rail rather than as
  * an unexplained second tile.  The server also creates the binding
  * (reusing this run's anchor) so it survives a reload.
   */
  const handleResumeFrom = useCallback(async (
   blockId: string, mode: ResumeMode = 'retry',
  ) => {
    if (!projectId || !run || resumingBlockId) return;
    setResumingBlockId(blockId);
    setCancelError(null);
    try {
     const res = await resumeRunFromBlock(projectId, run.id, blockId, mode);
     // Switch this tile to the new attempt immediately: it is the
     // newest in the lineage, and leaving the tile on the old one would
     // make the click look like it did nothing.
     selectAttempt(res.run.id);
      if (res.binding) {
        window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT, {
          detail: { bindingId: res.binding.id, runId: res.run.id },
        }));
      } else {
        // Server launched the run but could not bind it (no source
        // chat).  Say so — the run IS executing, so silence would look
        // like the click did nothing.
        setCancelError(
          'Resumed run started, but it is not attached to this ' +
          'conversation and will not appear as a tile.',
        );
      }
    } catch (err) {
      setCancelError(String(err));
    } finally {
      setResumingBlockId(null);
    }
  }, [projectId, run, resumingBlockId, selectAttempt]);

  /**
   * Launch a new attempt from a point INSIDE a loop.
   *
   * Deliberately mirrors handleResumeFrom — same attempt switch, same
   * binding-event dispatch, same non-fatal treatment of a missing
   * binding — because the two are one user-facing idea at two
   * granularities, and divergence here would mean a mid-loop resume
   * behaved subtly differently from a block-level one for no reason the
   * user could see.
   *
   * The server refuses some requests (parallel loop, dropped predecessor
   * artifact) with a 422 whose detail names WHY; surfacing it verbatim is
   * the point, since each refusal has a different remedy.
   */
  const handleResumeIteration = useCallback(async (
    blockId: string, index: number, mode: IterationResumeMode,
  ) => {
    if (!projectId || !run || resumingIteration != null) return;
    setResumingIteration(index);
    setCancelError(null);
    try {
      const res = await resumeRunFromIteration(
        projectId, run.id, blockId, index, mode,
      );
      selectAttempt(res.run.id);
      if (res.binding) {
        window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT, {
          detail: { bindingId: res.binding.id, runId: res.run.id },
        }));
      } else {
        setCancelError(
          'Resumed run started, but it is not attached to this ' +
          'conversation and will not appear as a tile.',
        );
      }
    } catch (err) {
      setCancelError(String(err));
    } finally {
      setResumingIteration(null);
    }
  }, [projectId, run, resumingIteration, selectAttempt]);

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

  /**
   * Expand/collapse by hand — and count it as engagement.
   *
   * The engagement capture handlers live on the EXPANDED container, so
   * the collapsed receipt (whose only handler is this toggle) never
   * stamped the interaction ref.  Opening a receipt therefore re-armed
   * the collapse effect with lastInteractionAt === null, i.e. the
   * UNTOUCHED 8s delay, and the tile the user had just deliberately
   * opened folded shut again a few seconds later.  Deliberately opening
   * a tile is the strongest possible signal of intent to read it, so it
   * has to defer the collapse at least as much as a stray mousedown
   * inside one does.
   *
   * It does more than defer: an expand by hand PINS the tile open, and
   * only a manual collapse clears the pin.  Deferring alone still closed
   * the tile under a reader after the quiet period, and re-opening it
   * re-armed the same timer — so a tile the user kept opening kept
   * closing itself.  Written to the ref from ``!expanded`` rather than
   * inside the state updater, because an updater may run twice under
   * StrictMode and must stay free of side effects.
   */
  const toggleExpand = useCallback(() => {
    noteInteraction();
    manuallyExpandedRef.current = !expanded;
    setExpanded(v => !v);
  }, [noteInteraction, expanded]);

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
  // 'held' is deliberately EXCLUDED: it is terminal for the run object
  // but the work is unfinished and continuable, and the tile is the only
  // surface offering "resume from here".  Hiding it would silently strand
  // the run — the exact failure this status was introduced to fix.
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
        {/* A run that stopped without finishing must not read as done.
            The receipt carries no controls, so the only state waiting on
            the user would otherwise look inert.

            Two separate conditions, because they are genuinely different
            states and ``isHeld`` does NOT cover the first:
            deriveRunControls returns a spread of IDLE for a terminal
            run, so ``isHeld`` is false for status==='held' and only ever
            describes a PAUSED/STEPPING (non-terminal) run.  This guard
            was written for the infra-held case and, keyed on isHeld
            alone, never fired for it — the one status it was meant to
            protect.  A paused run can also reach the receipt if the user
            collapses it by hand, so both are kept. */}
        {awaitsUser(run) && (
          <span
            className="tc-tile__held"
            title={
              'Stopped on an infrastructure fault, not a failure of the '
              + 'work. Expand to resume from where it stopped.'
            }
          >
            held
          </span>
        )}
        {controls.isHeld && (
          <span className="tc-tile__held" title={heldLabel(controls, stepping)}>
            {controls.isAtBoundary ? 'paused' : 'pausing'}
            {controls.stepCredits > 0 ? ` +${controls.stepCredits}` : ''}
          </span>
        )}
        {run.artifact?.duration_ms ? (
          <span className="tc-tile__meta">{formatDuration(run.artifact.duration_ms)}</span>
        ) : null}
      </div>
    );
  }

  // Expanded view
  return (
    <div
      className={`tc-tile tc-tile--expanded tc-tile--${run.status}`}
      /* Capture-phase, on the container: every click, key and text
         selection inside the tile counts as engagement, so the collapse
         timer defers.  Capture rather than bubble because several inner
         controls call stopPropagation (the header's Edit button, the
         map's resume/continue, the dots) and a bubble listener would
         miss exactly the interactions that most clearly mean "I am
         using this".  onMouseDownCapture rather than onClickCapture so
         a drag-select of trace text — reading, not clicking — also
         registers. */
      onMouseDownCapture={noteInteraction}
      onKeyDownCapture={noteInteraction}>
      <div className="tc-tile__header" onClick={toggleExpand}>
        <CaretDownOutlined className="tc-tile__chevron" />
        <span className="tc-tile__status-icon" style={{ color: statusIconColor }}>
          {STATUS_ICONS[run.status]}
        </span>
        <span className="tc-tile__title">{title}</span>
        <Tag color={statusColor} style={{ marginLeft: 'auto', fontSize: 10 }}>
          {run.status}
        </Tag>
        {/* Attempt ordinal.  Present as soon as a lineage exists, so a
            user looking at attempt 3 is never left wondering whether
            attempts 1 and 2 still exist. */}
        {lineage.length > 1 && (
          <span className="tc-tile__attempt">
            attempt {run.attempt ?? 1} of {lineage.length}
          </span>
        )}
        {/* Pause chip.  The status Tag alone is misleading under step
            debugging — it reads "running" for the whole time the
            stepped block executes, so a user who is single-stepping
            sees no indication the run is still under their control.

            Worded "paused"/"pausing", not "held": the status Tag beside
            it already reads "held" for the infra-fault case, and one word
            meaning two unrelated states in the same header is worse than
            no chip.  ``controls.isHeld`` is only ever true for a
            NON-terminal (paused/stepping) run — see the receipt's note. */}
        {controls.isHeld && (
          <span className="tc-tile__held" title={heldLabel(controls, stepping)}>
            {controls.isAtBoundary ? 'paused' : 'pausing'}
            {controls.stepCredits > 0 ? ` +${controls.stepCredits}` : ''}
          </span>
        )}
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
        {controls.canPause && (
          <Tooltip title="Pause at the next block boundary">
            <button
              className="tc-tile__pause"
              onClick={handlePause}
            >
              <PauseOutlined />
              {/* Labelled, not icon-only: Step in particular is not
                  guessable from its glyph, and an unlabelled control
                  reads as decoration rather than an available action. */}
              <span>Pause</span>
            </button>
          </Tooltip>
        )}
        {controls.canStep && (
          <Tooltip
            title={
              'Advance one block, then hold. Granularity is a whole block — ' +
              'stepping past a Task runs all of its LLM iterations and tool ' +
              'calls. Click repeatedly to queue more steps.'
            }
          >
            <button
              className="tc-tile__step"
              onClick={handleStep}
              disabled={controls.isSettling}
            >
              <StepForwardOutlined />
              <span>Step</span>
            </button>
          </Tooltip>
        )}
        {controls.canResume && (
          <Tooltip title="Release the hold and run to completion">
            <button className="tc-tile__resume" onClick={handleResume}>
              <PlayCircleOutlined />
              <span>Resume</span>
            </button>
          </Tooltip>
        )}
        {controls.canCancel && (
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
                : run.status === 'held'
                  ? 'Restart — this run stopped on an infrastructure fault, not a failure of the work'
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

        {/* Banner first: "how far did it get / did it change anything"
            outranks the run map, because a user arriving at a stopped
            run needs the verdict before the detail. */}
        {isPartial(run) && (
          <PartialBanner
            run={run}
            failedBlockLabel={(() => {
              const fb = firstFailedBlock(run);
              if (!fb || !displayCard) return null;
              const b = findBlockById(displayCard.root, fb.block_id);
              return b ? blockLabel(b) : null;
            })()}
          />
        )}

        <ProvenanceBlock run={run} />

        <AttemptRail
          lineage={lineage} currentRunId={run.id} onSelect={selectAttempt}
        />

        {displayCard && (
          <TaskRunMap
            projectId={projectId} card={displayCard} run={run} live={live}
            focusedId={focus?.blockId ?? null}
            focusedIndex={focus?.index ?? null}
            onFocus={onFocus}
            onResumeFrom={
              controls.canResumeFromBlock ? handleResumeFrom : undefined
            }
            onContinueFrom={
              controls.canContinueFromBlock
                ? (blockId: string) => handleResumeFrom(blockId, 'continue')
                : undefined
            }
            resumingBlockId={resumingBlockId}
          />
        )}

        {/* Control-action errors.  Previously cancelError was set by
            every control handler and rendered NOWHERE, so a failed
            pause/step/resume was silently swallowed.  resume-from makes
            that untenable: its 404/409/422 each mean something specific
            and actionable. */}
        {cancelError && (
          <div className="tc-tile__error-msg" role="alert">
            {cancelError}
            <button className="tc-tile__error-dismiss"
                    onClick={() => setCancelError(null)}>dismiss</button>
          </div>
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
                run={run}
                blockState={run.block_states?.[fb.id]}
                liveText={live.text[fb.id]}
                projectId={projectId}
                runId={run.id}
                iterationIndex={focus.index}
                iterationArtifact={iterArtifact}
                iterationLoading={iterLoading}
                iterationError={iterError}
                // Gated on the same flags as the block-level buttons:
                // both call an endpoint that 409s on a live run and 422s
                // without a card_snapshot, so offering them otherwise
                // would only produce errors.
                onRetryIteration={
                  controls.canResumeFromBlock
                    ? (b, i) => handleResumeIteration(b, i, 'retry_iteration')
                    : undefined
                }
                onContinueIteration={
                  controls.canContinueFromBlock
                    ? (b, i) => handleResumeIteration(b, i, 'continue_iteration')
                    : undefined
                }
                resumingIteration={resumingIteration}
              />
            </div>
          );
        })()}

        {isLive && (
          <div className="tc-tile__progress">
            {controls.isAtBoundary
              ? <PauseOutlined style={{ color: '#8957e5' }} />
              : <Spin size="small" />}
            <span>
              {controls.isHeld
                ? heldLabel(controls, stepping)
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

        {/* The whole-run progress narrative.  Placed after the result and
            before the instructions: it explains HOW the run got to that
            result, so it reads as supporting evidence rather than as the
            headline. */}
        {run.progress_notes && run.progress_notes.length > 0 && (
          <ProgressTrail notes={run.progress_notes} />
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
          // isLive rather than the old isRunning: a held/stepping run is
          // exactly when the trace matters most, and status reads 'paused'
          // between steps, which the old predicate excluded.
          defaultOpen={isLive}
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

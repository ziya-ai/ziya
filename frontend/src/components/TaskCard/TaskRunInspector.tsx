/**
 * TaskRunInspector — collapsible drawer with three tabs of live
 * visibility into an in-flight (or recently completed) task run:
 *
 *   1. Live output      — accumulated streaming text per task block
 *   2. Tool calls       — recent tool invocations (name, preview, ts)
 *   3. Event timeline   — raw lifecycle + task_* events with timestamps
 *
 * State is driven by the ``live`` payload from ``useTaskRunStream``.
 * Live state is transient and resets on remount or runId change.
 */
 import React, { useEffect, useState } from 'react';
import type { LiveTaskState } from '../../hooks/useTaskRunStream';
import type { IterationsResponse, RunStatus } from '../../types/task_run';
 import { collapseEventRuns, bucketEventsByIteration, type DeltaRun, type DisplayEvent, type EventBucket, type RawEvent } from './eventLog';
 import { truncatePreview } from './previewText';
 import { TaskMarkdown } from './TaskMarkdown';
 import { stripTaskMetaTags } from './completionCheck';
 import { isRunOver } from './runControls';

interface Props {
  live: LiveTaskState;
  onClear?: () => void;
  /** Optional default-open. */
  defaultOpen?: boolean;
  persistedIterations?: IterationsResponse['items'];
  runStatus?: RunStatus;
}

type TabKey = 'live' | 'tools' | 'events';

const TAB_LABELS: Record<TabKey, string> = {
  live: 'Live output',
  tools: 'Tool calls',
  events: 'Events',
};

const formatTs = (ts?: number): string => {
  if (!ts) return '';
  try {
    const d = new Date(ts * 1000);
    return d.toISOString().split('T')[1].replace('Z', '');
  } catch {
    return '';
  }
};

export const TaskRunInspector: React.FC<Props> = ({
  live, onClear, defaultOpen = false, runStatus,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const [tab, setTab] = useState<TabKey>('live');

  // The tabs' honest "trace not retained after the run completed" branches
  // were unreachable: this prop was declared and passed by the parent but
  // never destructured, so isTerminal was always undefined and a finished
  // run reported "No tool calls yet." as though it had been silent.
  //
  // Now shares ONE definition with the controls layer (isRunOver).  The
  // local list here omitted 'partial' and 'held', so the two statuses
  // that most need a "this is over" cue were the two that never got one
  // — a partial run kept its streaming cursor alive indefinitely.
  const isTerminal = isRunOver(runStatus);
  const eventCount = live.events.length;
  const toolCount = live.toolCalls.length;
  const blockCount = Object.keys(live.text).filter(k => live.text[k]?.length).length;

  return (
    <details
      className="tc-tile__inspector"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary>
        🔍 Inspect
        {(eventCount > 0 || toolCount > 0) && (
          <span className="tc-tile__inspector-counts">
            {' '}({eventCount} events
            {toolCount > 0 && `, ${toolCount} tool calls`}
            {blockCount > 0 && `, ${blockCount} blocks`})
          </span>
        )}
      </summary>

      <div className="tc-tile__inspector-tabs">
        {(Object.keys(TAB_LABELS) as TabKey[]).map(k => (
          <button
            key={k}
            className={`tc-tile__inspector-tab${tab === k ? ' tc-tile__inspector-tab--active' : ''}`}
            onClick={() => setTab(k)}
          >
            {TAB_LABELS[k]}
          </button>
        ))}
        {onClear && (eventCount > 0 || toolCount > 0 || blockCount > 0) && (
          <button
            className="tc-tile__inspector-clear"
            onClick={onClear}
            title="Clear live buffers"
          >
            clear
          </button>
        )}
      </div>

      <div className="tc-tile__inspector-body">
        {tab === 'live' && (
          <LiveTextTab
            text={live.text} iterations={live.iterations}
            isTerminal={isTerminal}
          />
        )}
        {tab === 'tools' && <ToolCallsTab calls={live.toolCalls} iterations={live.iterations} isTerminal={isTerminal} />}
        {tab === 'events' && <EventsTab events={live.events} isTerminal={isTerminal} />}
      </div>

      {/* Completion footer.  The inspector previously had NO terminal
          state of its own: a finished run left the last iteration's
          streaming cursor blinking and the tab bodies unchanged, so a
          user watching the trace had no cue that the work had moved on
          above.  Rendered inside the drawer (not the tile header) so it
          is visible exactly where the watching happens. */}
      {isTerminal && runStatus && (
        <div
          className={`tc-tile__inspector-done tc-tile__inspector-done--${runStatus}`}
          role="status"
        >
          <span className="tc-tile__inspector-done-dot" aria-hidden>■</span>
          <span>{runOverLabel(runStatus)}</span>
          <span className="tc-tile__inspector-done-hint">
            live trace ends here
          </span>
        </div>
      )}
    </details>
  );
};

/**
 * One-line "the run is over" wording per terminal status.
 *
 * Says what happened rather than only naming the status, because the
 * point of the footer is to stop someone waiting for output that will
 * never arrive — and 'held' in particular is NOT a verdict on the work,
 * so labelling it "failed" would be actively misleading.
 */
export function runOverLabel(status: RunStatus): string {
  switch (status) {
    case 'done': return 'Run complete — no further output';
    case 'partial': return 'Run stopped after partial progress — no further output';
    case 'failed': return 'Run failed — no further output';
    case 'cancelled': return 'Run cancelled — no further output';
    case 'held': return 'Run held on an infrastructure fault — resume to continue';
    default: return 'Run ended — no further output';
  }
}

// ── Tabs ──────────────────────────────────────────────────────

const LiveTextTab: React.FC<{
  text: Record<string, string>;
  iterations: LiveTaskState['iterations'];
  isTerminal?: boolean;
}> = ({ text, iterations, isTerminal }) => {
  // Prefer per-iteration sections when we have them — gives the user
  // explicit boundaries between runs of a repeat block (or just one
  // section per block for non-repeat task blocks).  Falls back to
  // the flat per-block view when no iteration events have been
  // observed yet (early in a run, or for legacy data).
  if (iterations && iterations.length > 0) {
    return <IterationSectionsView iterations={iterations} isTerminal={isTerminal} />;
  }
  const entries = Object.entries(text).filter(([, v]) => v && v.length > 0);
  if (entries.length === 0) {
    return <div className="tc-tile__inspector-empty">No streaming text yet.</div>;
  }
  return (
    <div className="tc-tile__inspector-live">
      {entries.map(([blockId, content]) => (
        <div key={blockId} className="tc-tile__inspector-block">
          <div className="tc-tile__inspector-block-id">block {blockId}</div>
            <TaskMarkdown
              markdown={stripTaskMetaTags(content)}
              enableCodeApply={false}
              breaks={true}
              // Was hardcoded true, so a finished run kept a streaming
              // cursor blinking on its last block forever — one of the
              // three places that gave no completion signal.
              isStreaming={!isTerminal}
              isSubRender={true}
            />
        </div>
      ))}
    </div>
  );
};

/**
 * Per-iteration view: each iteration gets its own collapsible
 * section with a delimiter row (index, status badge, duration).
 * The latest iteration stays open by default so a running task's
 * stream is immediately visible; finished older iterations collapse
 * so the user can scan totals without scrolling through full bodies.
 */
const IterationSectionsView: React.FC<{
  iterations: LiveTaskState['iterations'];
  isTerminal?: boolean;
}> = ({ iterations, isTerminal }) => {
  // Filter out iterations with no streamed text — they'd just
  // produce an empty box.  The toolCalls/events tabs still surface
  // those iterations for users who want the lifecycle detail.
  const withText = iterations.filter(it => it.streamText && it.streamText.length > 0);
  if (withText.length === 0) {
    return <div className="tc-tile__inspector-empty">No streaming text yet.</div>;
  }
  const latestIdx = withText.length - 1;
  return (
    <div className="tc-tile__inspector-live">
      {withText.map((it, i) => {
        const isLatest = i === latestIdx;
        const label = it.blockId
          ? `Iteration ${it.index} · block ${it.blockId.slice(0, 8)}`
          : `Iteration ${it.index}`;
        const statusClass = `tc-tile__inspector-iter-status tc-tile__inspector-iter-status--${it.status}`;
        const duration = typeof it.durationMs === 'number'
          ? `${(it.durationMs / 1000).toFixed(1)}s`
          : null;
        return (
          <details
            key={`${it.blockId ?? 'noblock'}-${it.index}`}
            className="tc-tile__inspector-iter"
            open={isLatest}
          >
            <summary className="tc-tile__inspector-iter-summary">
              <span className="tc-tile__inspector-iter-label">{label}</span>
              <span className={statusClass}>{it.status}</span>
              {duration && (
                <span className="tc-tile__inspector-iter-duration">{duration}</span>
              )}
              {typeof it.tokens === 'number' && (
                <span className="tc-tile__inspector-iter-tokens">{it.tokens.toLocaleString()} tok</span>
              )}
            </summary>
            <div className="tc-tile__inspector-iter-body">
              <TaskMarkdown
                markdown={stripTaskMetaTags(it.streamText)}
                enableCodeApply={false}
                breaks={true}
                // Belt-and-braces with the hook's terminal seal: the seal
                // fixes the DATA (a bucket left at 'running'), this guards
                // the RENDER even if a seal is ever missed.
                isStreaming={it.status === 'running' && !isTerminal}
                isSubRender={true}
              />
            </div>
          </details>
        );
      })}
    </div>
  );
};

type ToolCall = {
  block_id?: string; tool_name?: string; tool_id?: string;
  result_preview?: string; ts?: number;
};

const ToolCallsTab: React.FC<{
  calls: ToolCall[];
  iterations: LiveTaskState['iterations'];
  isTerminal?: boolean;
}> = ({ calls, iterations, isTerminal }) => {
  // Per-call expansion state.  Default-truncated previews avoid
  // the scroll-within-scroll nesting (320px outer body × N×120px
  // inner previews) that made the d3 task's tool output unreadable.
  // Keys are scoped (``${iterIdx}:${callIdx}`` or ``flat:${i}``)
  // so expansion state survives re-render and doesn't collide
  // across iteration sections.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (calls.length === 0) setExpanded(new Set());
  }, [calls.length === 0]);

  if (calls.length === 0) {
    // Tool calls are emitted only via the WebSocket stream during
    // execution; they are not persisted in the artifact.  After a
    // page navigation the ``live`` buffer resets to empty, and
    // there's nothing to rehydrate from.  Be honest about that
    // rather than implying the run made no tool calls.
    if (isTerminal) {
      return <div className="tc-tile__inspector-empty">
        Live tool-call trace not retained after the run completed.<br/>
        (Only the final summary is persisted server-side.)
      </div>;
    }
    return <div className="tc-tile__inspector-empty">No tool calls yet.</div>;
  }

  // Prefer per-iteration grouping when available — same UX as the
  // Live tab.  Iterations with no tool calls are filtered so the
  // user doesn't see empty sections.  Falls back to the flat list
  // when iterations is empty (covers legacy runs and edge cases
  // where calls didn't get bucketed).
  const withCalls = (iterations ?? []).filter(
    it => it.toolCalls && it.toolCalls.length > 0,
  );
  const useGrouped = withCalls.length > 0;

  if (useGrouped) {
    const latestIdx = withCalls.length - 1;
    return (
      <div className="tc-tile__inspector-tools-grouped">
        {withCalls.map((it, iterI) => {
          const isLatest = iterI === latestIdx;
          const label = it.blockId
            ? `Iteration ${it.index} · block ${it.blockId.slice(0, 8)}`
            : `Iteration ${it.index}`;
          const statusClass = `tc-tile__inspector-iter-status tc-tile__inspector-iter-status--${it.status}`;
          return (
            <details
              key={`${it.blockId ?? 'noblock'}-${it.index}`}
              className="tc-tile__inspector-iter"
              open={isLatest}
            >
              <summary className="tc-tile__inspector-iter-summary">
                <span className="tc-tile__inspector-iter-label">{label}</span>
                <span className={statusClass}>{it.status}</span>
                <span className="tc-tile__inspector-iter-tokens">
                  {it.toolCalls.length} call{it.toolCalls.length === 1 ? '' : 's'}
                </span>
              </summary>
              <div className="tc-tile__inspector-iter-body">
                <ToolCallList
                  calls={it.toolCalls}
                  scope={`iter-${iterI}`}
                  expanded={expanded}
                  setExpanded={setExpanded}
                />
              </div>
            </details>
          );
        })}
      </div>
    );
  }

  return (
    <ToolCallList
      calls={calls}
      scope="flat"
      expanded={expanded}
      setExpanded={setExpanded}
    />
  );
};

/**
 * Inner list — shared between the iteration-grouped view and the
 * flat fallback.  ``scope`` is mixed into the expansion-key so two
 * separate calls of this component don't share state.
 */
const ToolCallList: React.FC<{
  calls: ToolCall[];
  scope: string;
  expanded: Set<string>;
  setExpanded: React.Dispatch<React.SetStateAction<Set<string>>>;
}> = ({ calls, scope, expanded, setExpanded }) => {
  return (
    <ol className="tc-tile__inspector-tools">
      {calls.map((c, i) => {
        const key = `${scope}:${i}`;
        const isExpanded = expanded.has(key);
        // Default budget: 4 lines / 280 chars — wide enough to see
        // a path, error class, or list start without consuming the
        // inspector's whole height.
        const preview = c.result_preview
          ? truncatePreview(
              c.result_preview,
              isExpanded ? 1_000_000 : 4,
              isExpanded ? 1_000_000 : 280,
            )
          : null;
        return (
          <li key={`${c.tool_id ?? i}-${i}`} className="tc-tile__inspector-tool">
            <div className="tc-tile__inspector-tool-head">
              <span className="tc-tile__inspector-tool-name">{c.tool_name ?? '(unknown)'}</span>
              <span className="tc-tile__inspector-tool-ts">{formatTs(c.ts)}</span>
              {preview && preview.truncated && (
                <button
                  className="tc-tile__inspector-tool-expand"
                  onClick={() => {
                    const next = new Set(expanded);
                    if (next.has(key)) next.delete(key); else next.add(key);
                    setExpanded(next);
                  }}
                  title={`${preview.fullLines} lines · ${preview.fullChars.toLocaleString()} chars`}
                >{isExpanded ? '−' : '+'}</button>
              )}
            </div>
            {preview && preview.shown.length > 0 && (
              <pre className="tc-tile__inspector-tool-preview">
                {preview.shown}
                {preview.truncated && !isExpanded && (
                  <span className="tc-tile__inspector-tool-preview-ellipsis">
                    {`\n… (${preview.fullLines > 4
                      ? `${preview.fullLines - 4} more line${preview.fullLines - 4 === 1 ? '' : 's'}`
                      : `${preview.fullChars - preview.shown.length} more chars`})`}
                  </span>
                )}
              </pre>
            )}
          </li>
        );
      })}
    </ol>
  );
};

const EventsTab: React.FC<{
  events: Array<{ type: string; ts?: number; [k: string]: unknown }>;
  isTerminal?: boolean;
}> = ({ events, isTerminal }) => {
  // Per-delta-run expansion state.  Index-based since the bucketed
  // event list is the canonical chronological ordering for the run.
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  useEffect(() => {
    if (events.length === 0) setExpanded(new Set());
  }, [events.length === 0]);

  if (events.length === 0) {
    // Same reasoning as ToolCallsTab — events are WS-only and not
    // persisted, so a terminal run with an empty buffer means the
    // trace was lost when the user navigated away, not that the
    // run was silent.
    if (isTerminal) {
      return <div className="tc-tile__inspector-empty">
        Live event trace not retained after the run completed.<br/>
        (Only the final summary is persisted server-side.)
      </div>;
    }
    return <div className="tc-tile__inspector-empty">No events yet.</div>;
  }

  // Forward chronological order: events arrive append-only from the
  // server, so we trust source order rather than reversing.  Then
  // bucket by iteration boundary so each iteration renders in its
  // own gentle box (matching the Live and Tools tabs).  Lifecycle
  // events (run_started/completed and pre-iteration events) get
  // their own box at the top; remaining buckets are iterations.
  // Within each box, deltas are still collapsed so the timeline
  // stays scannable.
  const buckets: EventBucket[] = bucketEventsByIteration(events as RawEvent[]);
  // Hide the lifecycle box if it's empty; always show iteration
  // boxes so an empty one (started without any body yet) is still
  // visible as a structural placeholder.
  const visibleBuckets = buckets.filter(
    b => b.kind === 'iteration' || b.events.length > 0
  );
  const lastIterIdx = (() => {
    for (let i = visibleBuckets.length - 1; i >= 0; i--) {
      if (visibleBuckets[i].kind === 'iteration') return i;
    }
    return -1;
  })();

  // Running counter so each delta-run gets a globally unique
  // expand/collapse key across buckets.
  let absDeltaIdx = 0;

  const renderBucketBody = (display: DisplayEvent[], offset: number) => (
    <ol className="tc-tile__inspector-events" start={1}>
      {display.map((evt, i) => {
        const idx = offset + i;
        if ((evt as DeltaRun).type === 'task_text_delta_run') {
          const drun = evt as DeltaRun;
          const isExpanded = expanded.has(idx);
          return (
            <li key={idx} className="tc-tile__inspector-event tc-tile__inspector-event--delta-run">
              <span className="tc-tile__inspector-event-ts">{formatTs(drun.ts)}</span>
              <span className="tc-tile__inspector-event-type">task_text_delta_run</span>
              <code className="tc-tile__inspector-event-payload">
                {drun.count} delta{drun.count === 1 ? '' : 's'}, {drun.totalChars.toLocaleString()} chars
                {drun.block_id ? ` · block=${drun.block_id.slice(0, 8)}` : ''}
              </code>
              <button
                className="tc-tile__inspector-event-expand"
                onClick={() => {
                  const next = new Set(expanded);
                  if (next.has(idx)) next.delete(idx); else next.add(idx);
                  setExpanded(next);
                }}
              >{isExpanded ? '−' : '+'}</button>
              {isExpanded && (
                <pre className="tc-tile__inspector-event-expanded">
                  {drun.rawEvents
                    .map(e => (typeof e.content === 'string' ? e.content : ''))
                    .join('')}
                </pre>
              )}
            </li>
          );
        }
        const ev = evt as RawEvent;
        if (ev.type === 'improve_revision') {
          // A self-improvement verdict is the one event a reader must
          // not scroll past: it explains why the level restarted (or
          // why it stopped), and 'applied' means the card's text was
          // durably changed by this run.
          const verdict = typeof ev.verdict === 'string' ? ev.verdict : '?';
          const applied = !!ev.applied;
          const stop = typeof ev.stop === 'string' ? ev.stop : '';
          const rationale = typeof ev.rationale === 'string' ? ev.rationale : '';
          return (
            <li key={idx} className="tc-tile__inspector-event tc-tile__inspector-event--improve">
              <span className="tc-tile__inspector-event-ts">{formatTs(ev.ts as number | undefined)}</span>
              <span className="tc-tile__inspector-event-type">🌱 self-improve</span>
              <code className="tc-tile__inspector-event-payload">
                {verdict}
                {applied ? ' · revision applied — level restarted' : ''}
                {stop && stop !== 'accept' ? ` · stopped: ${stop}` : ''}
                {rationale ? ` — ${rationale}` : ''}
              </code>
            </li>
          );
        }
        return (
          <li key={idx} className="tc-tile__inspector-event">
            <span className="tc-tile__inspector-event-ts">{formatTs(ev.ts as number | undefined)}</span>
            <span className="tc-tile__inspector-event-type">{ev.type}</span>
            <code className="tc-tile__inspector-event-payload">
              {summarizeEvent(ev)}
            </code>
          </li>
        );
      })}
    </ol>
  );

  return (
    <div>
      {visibleBuckets.map((bucket, bIdx) => {
        const display: DisplayEvent[] = collapseEventRuns(bucket.events);
        const offset = absDeltaIdx;
        absDeltaIdx += display.length;
        // Lifecycle box gets its own label and stays open by
        // default — it's small and the user usually wants it
        // visible.  Iteration boxes use the same status badge
        // treatment as Live/Tools tabs; latest open, older closed.
        if (bucket.kind === 'lifecycle') {
          return (
            <details key={`lifecycle-${bIdx}`} className="tc-tile__inspector-iter" open>
              <summary className="tc-tile__inspector-iter-summary">
                <span className="tc-tile__inspector-iter-label">Lifecycle</span>
                <span className="tc-tile__inspector-iter-tokens">
                  {bucket.events.length} event{bucket.events.length === 1 ? '' : 's'}
                </span>
              </summary>
              <div className="tc-tile__inspector-iter-body">
                {renderBucketBody(display, offset)}
              </div>
            </details>
          );
        }
        const isLatest = bIdx === lastIterIdx;
        const label = bucket.blockId
          ? `Iteration ${bucket.index} · block ${bucket.blockId.slice(0, 8)}`
          : `Iteration ${bucket.index}`;
        const statusClass = bucket.status
          ? `tc-tile__inspector-iter-status tc-tile__inspector-iter-status--${bucket.status}`
          : '';
        return (
          <details
            key={`iter-${bucket.index}-${bucket.blockId ?? 'noblock'}`}
            className="tc-tile__inspector-iter"
            open={isLatest}
          >
            <summary className="tc-tile__inspector-iter-summary">
              <span className="tc-tile__inspector-iter-label">{label}</span>
              {bucket.status && <span className={statusClass}>{bucket.status}</span>}
              <span className="tc-tile__inspector-iter-tokens">
                {bucket.events.length} event{bucket.events.length === 1 ? '' : 's'}
              </span>
            </summary>
            <div className="tc-tile__inspector-iter-body">
              {renderBucketBody(display, offset)}
            </div>
          </details>
        );
      })}
    </div>
  );
};

// Best-effort one-line payload summary so the timeline tab is
// scannable without expanding each row.
function summarizeEvent(ev: { [k: string]: unknown }): string {
  const skip = new Set(['type', 'ts', 'run_id']);
  const parts: string[] = [];
  for (const [k, v] of Object.entries(ev)) {
    if (skip.has(k)) continue;
    if (v == null) continue;
    if (typeof v === 'string' && v.length > 60) {
      parts.push(`${k}=${JSON.stringify(v.slice(0, 60))}…`);
    } else if (typeof v === 'object') {
      parts.push(`${k}=…`);
    } else {
      parts.push(`${k}=${String(v)}`);
    }
  }
  return parts.join(' ');
}

export default TaskRunInspector;

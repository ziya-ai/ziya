/**
 * Pure helpers for the TaskRunInspector "Events" tab.
 *
 * Two transformations the raw event stream needs before display:
 *
 *   1. **Forward chronological order** — events arrive in stream
 *      order (oldest → newest); we render them that way so a reader
 *      can follow the run's narrative top-to-bottom.  The previous
 *      implementation reversed them, which broke causality (cause
 *      appeared after effect) and made delta runs unreadable.
 *
 *   2. **Delta collapsing** — ``task_text_delta`` events arrive one
 *      per token-or-fragment.  A single sentence often produces 30+
 *      events.  Adjacent deltas for the same block are folded into
 *      one ``DeltaRun`` summary row showing count and total character
 *      length, expandable by the UI if the user wants the raw run.
 *
 * Both helpers are pure functions over arrays so they can be unit
 * tested without React/WebSocket plumbing.  Bounded-output behaviour
 * (pagination) is the caller's concern — this module just shapes
 * the data.
 */

export interface RawEvent {
  type: string;
  ts?: number;
  [k: string]: unknown;
}

/**
 * One ``{{placeholder}}`` from a task's instructions and what it
 * expanded to for one iteration.  Carried by the ``task_bindings``
 * event the block executor emits at task dispatch
 * (app/agents/block_executor.py ``_emit_task_bindings``).
 *
 * ``resolved: false`` means the model saw the braces literally — an
 * unknown head such as a typo, or a loop-scoped placeholder outside a
 * loop.  A KNOWN placeholder with no data yet (``previous.summary`` on
 * iteration 0) is resolved with value "", matching the renderer.
 */
export interface TemplateBinding {
  placeholder: string;
  value: string | null;
  resolved: boolean;
  /** Set when the server clipped ``value``; ``length`` is the full size. */
  truncated?: boolean;
  length?: number | null;
}

function isTemplateBinding(v: unknown): v is TemplateBinding {
  if (!v || typeof v !== 'object') return false;
  const b = v as { placeholder?: unknown; resolved?: unknown; value?: unknown };
  return typeof b.placeholder === 'string'
    && typeof b.resolved === 'boolean'
    && (b.value === null || b.value === undefined || typeof b.value === 'string');
}

/** The bindings payload of a ``task_bindings`` event, or [] for anything else. */
export function bindingsFromEvent(evt: unknown): TemplateBinding[] {
  if (!evt || typeof evt !== 'object') return [];
  const e = evt as { type?: unknown; bindings?: unknown };
  if (e.type !== 'task_bindings' || !Array.isArray(e.bindings)) return [];
  return e.bindings.filter(isTemplateBinding).map(b => ({
    ...b, value: b.value ?? null,
  }));
}

/**
 * Merge a newer set of bindings over an existing one, keyed by
 * placeholder.  An iteration whose body is a group of several tasks
 * emits one ``task_bindings`` per task; they render against the same
 * iteration bindings, so a shared placeholder carries the same value
 * and later entries simply confirm earlier ones.  Where they do
 * differ (``previous_sibling`` inside a body group) the later task's
 * value wins — it is the most recent thing the model was told.
 * Order is first-appearance, so the header stays stable as tasks
 * add to it.
 */
export function mergeBindings(
  existing: ReadonlyArray<TemplateBinding> | undefined,
  incoming: ReadonlyArray<TemplateBinding>,
): TemplateBinding[] {
  if (!existing || existing.length === 0) return [...incoming];
  if (incoming.length === 0) return [...existing];
  const out = [...existing];
  for (const b of incoming) {
    const i = out.findIndex(x => x.placeholder === b.placeholder);
    if (i < 0) out.push(b); else out[i] = b;
  }
  return out;
}

/** Bindings reported across a slice of events (an Events-tab bucket). */
export function collectBindings(events: ReadonlyArray<RawEvent>): TemplateBinding[] {
  let acc: TemplateBinding[] = [];
  for (const evt of events) {
    const got = bindingsFromEvent(evt);
    if (got.length) acc = mergeBindings(acc, got);
  }
  return acc;
}

/**
 * A collapsed run of adjacent ``task_text_delta`` events for the
 * same block.  Surfaces aggregate stats (count, char total) so the
 * timeline stays scannable.  ``rawEvents`` retained for opt-in
 * expansion.
 */
export interface DeltaRun {
  type: 'task_text_delta_run';
  block_id: string;
  count: number;
  totalChars: number;
  ts?: number;       // timestamp of the FIRST delta in the run
  endTs?: number;    // timestamp of the LAST delta in the run
  rawEvents: RawEvent[];
}

/** Display-time event: either a single raw event or a collapsed delta run. */
export type DisplayEvent = RawEvent | DeltaRun;

/**
 * Fold adjacent ``task_text_delta`` events for the same ``block_id``
 * into a single ``DeltaRun``.  Non-delta events and deltas separated
 * by other events (or for a different block) start a new run.
 *
 * Preserves input order — does not reverse, sort, or filter.
 */
export function collapseEventRuns(events: ReadonlyArray<RawEvent>): DisplayEvent[] {
  const out: DisplayEvent[] = [];
  for (const evt of events) {
    if (evt.type === 'task_text_delta') {
      const blockId = typeof evt.block_id === 'string' ? evt.block_id : '';
      const content = typeof evt.content === 'string' ? evt.content : '';
      const last = out[out.length - 1];
      if (last && (last as DeltaRun).type === 'task_text_delta_run'
          && (last as DeltaRun).block_id === blockId) {
        const run = last as DeltaRun;
        run.count += 1;
        run.totalChars += content.length;
        run.endTs = typeof evt.ts === 'number' ? evt.ts : run.endTs;
        run.rawEvents.push(evt);
      } else {
        out.push({
          type: 'task_text_delta_run',
          block_id: blockId,
          count: 1,
          totalChars: content.length,
          ts: typeof evt.ts === 'number' ? evt.ts : undefined,
          endTs: typeof evt.ts === 'number' ? evt.ts : undefined,
          rawEvents: [evt],
        });
      }
    } else if (evt.type === 'task_text_delta_run') {
      // Server-side relay (task_run_stream_relay.py) collapses
      // adjacent deltas into runs before fanout.  Those events
      // arrive with shape { type, block_id, count, content }
      // — totalChars / rawEvents / ts aren't set.  Normalise to
      // the local DeltaRun shape so consumers (which read
      // totalChars.toLocaleString() and walk rawEvents) don't
      // have to special-case undefined.
      const blockId = typeof (evt as { block_id?: unknown }).block_id === 'string'
        ? (evt as unknown as { block_id: string }).block_id : '';
      const content = typeof (evt as { content?: unknown }).content === 'string'
        ? (evt as unknown as { content: string }).content : '';
      const count = typeof (evt as { count?: unknown }).count === 'number'
        ? (evt as unknown as { count: number }).count : 0;
      out.push({
        type: 'task_text_delta_run',
        block_id: blockId,
        count,
        totalChars: content.length,
        ts: typeof evt.ts === 'number' ? evt.ts : undefined,
        endTs: undefined,
        rawEvents: [evt],
      });
    } else {
      out.push(evt);
    }
  }
  return out;
}

export interface PageWindow<T> {
  /** Items in this window (display-time slice). */
  items: T[];
  /** 0-based page index. */
  page: number;
  /** Total number of pages (>= 1, even if items is empty). */
  pageCount: number;
  /** Total item count across all pages. */
  total: number;
}

/**
 * Slice a chronological array into a page window.  ``page`` is
 * clamped to the valid range; negative values are treated as 0.
 * When ``page`` exceeds ``pageCount - 1`` it's clamped to the last
 * page — useful for "show latest" UX where new events extend the
 * tail and the caller wants to keep tracking the end.
 */
export function pageEvents<T>(
  items: ReadonlyArray<T>,
  page: number,
  pageSize: number,
): PageWindow<T> {
  const size = Math.max(1, Math.floor(pageSize));
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / size));
  const clamped = Math.min(Math.max(0, Math.floor(page)), pageCount - 1);
  const start = clamped * size;
  return {
    items: items.slice(start, start + size),
    page: clamped,
    pageCount,
    total,
  };
}

/**
 * A bucket of events grouped by iteration boundary, for the
 * Events tab's "gentle box per iteration" rendering.
 *
 * Two kinds of bucket:
 *   - ``lifecycle``: events that aren't tied to any iteration —
 *     ``run_started``, ``run_completed``, ``run_failed``, plus any
 *     events that occurred before the first ``iteration_started``.
 *   - ``iteration``: a contiguous slice of events between (and
 *     including) one ``iteration_started`` and its matching
 *     ``iteration_completed``.  If the iteration is still running,
 *     the slice extends to the end of the input and ``status`` is
 *     "running".
 *
 * Bucketing is purely structural — it doesn't reorder events,
 * doesn't filter, and doesn't combine adjacent deltas (callers can
 * apply ``collapseEventRuns`` to a bucket's events if desired).
 * Buckets appear in source order so the UI can render them
 * top-to-bottom and the latest bucket is always last.
 */
export interface EventBucket {
  kind: 'lifecycle' | 'iteration';
  /** 0-based ordinal among emitted iterations (only for kind="iteration"). */
  index?: number;
  /** Block id from the iteration's started event (only for kind="iteration"). */
  blockId?: string;
  /** "running" until iteration_completed is seen, then mirrors that event's status. */
  status?: 'running' | 'passed' | 'failed';
  /** Events in source order. */
  events: RawEvent[];
}

/**
 * Split a flat event timeline into iteration-keyed buckets plus a
 * lifecycle bucket for run-scoped events.
 *
 * Iteration boundary detection:
 *   - ``iteration_started`` opens a new iteration bucket.  Its
 *     ``index``/``blockId`` are read from the event payload when
 *     present, otherwise inferred from emit order.
 *   - ``iteration_completed`` closes the current iteration and
 *     copies its status ("passed" / "failed").  Defensive: if
 *     there's no open iteration, the event is dropped into the
 *     lifecycle bucket.
 *   - Any event before the first ``iteration_started`` goes to the
 *     lifecycle bucket.
 *   - Run-scoped events (``run_started``, ``run_completed``,
 *     ``run_failed``) always go to the lifecycle bucket regardless
 *     of where they appear in the stream.
 *
 * The lifecycle bucket is always returned, even if empty, so
 * callers can render it as a stable section header.  Empty
 * iteration buckets (``iteration_started`` with no body before
 * ``iteration_completed``) are returned as-is — the caller decides
 * whether to show or hide them.
 */
export function bucketEventsByIteration(
  events: ReadonlyArray<RawEvent>,
): EventBucket[] {
  const RUN_SCOPE = new Set(['run_started', 'run_completed', 'run_failed']);
  const lifecycle: EventBucket = { kind: 'lifecycle', events: [] };
  const iterations: EventBucket[] = [];
  let current: EventBucket | null = null;
  let nextIndex = 0;

  for (const evt of events) {
    if (RUN_SCOPE.has(evt.type)) {
      lifecycle.events.push(evt);
      continue;
    }
    if (evt.type === 'iteration_started') {
      const idxRaw = (evt as { index?: unknown }).index;
      const blockIdRaw = (evt as { block_id?: unknown }).block_id;
      const bucket: EventBucket = {
        kind: 'iteration',
        index: typeof idxRaw === 'number' ? idxRaw : nextIndex,
        blockId: typeof blockIdRaw === 'string' ? blockIdRaw : undefined,
        status: 'running',
        events: [evt],
      };
      iterations.push(bucket);
      current = bucket;
      nextIndex += 1;
      continue;
    }
    if (evt.type === 'iteration_completed') {
      if (current) {
        current.events.push(evt);
        const statusRaw = (evt as { status?: unknown }).status;
        current.status = statusRaw === 'failed' ? 'failed' : 'passed';
        current = null;
      } else {
        // Defensive: completion without an open iteration.  Stash
        // it in lifecycle so it's not silently dropped.
        lifecycle.events.push(evt);
      }
      continue;
    }
    // Any other event — route to current iteration if one's open,
    // otherwise to lifecycle (events before the first iteration).
    (current ?? lifecycle).events.push(evt);
  }

  return [lifecycle, ...iterations];
}

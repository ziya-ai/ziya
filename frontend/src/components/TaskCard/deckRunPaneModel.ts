/**
 * deckRunPaneModel — pure helpers behind the Task Cards dialog's run pane.
 *
 * Two jobs:
 *
 *  1. Which runs a card has.  A Call executes its target inline in the
 *     caller's run, so a card that only runs as a callee owns no run
 *     records; its entries come from the callers' runs (CalleeContext).
 *
 *  2. The run as a thread: one section per block, and which section is
 *     open.  The pane shows one block's output at a time, so its length
 *     follows what is being read, not how long the run has been going.
 */

import type { Block } from '../../types/task_card';
import type {
  BlockStatus, CalleeContext, IterationSummary, TaskRun,
} from '../../types/task_run';
import { isLiveRun, needsAttention } from './deckRunIndex';
import {
  flattenBlocks, isLoopBlock, resolveBlockStatus, type LoopPosition,
} from './runMapModel';
import { recoveryTarget } from './recoveryTarget';
import { holdLabel, positionOf, type HoldChain } from './holdChain';

// ── Run entries ───────────────────────────────────────────────

export interface DeckRunEntry {
  runId: string;
  status: string;
  /** Epoch ms used for ordering and the age label. */
  at: number;
  attempt?: number | null;
  /** Card that owns the run, when it is not this card. */
  callerCardId?: string;
  /** This card's tree as the caller's run recorded it. */
  calleeRoot?: Block | null;
  conversationId?: string | null;
}

/**
 * A card's runs: its own, then the runs it is being called inside.
 * Deduplicated by run id (own record wins), newest first within each
 * group.
 */
export function buildRunEntries(
  ownRuns: ReadonlyArray<TaskRun>,
  callee: ReadonlyArray<CalleeContext>,
): DeckRunEntry[] {
  const out: DeckRunEntry[] = [];
  const seen = new Set<string>();
  for (const r of ownRuns) {
    if (!r?.id || seen.has(r.id)) continue;
    seen.add(r.id);
    out.push({
      runId: r.id, status: r.status, at: r.created_at,
      attempt: r.attempt ?? null,
      conversationId: r.source_conversation_id ?? null,
    });
  }
  const called = [...callee].sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
  for (const c of called) {
    if (!c?.run_id || seen.has(c.run_id)) continue;
    seen.add(c.run_id);
    out.push({
      runId: c.run_id, status: c.run_status, at: c.updated_at ?? 0,
      callerCardId: c.caller_card_id,
      calleeRoot: c.callee_root ?? null,
      conversationId: c.source_conversation_id ?? null,
    });
  }
  return out;
}

/** True when any entry is held, failed, partial or still executing. */
export function entriesWantEyes(entries: ReadonlyArray<DeckRunEntry>): boolean {
  return entries.some(e => needsAttention(e.status) || isLiveRun(e.status));
}

/**
 * The run shown when a card is opened: a live one, else one that needs
 * attention, else the first listed.  Live outranks attention because a
 * retry in flight supersedes the failed attempt it came from.
 */
export function defaultEntryId(
  entries: ReadonlyArray<DeckRunEntry>,
): string | null {
  const live = entries.find(e => isLiveRun(e.status));
  if (live) return live.runId;
  const attn = entries.find(e => needsAttention(e.status));
  if (attn) return attn.runId;
  return entries[0]?.runId ?? null;
}

// ── Thread ────────────────────────────────────────────────────

export interface ThreadSection {
  block: Block;
  depth: number;
  status: BlockStatus;
  isLoop: boolean;
  /** Enclosing loop, for a direct child of a loop body. */
  loop?: LoopPosition;
  /** Set when the row belongs to a card reached through a Call. */
  viaCall?: string;
  durationMs: number | null;
}

export interface ThreadFocus {
  blockId: string;
  /** Loop iteration; null for a block that is not a loop. */
  index: number | null;
  passKey: string | null;
}

/** One section per visible block of ``root``, in execution order. */
export function buildThread(
  root: Block | null | undefined,
  run: TaskRun | null,
  liveStatuses: Record<string, string> = {},
): ThreadSection[] {
  return flattenBlocks(root, 0, run?.call_snapshots).map(row => {
    const st = run?.block_states?.[row.block.id];
    const dur = st?.artifact?.duration_ms
      ?? (st?.started_at != null && st?.completed_at != null
        ? st.completed_at - st.started_at : null);
    return {
      block: row.block,
      depth: row.depth,
      status: resolveBlockStatus(row.block.id, liveStatuses, run, row.loop),
      isLoop: isLoopBlock(row.block),
      ...(row.loop ? { loop: row.loop } : {}),
      ...(row.viaCall ? { viaCall: row.viaCall } : {}),
      durationMs: typeof dur === 'number' && dur >= 0 ? dur : null,
    };
  });
}

/**
 * The iteration of a loop worth opening: one in flight, else the last
 * that failed, else the last recorded.  "Last" is array order, which is
 * the order iterations sealed in.
 */
export function pickIteration(
  summaries: ReadonlyArray<IterationSummary> | undefined,
  runningIndices: ReadonlyArray<number> = [],
): { index: number; passKey: string | null } | null {
  const all = summaries ?? [];
  if (runningIndices.length > 0) {
    const last = all[all.length - 1];
    return {
      index: Math.max(...runningIndices),
      passKey: last?.pass_key ?? null,
    };
  }
  for (let i = all.length - 1; i >= 0; i--) {
    if (all[i].status === 'failed') {
      return { index: all[i].index, passKey: all[i].pass_key ?? null };
    }
  }
  const last = all[all.length - 1];
  return last ? { index: last.index, passKey: last.pass_key ?? null } : null;
}

/**
 * The section a block's output is shown under.  A loop body's output is
 * recorded against the loop and an iteration index, never against the
 * body block, so a body block resolves to its loop.
 */
export function focusFor(
  section: ThreadSection,
  run: TaskRun | null,
  runningIndices: ReadonlyArray<number> = [],
): ThreadFocus {
  const blockId = section.loop && !section.isLoop
    ? section.loop.id : section.block.id;
  const isLoop = section.isLoop || !!section.loop;
  if (!isLoop) return { blockId, index: null, passKey: null };
  const it = pickIteration(
    run?.block_states?.[blockId]?.iteration_summaries, runningIndices);
  return { blockId, index: it?.index ?? null, passKey: it?.passKey ?? null };
}

/**
 * The section open by default: where a stopped run stopped, else what is
 * running, else the last block that finished.  Null when the run has not
 * reached any block of this tree — including a hold that belongs to a
 * different card of the same study.
 */
export function defaultFocus(
  sections: ReadonlyArray<ThreadSection>,
  run: TaskRun | null,
  runningByBlock: Record<string, number[]> = {},
): ThreadFocus | null {
  if (!run || sections.length === 0) return null;
  const pick = (s: ThreadSection | undefined) => {
    if (!s) return null;
    const f = focusFor(s, run);
    return focusFor(s, run, runningByBlock[f.blockId] ?? []);
  };
  const target = recoveryTarget(run);
  if (target) {
    const hit = pick(sections.find(s => s.block.id === target.blockId));
    if (hit) return hit;
  }
  const running = pick(sections.find(s => s.status === 'running'));
  if (running) return running;
  for (let i = sections.length - 1; i >= 0; i--) {
    if (['done', 'failed', 'held', 'partial'].includes(sections[i].status)) {
      return pick(sections[i]);
    }
  }
  return null;
}

export function sameFocus(
  a: ThreadFocus | null, b: ThreadFocus | null,
): boolean {
  return !!a && !!b && a.blockId === b.blockId && a.index === b.index
    && (a.passKey ?? null) === (b.passKey ?? null);
}

/** "14s", "1m 48s", "1h 48m". */
export function formatElapsed(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// ── Outline markers ───────────────────────────────────────────

/** What the definition outline shows for one block of the shown run. */
export interface OutlineMarker {
  /** Block status, as a STATUS_GLYPHS key. */
  status: string;
  /** Position relative to a hold, when the run is held in this tree. */
  badge?: 'held' | 'holding' | 'blocked';
  title?: string;
}

const NOT_RUN: ReadonlySet<string> = new Set(['queued', 'cancelled', 'skipped']);

/**
 * Per-block markers for the definition outline, from the run the run
 * pane is showing.  A hold is marked only when the held block is in this
 * tree: a hold in another card of the same study is that card's to show.
 */
export function outlineMarkers(
  sections: ReadonlyArray<ThreadSection>,
  chain: HoldChain,
): Record<string, OutlineMarker> {
  const out: Record<string, OutlineMarker> = {};
  const holdHere = chain.isHeld && !!chain.heldBlockId
    && sections.some(s => s.block.id === chain.heldBlockId);
  for (const s of sections) {
    const id = s.block.id;
    if (!id) continue;
    const pos = holdHere ? positionOf(chain, id) : 'none';
    const title = pos === 'none' ? undefined : (holdLabel(chain, pos) ?? undefined);
    if (pos === 'local') out[id] = { status: 'held', badge: 'held', title };
    else if (pos === 'descendant') out[id] = { status: 'held', badge: 'holding', title };
    // The derivation calls every block off the hold path an ancestor,
    // including ones that finished before the hold; only a block that
    // never ran was stopped by it.
    else if (pos === 'ancestor' && NOT_RUN.has(s.status)) {
      out[id] = { status: s.status, badge: 'blocked', title };
    }
    else out[id] = { status: s.status };
  }
  return out;
}

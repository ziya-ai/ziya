/**
 * runMapModel — pure helpers backing TaskRunMap (the per-block run
 * visualization).  Extracted from the component so status resolution,
 * tree flattening, and iteration-dot windowing are unit-testable.
 */

import type { Block } from '../../types/task_card';
import type {
  TaskRun, BlockStatus, IterationSummary, CallSnapshot,
} from '../../types/task_run';

export interface MapRow {
  block: Block;
  depth: number;
  /**
   * Id of the Call block this row was reached through, when it belongs
   * to a callee rather than to the card itself.  Lets the row say so:
   * the callee is a different card, and presenting its blocks as though
   * this card declared them would misattribute both the work and the
   * permissions.
   */
  viaCall?: string;
  /**
   * Set on a DIRECT child of a repeat/until body: the loop's id and this
   * block's position in that body.  Body blocks have no block_states
   * entry (the executor persists only structural blocks), so this is the
   * key resolveBlockStatus uses to read the block's outcome from the
   * loop's latest iteration digest.  A child of a group inside the body
   * is not tagged: the executor records the group as ONE stage.
   */
  loop?: LoopPosition;
}

export interface LoopPosition {
  id: string;
  index: number;
}

/**
 * Flatten a block tree into indented display rows, depth-first.
 * Group blocks are invisible wrappers (matching the editor, which
 * renders them chromeless): their children appear at the group's own
 * depth and the group itself gets no row.
 *
 * ``callSnapshots`` splices a resolved Call target's tree in beneath its
 * call row.  The callee lives in another card, so it is in neither this
 * card nor ``card_snapshot`` — without it the map shows a call row that
 * produced an artifact from nothing, while the callee's blocks stream
 * status events that land on no row.
 */
export function flattenBlocks(
  root: Block | undefined | null, depth = 0,
  callSnapshots?: Record<string, CallSnapshot>,
  // Guards a malformed record: the server rejects call cycles, but a
  // hand-edited or truncated run file must not hang the UI.
  seen: ReadonlySet<string> = new Set(),
  loop?: LoopPosition,
): MapRow[] {
  if (!root) return [];
  const rows: MapRow[] = [];
  if (root.block_type === 'group') {
    for (const child of root.body ?? []) {
      rows.push(...flattenBlocks(child, depth, callSnapshots, seen, loop));
    }
    return rows;
  }
  rows.push(loop ? { block: root, depth, loop } : { block: root, depth });
  const isLoop = isLoopBlock(root);
  (root.body ?? []).forEach((child, i) => {
    rows.push(...flattenBlocks(
      child, depth + 1, callSnapshots, seen,
      isLoop ? { id: root.id, index: i } : undefined,
    ));
  });
  if (root.block_type === 'call' && callSnapshots) {
    const snap = callSnapshots[root.id];
    const key = snap?.key ?? root.id;
    if (snap?.root && !seen.has(key)) {
      const next = new Set(seen).add(key);
      for (const r of flattenBlocks(snap.root, depth + 1, callSnapshots, next)) {
        rows.push({ ...r, viaCall: r.viaCall ?? root.id });
      }
    }
  }
  return rows;
}

/**
 * Resolve a block's display status.  Precedence:
 *   1. live ``block_status`` events (freshest — updates mid-run)
 *   2. the REST snapshot's block_states (durable — survives reload)
 *   3. for a loop-body block with neither: the enclosing loop's latest
 *      iteration digest (``IterationSummary.stages``), matched by body
 *      position.  Body blocks never get a block_states entry, so without
 *      this every one of them read 'queued' after a reload — a 'done'
 *      loop over four never-run children showed four bare queued rows
 *      (GFX Stage 2 run 3068d3d0).
 *   4. 'queued'
 * Terminal backstop: once the run itself is terminal nothing can
 * still be running — a stale 'running' degrades to the run's own
 * terminal status (covers dropped terminal events).
 */
export function resolveBlockStatus(
  blockId: string,
  liveStatuses: Record<string, string>,
  run: TaskRun | null,
  loop?: LoopPosition,
): BlockStatus {
  const live = liveStatuses[blockId];
  const persisted = run?.block_states?.[blockId]?.status;
  let status = (
    live ?? persisted ?? (loop ? digestStatus(run, loop) : undefined) ?? 'queued'
  ) as BlockStatus;
  const terminal = run
    && ['done', 'partial', 'failed', 'cancelled', 'held'].includes(run.status);
  if (terminal && status === 'running') {
    // A stale 'running' under a terminal run degrades to the run's own
    // outcome — except 'partial', which is a RUN-level classification
    // and meaningless for a single block.  A block left running when a
    // partial run unwound was interrupted, so say that instead.
    //
    // 'held' takes the same exception, for a sharper reason: the run's
    // held_at_block_id names the ONE block that raised the fault, so
    // painting every stale-running block 'held' would claim N faults
    // where there was one and make the hold's location unfindable.  A
    // block still running when a held run unwound was cut off by the
    // fault, not the source of it — which is what 'cancelled' means
    // here, and matches the iteration records the executor persists for
    // gate-cancelled siblings.
    status = (run!.status === 'partial' || run!.status === 'held'
      ? 'cancelled'
      : run!.status) as BlockStatus;
  }
  return status;
}

/**
 * A body block's outcome in the enclosing loop's LATEST iteration (by
 * index, not array order — a resumed run's replayed prefix is seeded out
 * of sequence).  Undefined when the loop has no iterations, the record
 * predates the digest, or the position is not covered: those are
 * unknowns, and the caller's 'queued' default is the honest word for
 * them.
 */
function digestStatus(
  run: TaskRun | null, loop: LoopPosition,
): BlockStatus | undefined {
  const summaries = run?.block_states?.[loop.id]?.iteration_summaries;
  if (!summaries?.length) return undefined;
  const latest = summaries.reduce((a, b) => (b.index > a.index ? b : a));
  const entry = latest.stages?.find(s => s.index === loop.index);
  switch (entry?.status) {
    case 'passed': return 'done';
    case 'failed': return 'failed';
    case 'skipped': return 'skipped';
    case 'cancelled': return 'cancelled';
    default: return undefined;
  }
}

export const isLoopBlock = (b: Block): boolean =>
  b.block_type === 'repeat' || b.block_type === 'until';

/** Max iteration dots rendered per loop row; older passes collapse
 * into a "+N" prefix so a 10,000-iteration loop stays one line. */
export const MAX_DOTS = 30;

export interface DotModel {
  /** Most recent iterations, oldest first, capped at MAX_DOTS. */
  dots: Array<{
    index: number;
    status: 'passed' | 'failed' | 'cancelled';
    hasArtifact: boolean;
    /**
     * Carried from an earlier attempt, not executed by this run.  Drawn
     * dimmed so a resumed loop shows its preserved prefix as preserved
     * rather than restarting the count — which read as the banked
     * iterations having been discarded.
     */
    replayed: boolean;
    /** Resolved body-task name for this iteration, when templated. */
    label?: string;
  }>;
  /** Count of older iterations collapsed out of view. */
  overflow: number;
  total: number;
  /** True when the loop is mid-iteration (renders a pulsing dot). */
  running: boolean;
  /**
   * Ordinals of the iterations currently in flight, ascending.  A
   * PARALLEL Repeat has several at once, and ``running`` — one boolean
   * for the whole row — could only ever render a single pulsing dot, so
   * a fan-out of 8 was indistinguishable from a serial loop.  Empty
   * when the block is not running, or when no live iteration buckets
   * are known (a reloaded run with no live events), in which case
   * ``running`` alone still drives the legacy single dot.
   */
  runningIndices: number[];
}

export function buildDots(
  summaries: IterationSummary[] | undefined,
  blockRunning: boolean,
  runningIndices: number[] = [],
): DotModel {
  const all = summaries ?? [];
  const total = all.length;
  const shown = all.slice(Math.max(0, total - MAX_DOTS));
  return {
    dots: shown.map(s => ({
      index: s.index,
      status: s.status,
      hasArtifact: s.has_artifact,
      replayed: !!s.replayed,
      ...(s.resolved_name ? { label: s.resolved_name } : {}),
    })),
    overflow: total - shown.length,
    total,
    running: blockRunning,
    runningIndices: blockRunning
      ? [...runningIndices].sort((a, b) => a - b)
      : [],
  };
}

/** One pass of a loop: its dot model plus the enclosing-pass key
 * (``IterationSummary.pass_key``; null for a top-level loop). */
export interface DotPass {
  passKey: string | null;
  dots: DotModel;
}

/**
 * Group a loop's summaries by the outer pass that produced them, in
 * first-appearance order.  A top-level loop yields exactly one group
 * with ``passKey === null`` and a dot model identical to ``buildDots``,
 * so every pre-existing single-strip row renders unchanged.  A nested
 * loop yields one group per outer iteration; ``index`` repeats across
 * groups and is unique only within one.  Live running indices belong
 * to the pass in progress, which is always the last group (or a new
 * empty one when nothing of the current pass has completed yet).
 */
export function buildDotPasses(
  summaries: IterationSummary[] | undefined,
  blockRunning: boolean,
  runningIndices: number[] = [],
): DotPass[] {
  const order: Array<string | null> = [];
  const groups = new Map<string | null, IterationSummary[]>();
  for (const s of summaries ?? []) {
    const k = s.pass_key ?? null;
    if (!groups.has(k)) { groups.set(k, []); order.push(k); }
    groups.get(k)!.push(s);
  }
  if (order.length === 0) {
    return [{ passKey: null, dots: buildDots([], blockRunning, runningIndices) }];
  }
  return order.map((k, i) => ({
    passKey: k,
    dots: buildDots(
      groups.get(k),
      i === order.length - 1 && blockRunning,
      i === order.length - 1 ? runningIndices : [],
    ),
  }));
}

/** Passes shown before the history collapses: current plus three prior. */
export const PASS_WINDOW = 4;

/** A rendered pass line, or a placeholder for ``hidden`` collapsed ones. */
export type PassRow = DotPass | { hidden: number };

/**
 * Collapse a long pass history to the first pass, an ellipsis row, the
 * previous pass and the current one.  Up to PASS_WINDOW passes are shown
 * in full.  The first pass stays visible because it is the baseline the
 * later ones are being compared against; the last two because the
 * question a live repair loop is asked is "did this pass beat the one
 * before it?".
 */
export function collapsePasses(passes: DotPass[]): PassRow[] {
  if (passes.length <= PASS_WINDOW) return passes;
  const n = passes.length;
  return [
    passes[0],
    { hidden: n - 3 },
    passes[n - 2],
    passes[n - 1],
  ];
}

/**
 * Label for the count trailing a loop row's dot strip.  With a known
 * roster size (a for_each Repeat persists ``planned_iterations`` at
 * plan time) progress reads as "n/m" against the whole roster;
 * without one, the bare completed count, as before.
 */
export function dotCountLabel(
  total: number, planned?: number | null,
): string {
  return planned != null && planned > 0
    ? `${total}/${planned}`
    : String(total);
}

const TYPE_EMOJI: Record<string, string> = {
  task: '🔵', repeat: '🔁', until: '🔄', parallel: '⚡',
  schedule: '⏰', state: '📌', group: '▫️', call: '📞',
};

export function blockEmoji(b: Block): string {
  if (b.block_type === 'task' && b.emoji) return b.emoji;
  return TYPE_EMOJI[b.block_type] ?? '▫️';
}

const LABEL_MAX = 70;

/** Human row label: explicit name, else first line of instructions,
 * else a type-derived descriptor. */
export function blockLabel(b: Block): string {
  if (b.name) return truncate(b.name);
  if (b.block_type === 'task' && b.instructions) {
    const line = b.instructions.trim().split('\n')[0];
    if (line) return truncate(line);
  }
  switch (b.block_type) {
    case 'repeat': {
      const mode = b.repeat_mode ?? 'count';
      if (mode === 'for_each') return 'For each item';
      if (mode === 'until') return 'Repeat until condition';
      return `Repeat ×${b.repeat_count ?? 1}`;
    }
    case 'until': return truncate(`Until: ${b.until_condition || 'condition met'}`);
    case 'parallel': return 'In parallel';
    case 'schedule': return 'Schedule';
    case 'state': return 'State / givens';
    case 'call': return truncate(`Call: ${b.call_target || '(no target)'}`);
    case 'ask': return truncate(`Ask: ${b.ask_question || 'human checkpoint'}`);
    default: return b.block_type;
  }
}

function truncate(s: string): string {
  return s.length > LABEL_MAX ? s.slice(0, LABEL_MAX) + '…' : s;
}

/** One config row in the block detail panel.  ``pre`` renders the
 * value in a monospace pre-wrapped block (instructions, sources). */
export interface ConfigLine {
  label: string;
  value: string;
  pre?: boolean;
}

/**
 * Human-readable configuration of a block, for the drill-down panel.
 * Pure over the Block definition — no run state.  Instructions are
 * surfaced verbatim (pre) since they're the block's actual brief.
 */
export function blockConfigLines(b: Block): ConfigLine[] {
  const lines: ConfigLine[] = [{ label: 'Type', value: b.block_type }];
  if (b.block_type === 'task' && b.instructions) {
    lines.push({ label: 'Instructions', value: b.instructions, pre: true });
  }
  if (b.block_type === 'repeat') {
    const mode = b.repeat_mode ?? 'count';
    lines.push({ label: 'Mode', value: mode });
    if (mode === 'count') lines.push({ label: 'Count', value: String(b.repeat_count ?? 1) });
    if (mode === 'until') {
      lines.push({ label: 'Max', value: String(b.repeat_max ?? 1) });
      if (b.repeat_until) lines.push({ label: 'Until contains', value: b.repeat_until });
    }
    if (mode === 'for_each' && b.repeat_for_each_source) {
      lines.push({ label: 'For each', value: b.repeat_for_each_source, pre: true });
    }
    lines.push({ label: 'Propagate', value: b.repeat_propagate ?? 'last' });
    if (b.repeat_parallel) lines.push({ label: 'Parallel', value: 'yes' });
  }
  if (b.block_type === 'until') {
    lines.push({ label: 'Condition', value: b.until_condition || '(none)', pre: true });
    lines.push({ label: 'Max', value: String(b.until_max ?? 5) });
    lines.push({ label: 'Mode', value: b.until_mode ?? 'model' });
  }
  if (b.block_type === 'state') {
    if (b.state_context) lines.push({ label: 'Context', value: b.state_context, pre: true });
    if (b.state_variables && Object.keys(b.state_variables).length > 0) {
      lines.push({
        label: 'Variables',
        value: JSON.stringify(b.state_variables, null, 2),
        pre: true,
      });
    }
  }
  if (b.on_failure) lines.push({ label: 'On failure', value: b.on_failure });
  const s = b.scope;
  if (s) {
    const parts: string[] = [];
    if (s.paths?.length) parts.push(`${s.paths.length} path(s)`);
    if (s.tools?.length) parts.push(`${s.tools.length} tool(s)`);
    if (s.skills?.length) parts.push(`${s.skills.length} skill(s)`);
    if (s.shell_commands?.length) parts.push(`${s.shell_commands.length} shell grant(s)`);
    if (s.model_tier) parts.push(`tier: ${s.model_tier}`);
    if (s.model_name) parts.push(`model: ${s.model_name}`);
    if (parts.length) lines.push({ label: 'Scope', value: parts.join(', ') });
  }
  return lines;
}

/** Find a block by id anywhere in a tree (depth-first). */
export function findBlockById(
  root: Block | undefined | null, id: string,
): Block | null {
  if (!root) return null;
  if (root.id === id) return root;
  for (const child of root.body ?? []) {
    const found = findBlockById(child, id);
    if (found) return found;
  }
  return null;
}

/**
 * Find a block by id in a run's FULL tree — the card's own blocks plus
 * every recorded callee.
 *
 * A Call is named, not inlined, so a callee's blocks are in neither the
 * card nor ``card_snapshot``; they exist only in ``run.call_snapshots``.
 * ``findBlockById`` over the card root therefore returns null for a block
 * inside a called card, and every label derived from it degraded silently
 * to the raw id — which is how the recovery banner came to read
 * "↻ Retry b-cf96c4e2".  That fallback was the visible tell for a much
 * larger defect: the resume request itself 404'd, because the server
 * searched the same tree.
 *
 * The card's own tree wins.  A Call block's id is a KEY of
 * ``call_snapshots`` and never a node inside one, so the id spaces are
 * disjoint today; the precedence is asserted anyway so a future change
 * that inlines callees cannot silently resolve a caller block to a
 * callee's.
 */
export function findBlockInRun(
  root: Block | undefined | null,
  callSnapshots: Record<string, { root?: Block }> | undefined | null,
  id: string,
): Block | null {
  const own = findBlockById(root, id);
  if (own) return own;
  for (const snap of Object.values(callSnapshots ?? {})) {
    const found = findBlockById(snap?.root, id);
    if (found) return found;
  }
  return null;
}

/**
 * Glyph per block status, shared by TaskRunMap and BlockOutline so the
 * two renderers of the same run cannot show a block two different ways.
 * `held` has its own entry: a `?? '○'` fallback painted the faulting
 * block identically to a queued one, flattening the backend's held
 * status back into "hasn't started yet".
 */
export const STATUS_GLYPHS: Record<string, string> = {
  queued: '○', running: '●', done: '✓',
  failed: '✗', cancelled: '◼', skipped: '⤼', held: '⏸',
};

export interface FoldedRow extends MapRow {
  /** Some later row is nested under this one. */
  hasChildren: boolean;
  /** This row is collapsed and its descendants are omitted. */
  collapsed: boolean;
  /** How many rows a collapsed row is hiding (0 when expanded). */
  hiddenCount: number;
}

/**
 * Apply a set of collapsed block ids to a flattened row list.
 *
 * Operates on the FLAT list rather than re-walking the tree so it
 * composes with flattenBlocks unchanged — including the invisible
 * group rule (a group's children sit at the group's own depth) and the
 * call-snapshot splice.  "Descendant" is therefore purely positional:
 * every following row with a greater depth, until one at the same or
 * a shallower depth.  Collapsing a leaf is a no-op rather than an
 * error, so a stale id in the set (block deleted) is harmless.
 */
export function foldRows(
  rows: MapRow[], collapsed: ReadonlySet<string>,
): FoldedRow[] {
  const out: FoldedRow[] = [];
  let i = 0;
  while (i < rows.length) {
    const row = rows[i];
    let end = i + 1;
    while (end < rows.length && rows[end].depth > row.depth) end++;
    const childCount = end - i - 1;
    const isCollapsed = childCount > 0 && collapsed.has(row.block.id);
    out.push({
      ...row,
      hasChildren: childCount > 0,
      collapsed: isCollapsed,
      hiddenCount: isCollapsed ? childCount : 0,
    });
    i = isCollapsed ? end : i + 1;
  }
  return out;
}

/**
 * First row the outline can select: one whose block has a persisted id.
 * A freshly created card's inner block may still have id '' until the
 * server assigns one on save; selecting it would make every by-id tree
 * edit ambiguous (the root group's id is also '' in that state), so the
 * outline treats such rows as visible but not selectable.
 */
export function firstSelectableRow(rows: MapRow[]): MapRow | null {
  return rows.find(r => r.block.id !== '') ?? null;
}
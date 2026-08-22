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
): MapRow[] {
  if (!root) return [];
  const rows: MapRow[] = [];
  if (root.block_type === 'group') {
    for (const child of root.body ?? []) {
      rows.push(...flattenBlocks(child, depth, callSnapshots, seen));
    }
    return rows;
  }
  rows.push({ block: root, depth });
  for (const child of root.body ?? []) {
    rows.push(...flattenBlocks(child, depth + 1, callSnapshots, seen));
  }
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
 *   3. 'queued'
 * Terminal backstop: once the run itself is terminal nothing can
 * still be running — a stale 'running' degrades to the run's own
 * terminal status (covers dropped terminal events).
 */
export function resolveBlockStatus(
  blockId: string,
  liveStatuses: Record<string, string>,
  run: TaskRun | null,
): BlockStatus {
  const live = liveStatuses[blockId];
  const persisted = run?.block_states?.[blockId]?.status;
  let status = (live ?? persisted ?? 'queued') as BlockStatus;
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
  }>;
  /** Count of older iterations collapsed out of view. */
  overflow: number;
  total: number;
  /** True when the loop is mid-iteration (renders a pulsing dot). */
  running: boolean;
}

export function buildDots(
  summaries: IterationSummary[] | undefined,
  blockRunning: boolean,
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
    })),
    overflow: total - shown.length,
    total,
    running: blockRunning,
  };
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

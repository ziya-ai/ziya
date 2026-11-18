/**
 * RunThreadPane — the inline tile's run view.
 *
 * Two columns: the card (the tile's run map, passed in as ``card``) and
 * the transcript of one block.  With no selection the transcript follows
 * the run; a click on a map row or an iteration dot selects, and "follow
 * run" goes back.  The card column can be put away to a rail of status
 * glyphs, remembered per tile.
 *
 * Selection is owned by the tile (its existing ``focus`` state), so the
 * map, this pane and the block detail panel agree on what is selected.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import type { Artifact, Block } from '../../types/task_card';
import type { SteerNote, TaskRun } from '../../types/task_run';
import type { LiveTaskState } from '../../hooks/useTaskRunStream';
import { getIterationArtifact } from '../../services/taskRunApi';
import {
  buildThread, defaultFocus, focusFor, type ThreadFocus,
} from './deckRunPaneModel';
import { blockLabel, findBlockInRun, STATUS_GLYPHS } from './runMapModel';
import { RunTranscript } from './TranscriptView';
import { SteerComposer, type SteerTarget } from './SteerComposer';
import './run-transcript.css';

interface Selection {
  blockId: string;
  index: number | null;
  passKey?: string | null;
}

interface Props {
  projectId: string;
  root: Block | null | undefined;
  run: TaskRun;
  live: LiveTaskState;
  isLive: boolean;
  /** The card column's content: the tile's run map. */
  card: React.ReactNode;
  /** False for a single-block card, which has no map to show. */
  hasCard: boolean;
  /** The tile's selection; null follows the run. */
  focus: Selection | null;
  onFocus: (blockId: string, index: number | null, passKey?: string | null) => void;
  onFollow: () => void;
  /** localStorage key for "the card is put away". */
  storageKey: string;
  steer?: {
    targets: SteerTarget[];
    notes?: SteerNote[];
    busy?: boolean;
    onSend: (target: SteerTarget, text: string, hold: boolean) => void;
  };
}

function readAway(key: string): boolean {
  try { return window.localStorage.getItem(key) === '1'; } catch { return false; }
}

export const RunThreadPane: React.FC<Props> = ({
  projectId, root, run, live, isLive, card, hasCard,
  focus, onFocus, onFollow, storageKey, steer,
}) => {
  const [cardAway, setCardAwayState] = useState<boolean>(() => readAway(storageKey));
  const setCardAway = useCallback((away: boolean) => {
    setCardAwayState(away);
    try { window.localStorage.setItem(storageKey, away ? '1' : '0'); } catch { /* private mode */ }
  }, [storageKey]);

  const sections = useMemo(
    () => buildThread(root, run, live.blockStatuses),
    [root, run, live.blockStatuses]);

  const runningByBlock = useMemo(() => {
    const out: Record<string, number[]> = {};
    for (const it of live.iterations) {
      if (it.status === 'running' && it.blockId) {
        (out[it.blockId] ??= []).push(it.index);
      }
    }
    return out;
  }, [live.iterations]);

  // What the transcript shows.  A loop body's output is recorded against
  // the loop and an iteration index, so a selected body row resolves to
  // its loop (focusFor).
  const shown = useMemo<ThreadFocus | null>(() => {
    if (!focus) return defaultFocus(sections, run, runningByBlock);
    if (focus.index != null) {
      return { blockId: focus.blockId, index: focus.index, passKey: focus.passKey ?? null };
    }
    const s = sections.find(x => x.block.id === focus.blockId);
    if (!s) return { blockId: focus.blockId, index: null, passKey: null };
    const at = focusFor(s, run);
    return focusFor(s, run, runningByBlock[at.blockId] ?? []);
  }, [focus, sections, run, runningByBlock]);

  const bucket = useMemo(() => {
    if (!shown) return null;
    for (let i = live.iterations.length - 1; i >= 0; i--) {
      const it = live.iterations[i];
      if (it.blockId === shown.blockId
        && (shown.index == null || it.index === shown.index)) return it;
    }
    return null;
  }, [shown, live.iterations]);

  const streamText = bucket?.streamText
    || (shown && shown.index == null ? live.text[shown.blockId] : '') || '';
  const hasTranscript = !!bucket?.segments?.length || !!streamText;

  // A block with nothing streamed this session shows what the run kept.
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  useEffect(() => {
    setArtifact(null);
    if (!shown || hasTranscript) return;
    if (shown.index == null) {
      setArtifact(run.block_states?.[shown.blockId]?.artifact ?? null);
      return;
    }
    const summary = run.block_states?.[shown.blockId]?.iteration_summaries
      ?.find(s => s.index === shown.index
        && (s.pass_key ?? null) === (shown.passKey ?? null));
    if (!summary?.has_artifact) return;
    let cancelled = false;
    getIterationArtifact(projectId, run.id, shown.blockId, shown.index, shown.passKey)
      .then(a => { if (!cancelled) setArtifact(a); })
      .catch(() => { /* the pane says no output was kept */ });
    return () => { cancelled = true; };
  }, [shown?.blockId, shown?.index, shown?.passKey, run.id, run.status, projectId, hasTranscript]);  // eslint-disable-line react-hooks/exhaustive-deps

  // With the card away the outline no longer says where this is, so the
  // header carries the path.
  const headLabel = useMemo(() => {
    if (!shown) return '';
    const owner = findBlockInRun(root, run.call_snapshots, shown.blockId);
    const parts = [owner ? blockLabel(owner) : shown.blockId];
    if (focus && focus.blockId !== shown.blockId) {
      const sel = findBlockInRun(root, run.call_snapshots, focus.blockId);
      if (sel) parts.push(blockLabel(sel));
    }
    return parts.join(' › ') + (shown.index != null ? ` · #${shown.index}` : '');
  }, [shown, focus, root, run.call_snapshots]);

  const shownStatus = shown
    ? (sections.find(s => s.block.id === shown.blockId)?.status ?? 'queued')
    : 'queued';
  const blockLive = isLive && (bucket ? bucket.status === 'running' : shownStatus === 'running');

  // The note composer starts on the block being read.
  const steerTargets = useMemo(() => {
    if (!steer || !shown) return steer?.targets ?? [];
    const mine = (t: SteerTarget) => t.blockId === shown.blockId
      && (shown.index == null || t.index === shown.index);
    return [...steer.targets.filter(mine), ...steer.targets.filter(t => !mine(t))];
  }, [steer, shown]);

  const topLevel = useMemo(() => sections.filter(s => s.depth === 0), [sections]);

  return (
    <div className={`tc-run${hasCard && !cardAway ? '' : ' tc-run--card-away'}`}
         data-testid="run-thread-pane">
      {hasCard && !cardAway && (
        <div className="tc-run__card">
          <div className="tc-run__card-head">
            <span>CARD</span>
            <button type="button" className="tc-run__toggle"
                    title="Put the card away" onClick={() => setCardAway(true)}>«</button>
          </div>
          {card}
        </div>
      )}
      {hasCard && cardAway && (
        <div className="tc-run__rail">
          <button type="button" className="tc-run__toggle"
                  title="Show the card" onClick={() => setCardAway(false)}>»</button>
          {topLevel.map(s => (
            <button
              key={s.block.id}
              type="button"
              data-testid="run-rail-glyph"
              className={
                `tc-run__rail-glyph tc-run__rail-glyph--${s.status}`
                + (focus?.blockId === s.block.id ? ' tc-run__rail-glyph--selected' : '')
              }
              title={blockLabel(s.block)}
              onClick={() => onFocus(s.block.id, null)}
            >
              {STATUS_GLYPHS[s.status] ?? '○'}
            </button>
          ))}
          <span className="tc-run__rail-label">CARD</span>
        </div>
      )}

      <div className="tc-run__pane">
        <div className="tc-run__pane-head" data-testid="run-pane-head">
          <span className={`tc-run__pane-glyph tc-run__pane-glyph--${shownStatus}`}>
            {STATUS_GLYPHS[shownStatus] ?? '○'}
          </span>
          <span className="tc-run__pane-label">{headLabel || 'Run'}</span>
          {focus && isLive && (
            <button type="button" className="tc-run__follow"
                    title="Go back to following whichever block is running"
                    onClick={onFollow}>follow run</button>
          )}
        </div>
        <div className="tc-run__body">
          {hasTranscript ? (
            <RunTranscript
              segments={bucket?.segments}
              fallbackText={streamText}
              live={blockLive}
            />
          ) : artifact?.summary ? (
            <RunTranscript fallbackText={artifact.summary} live={false} />
          ) : (
            <div className="tc-run__empty">
              {!shown
                ? 'Nothing has run yet.'
                : shownStatus === 'queued'
                ? 'Not reached.'
                : shownStatus === 'running'
                ? 'Waiting for output…'
                : 'No output was kept for this block.'}
            </div>
          )}
        </div>
        {steer && isLive && (
          <SteerComposer
            targets={steerTargets} notes={steer.notes}
            busy={steer.busy} onSend={steer.onSend}
          />
        )}
      </div>
    </div>
  );
};

export default RunThreadPane;

/**
 * DeckRunPane — a card's run, shown in the Task Cards dialog as a thread.
 *
 * One section per block.  Finished blocks are one-line dividers, a loop
 * is a strip of dots, and only the selected block's output is open, so
 * the pane's length follows what is being read and not how long the run
 * has been going.  A stopped run's recovery controls sit in the thread
 * at the block it stopped on.
 *
 * Driven by run id, not by a conversation binding: the same stream hook,
 * control derivation, recovery banner and steer composer the inline tile
 * uses, so the two surfaces report one run the same way.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Modal, Select, Spin, Tooltip } from 'antd';
import type { Block, Artifact } from '../../types/task_card';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { TASK_BINDING_EVENT } from '../../hooks/useTaskBindings';
import {
  answerTaskRunAsk, cancelTaskRun, getIterationArtifact, pauseTaskRun, resumeRunFromBlock,
  resumeTaskRun, steerTaskRun, stepTaskRun, type ResumeMode,
} from '../../services/taskRunApi';
import { deriveRunControls } from './runControls';
import { recoveryTarget, bankedIterationPrefix } from './recoveryTarget';
import { RunRecoveryBanner } from './RunRecoveryBanner';
import { AskAnswerPanel } from './AskAnswerPanel';
import { deriveHoldChain } from './holdChain';
import { IterationDotStrip } from './IterationDotStrip';
import { SteerComposer, type SteerTarget } from './SteerComposer';
import { TaskMarkdown } from './TaskMarkdown';
import { RunTranscript } from './TranscriptView';
import { stripTaskMetaTags } from './completionCheck';
import { formatLastActivity } from './liveActivity';
import { RUN_STATUS_FG, RUN_STATUS_LABEL } from './runStatusVocabulary';
import {
  blockLabel, buildDotPasses, findBlockInRun, STATUS_GLYPHS,
} from './runMapModel';
import {
  buildThread, defaultEntryId, defaultFocus, focusFor, formatElapsed,
  outlineMarkers, sameFocus, type DeckRunEntry, type OutlineMarker,
  type ThreadFocus, type ThreadSection,
} from './deckRunPaneModel';
import { DeckPaneTab } from './DeckPaneChrome';
import './task-card-inline-tile.css';
import './task-deck.css';

interface Props {
  projectId: string;
  /** Selected card id; a change resets which run is shown. */
  cardId: string | null;
  /** The card's live root, for a run recorded without a snapshot. */
  cardRoot: Block | null | undefined;
  entries: DeckRunEntry[];
  /** Card id -> name, to name the caller of a callee run. */
  cardNames: Record<string, string>;
  /** Collapse this pane; absent when it cannot be collapsed. */
  onCollapse?: () => void;
  onOpenConversation: (conversationId: string) => void;
  /** A run was created or changed state from here. */
  onRunsChanged: () => void;
  /** The shown run's per-block markers, for the definition outline. */
  onMarkersChange?: (markers: Record<string, OutlineMarker> | null) => void;
  /** Open and scroll to this block's section (outline selection). */
  revealBlock?: { blockId: string; seq: number } | null;
  /** The user opened a section; the definition can select its block. */
  onSectionSelect?: (blockId: string) => void;
}

const entryLabel = (e: DeckRunEntry, names: Record<string, string>): string => {
  const parts = [RUN_STATUS_LABEL[e.status as keyof typeof RUN_STATUS_LABEL] ?? e.status];
  if (e.callerCardId) parts.push(`inside ${names[e.callerCardId] ?? e.callerCardId}`);
  if ((e.attempt ?? 1) > 1) parts.push(`attempt ${e.attempt}`);
  if (e.at) parts.push(formatLastActivity(e.at).label);
  return parts.join(' · ');
};

export const DeckRunPane: React.FC<Props> = ({
  projectId, cardId, cardRoot, entries, cardNames,
  onCollapse, onOpenConversation, onRunsChanged,
  onMarkersChange, revealBlock, onSectionSelect,
}) => {
  const [runId, setRunId] = useState<string | null>(() => defaultEntryId(entries));
  // A different card starts from its own default run.
  useEffect(() => {
    setRunId(defaultEntryId(entries));
    setFocus(null);
    setUserFocused(false);
    setArtifact(null);
    setError(null);
  }, [cardId]);  // eslint-disable-line react-hooks/exhaustive-deps
  // Entries arrive after the card does (the callee lookup is a request).
  useEffect(() => {
    if (runId == null && entries.length > 0) setRunId(defaultEntryId(entries));
  }, [entries, runId]);

  const { run: streamed, live, refresh, clearLive } =
    useTaskRunStream(projectId, runId ?? undefined);
  // The hook keeps the previous run until the new one loads.
  const run = streamed && streamed.id === runId ? streamed : null;
  const entry = entries.find(e => e.runId === runId) ?? null;

  const [focus, setFocus] = useState<ThreadFocus | null>(null);
  const [userFocused, setUserFocused] = useState(false);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [passesOpen, setPassesOpen] = useState<Record<string, boolean>>({});

  const selectRun = useCallback((id: string) => {
    setRunId(id);
    clearLive();
    setFocus(null);
    setUserFocused(false);
    setArtifact(null);
    setError(null);
  }, [clearLive]);

  // This card's tree as the run saw it.  For a callee run that is the
  // tree the caller recorded: the run's own snapshot is the CALLER's
  // card, which is not the card selected in the dialog.
  const root = entry?.calleeRoot ?? run?.card_snapshot?.root ?? cardRoot ?? null;

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

  // Markers for the definition outline.  Cleared on unmount so the
  // outline never shows a run the dialog no longer has open.
  const chain = useMemo(() => deriveHoldChain(run, root), [run, root]);
  const markers = useMemo(
    () => (run ? outlineMarkers(sections, chain) : null), [run, sections, chain]);
  useEffect(() => { onMarkersChange?.(markers); }, [markers, onMarkersChange]);
  useEffect(() => () => onMarkersChange?.(null), [onMarkersChange]);

  // Follow the run until the user picks a section themselves.
  useEffect(() => {
    if (userFocused || !run) return;
    const next = defaultFocus(sections, run, runningByBlock);
    setFocus(prev => (sameFocus(prev, next) || (!prev && !next) ? prev : next));
  }, [sections, run, runningByBlock, userFocused]);

  const controls = deriveRunControls(run);
  const isLive = run != null && !controls.isTerminal;
  const target = controls.canResumeFromBlock ? recoveryTarget(run) : null;
  const targetBlock = target && run
    ? findBlockInRun(root, run.call_snapshots, target.blockId) : null;
  /** Section the recovery banner sits under; null = top of the thread. */
  const bannerAt = useMemo(() => {
    if (!target) return null;
    const s = sections.find(x => x.block.id === target.blockId);
    return s ? focusFor(s, run).blockId : null;
  }, [target, sections, run]);

  // ── Output of the focused section ──
  const bucket = useMemo(() => {
    if (!focus) return null;
    for (let i = live.iterations.length - 1; i >= 0; i--) {
      const it = live.iterations[i];
      if (it.blockId === focus.blockId
        && (focus.index == null || it.index === focus.index)) return it;
    }
    return null;
  }, [focus, live.iterations]);

  const streamText = bucket?.streamText
    || (focus && focus.index == null ? live.text[focus.blockId] : '') || '';

  useEffect(() => {
    setArtifact(null);
    if (!focus || !run || streamText) return;
    if (focus.index == null) {
      setArtifact(run.block_states?.[focus.blockId]?.artifact ?? null);
      return;
    }
    const summary = run.block_states?.[focus.blockId]?.iteration_summaries
      ?.find(s => s.index === focus.index
        && (s.pass_key ?? null) === (focus.passKey ?? null));
    if (!summary?.has_artifact) return;
    let cancelled = false;
    getIterationArtifact(
      projectId, run.id, focus.blockId, focus.index, focus.passKey)
      .then(a => { if (!cancelled) setArtifact(a); })
      .catch(() => { /* the section says no output was kept */ });
    return () => { cancelled = true; };
  }, [focus, run?.id, run?.status, projectId, !!streamText]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ── Actions ──
  const act = useCallback(async (name: string, fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(name);
    setError(null);
    try {
      await fn();
      await refresh();
      onRunsChanged();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }, [busy, refresh, onRunsChanged]);

  const resumeFrom = useCallback(async (blockId: string, mode: ResumeMode) => {
    if (!run || busy) return;
    // A retry executes the block again and discards what it recorded,
    // including files it wrote outside the run record.  Same gate as the
    // inline tile; 'continue' re-executes nothing and needs none.
    const st = run.block_states?.[blockId];
    if (mode === 'retry' && st?.artifact && st.block_type !== 'parallel') {
      const ok = await new Promise<boolean>(resolve => Modal.confirm({
        title: 'Re-run this block?',
        content: 'This block already produced a result. Re-running executes it '
          + 'again and discards that result, including any files it wrote '
          + 'outside the run record. To keep it, use Continue.',
        okText: 'Re-run', okButtonProps: { danger: true },
        onOk: () => resolve(true), onCancel: () => resolve(false),
      }));
      if (!ok) return;
    }
    setBusy('resume');
    setError(null);
    try {
      const res = await resumeRunFromBlock(projectId, run.id, blockId, mode);
      selectRun(res.run.id);
      if (res.binding) {
        window.dispatchEvent(new CustomEvent(TASK_BINDING_EVENT, {
          detail: { bindingId: res.binding.id, runId: res.run.id },
        }));
      }
      onRunsChanged();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }, [run, busy, projectId, selectRun, onRunsChanged]);

  const steerTargets = useMemo<SteerTarget[]>(() => {
    if (!isLive || !run) return [];
    return live.iterations
      .filter(it => it.status === 'running' && it.blockId)
      .map(it => {
        const b = findBlockInRun(root, run.call_snapshots, it.blockId!);
        const name = b ? blockLabel(b) : it.blockId!;
        const isLoop = b?.block_type === 'repeat' || b?.block_type === 'until';
        return {
          blockId: it.blockId!, index: it.index,
          label: isLoop ? `${name} · #${it.index}` : name,
        };
      });
  }, [isLive, run, root, live.iterations]);

  const onFocusDot = useCallback((
    blockId: string, index: number | null, passKey: string | null = null,
  ) => {
    setUserFocused(true);
    setFocus({ blockId, index, passKey });
  }, []);

  const focusSection = useCallback((s: ThreadSection) => {
    setUserFocused(true);
    setFocus(focusFor(s, run, runningByBlock[focusFor(s, run).blockId] ?? []));
  }, [run, runningByBlock]);

  const openSection = useCallback((s: ThreadSection) => {
    focusSection(s);
    onSectionSelect?.(s.block.id);
  }, [focusSection, onSectionSelect]);

  // Outline selection opens the block's section and scrolls to it.  Not
  // reported back through onSectionSelect, so the two do not ping-pong.
  const threadRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!revealBlock || !run) return;
    const s = sections.find(x => x.block.id === revealBlock.blockId);
    if (!s) return;
    focusSection(s);
    const at = focusFor(s, run).blockId;
    const sel = `[data-block-id="${typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(at) : at}"]`;
    const el = threadRef.current?.querySelector(sel) as HTMLElement | null;
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [revealBlock]);  // eslint-disable-line react-hooks/exhaustive-deps

  const banner = target && run ? (
    <RunRecoveryBanner
      run={run}
      target={target}
      targetLabel={targetBlock ? blockLabel(targetBlock) : target.blockId}
      resumeAtIteration={bankedIterationPrefix(run, targetBlock)}
      onRetry={id => void resumeFrom(id, 'retry')}
      onContinue={controls.canContinueFromBlock
        ? id => void resumeFrom(id, 'continue') : undefined}
      busy={busy != null}
    />
  ) : null;

  const convId = run?.source_conversation_id ?? entry?.conversationId ?? null;
  const statusColor = run
    ? RUN_STATUS_FG[run.status as keyof typeof RUN_STATUS_FG] : undefined;

  return (
    <div className="tc-deck__run" data-testid="deck-run-pane">
      <div className="tc-deck__pane-head">
        <Select
          size="small"
          className="tc-deck__run-picker"
          value={runId ?? undefined}
          onChange={selectRun}
          popupMatchSelectWidth={false}
          options={[
            ...entries.map(e => ({ value: e.runId, label: entryLabel(e, cardNames) })),
            // A resume started here is selected before the list catches up.
            ...(runId && !entry
              ? [{ value: runId, label: run ? `${run.status} · new attempt` : 'loading…' }]
              : []),
          ]}
        />
        <span className="tc-deck__run-count">
          {entries.length > 1 ? `${entries.length} runs` : ''}
        </span>
        <span className="tc-deck__spacer" />
        {controls.canPause && (
          <button className="tc-deck__btn" disabled={busy != null}
            onClick={() => void act('pause', () => pauseTaskRun(projectId, run!.id))}>
            Pause
          </button>
        )}
        {controls.canResume && (
          <button className="tc-deck__btn tc-deck__btn--primary" disabled={busy != null}
            onClick={() => void act('resume', () => resumeTaskRun(projectId, run!.id))}>
            Resume
          </button>
        )}
        {controls.canStep && (
          <Tooltip title="Advance one block, then hold">
            <button className="tc-deck__btn" disabled={busy != null || controls.isSettling}
              onClick={() => void act('step', () => stepTaskRun(projectId, run!.id, 1))}>
              Step
            </button>
          </Tooltip>
        )}
        {controls.canCancel && (
          <button className="tc-deck__btn tc-deck__btn--danger" disabled={busy != null}
            onClick={() => void act('cancel', () => cancelTaskRun(projectId, run!.id))}>
            Cancel
          </button>
        )}
        {convId && (
          <Tooltip title="Open the conversation this run is anchored in">
            <button className="tc-deck__btn" onClick={() => onOpenConversation(convId)}>
              ↗
            </button>
          </Tooltip>
        )}
        {onCollapse && (
          <DeckPaneTab label="RUN" side="right" onCollapse={onCollapse}
            tone={run?.status === 'held' ? 'held' : 'plain'}
            title="Collapse the run — the definition takes the space" />
        )}
      </div>

      <div className="tc-thread" ref={threadRef}>
        {!run && <div className="tc-thread__empty"><Spin size="small" /> Loading run…</div>}
        {error && (
          <div className="tc-thread__error" role="alert">
            {error}
            <button className="tc-deck__btn" onClick={() => setError(null)}>dismiss</button>
          </div>
        )}
        {/* Same gate as the inline tile: a held run can still hold an
            unanswered Ask, and an answer already recorded is not re-asked. */}
        {run && (run.status === 'awaiting_input' || run.status === 'held')
          && run.pending_ask && !run.ask_answers?.[run.pending_ask.block_id] && (
          <AskAnswerPanel
            pending={run.pending_ask}
            busy={busy === 'answer'}
            onAnswer={(decision, answer) => void act('answer', () => answerTaskRunAsk(
              projectId, run.id, run.pending_ask!.block_id, { decision, answer }))}
          />
        )}
        {/* A stop that belongs to no block of this tree (e.g. a hold in a
            sibling card of the same study) still gets its controls. */}
        {banner && bannerAt == null && banner}
        {run && sections.length === 0 && (
          <div className="tc-thread__empty">This run recorded no blocks for this card.</div>
        )}
        {run && sections.map(s => {
          const here = focusFor(s, run).blockId;
          const open = !!focus && focus.blockId === here && here === s.block.id;
          const st = run.block_states?.[s.block.id];
          const glyph = STATUS_GLYPHS[s.status] ?? '○';
          return (
            <div key={`${s.viaCall ?? ''}:${s.block.id}`}
              data-block-id={s.block.id}
              className={`tc-thread__sec tc-thread__sec--${s.status}${open ? ' tc-thread__sec--open' : ''}`}
              style={{ marginLeft: s.depth * 14 }}>
              <button type="button" className="tc-thread__div"
                onClick={() => openSection(s)}
                title="Show this block's output">
                <span className="tc-thread__glyph">{glyph}</span>
                <span className="tc-thread__label">{blockLabel(s.block)}</span>
                {s.durationMs != null && (
                  <span className="tc-thread__meta">· {formatElapsed(s.durationMs)}</span>
                )}
                {s.viaCall && <span className="tc-thread__meta">· called</span>}
                <span className="tc-thread__rule" />
                {s.status === 'held' && <span className="tc-thread__held">held</span>}
              </button>
              {s.isLoop && (st?.iteration_summaries?.length || s.status === 'running') ? (
                <div className="tc-thread__dots">
                  <IterationDotStrip
                    blockId={s.block.id}
                    passes={buildDotPasses(
                      st?.iteration_summaries, s.status === 'running',
                      runningByBlock[s.block.id] ?? [])}
                    planned={st?.planned_iterations}
                    focusedId={focus?.blockId ?? null}
                    focusedIndex={focus?.index ?? null}
                    focusedPassKey={focus?.passKey ?? null}
                    onFocus={onFocusDot}
                    expanded={!!passesOpen[s.block.id]}
                    onToggleExpanded={() => setPassesOpen(p => (
                      { ...p, [s.block.id]: !p[s.block.id] }))}
                  />
                  {open && focus?.index != null && (
                    <span className="tc-thread__meta">showing #{focus.index}</span>
                  )}
                </div>
              ) : null}
              {open && (
                <div className="tc-thread__turn">
                  {(bucket?.segments?.length || streamText) ? (
                    <RunTranscript
                      segments={bucket?.segments}
                      fallbackText={streamText}
                      live={isLive && bucket?.status === 'running'}
                    />
                  ) : artifact?.summary ? (
                    <TaskMarkdown
                      markdown={stripTaskMetaTags(artifact.summary)}
                      enableCodeApply={false}
                      breaks={true}
                      isStreaming={false}
                      isSubRender={true}
                    />
                  ) : (
                    <div className="tc-thread__empty">
                      {s.status === 'queued'
                        ? 'Not reached.'
                        : 'No output was kept for this block.'}
                    </div>
                  )}
                  {st?.error && <div className="tc-thread__error">{st.error}</div>}
                </div>
              )}
              {banner && bannerAt === s.block.id && banner}
            </div>
          );
        })}
      </div>

      <div className="tc-deck__composer">
        {steerTargets.length > 0 ? (
          <SteerComposer
            targets={steerTargets}
            notes={run?.steer_notes}
            busy={busy === 'steer'}
            onSend={(t, text, hold) => void act('steer', () => steerTaskRun(
              projectId, run!.id,
              { block_id: t.blockId, index: t.index, text, hold }))}
          />
        ) : (
          <div className="tc-deck__composer-off">
            {isLive
              ? 'No block is taking notes right now.'
              : 'This run is not executing, so there is no block to send a note to.'}
          </div>
        )}
        {run && (
          <div className="tc-deck__stats" style={statusColor ? { borderColor: statusColor } : undefined}>
            {(run.total_tokens ?? 0).toLocaleString()} tokens · {run.total_tool_calls ?? 0} tool calls
          </div>
        )}
      </div>
    </div>
  );
};

export default DeckRunPane;

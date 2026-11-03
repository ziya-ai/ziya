/**
 * SteerComposer — say something to a RUNNING block.
 *
 * Sits under the Inspector's Live output tab (the one place a block's
 * transcript already streams, for every card shape including a bare
 * single Task with no run map).  A note is delivered as a user turn
 * before the target's next model round, so the wording promises exactly
 * that and no more: not "sent", but "lands before its next model round".
 *
 * "Send & hold" is Send plus the existing Step semantics — the run holds
 * at the next block boundary so the operator can read the reaction
 * before the run moves on.  It is not a third control mechanism.
 *
 * Targets are the live iteration buckets the inspector already shows.
 * The frontend cannot tell a loop iteration from a bare task's synthetic
 * index 0, so ``index`` is always sent and the SERVER decides whether it
 * applies (app/utils/task_steer.resolve_steer_target).
 */

import React, { useCallback, useEffect, useState } from 'react';
import type { SteerNote } from '../../types/task_run';

export interface SteerTarget {
  blockId: string;
  index: number;
  label: string;
}

interface Props {
  targets: SteerTarget[];
  notes?: SteerNote[];
  busy?: boolean;
  onSend: (target: SteerTarget, text: string, hold: boolean) => void;
}

const statusLabel: Record<SteerNote['status'], string> = {
  queued: 'queued — lands before the next model round',
  delivered: 'delivered ✓',
  expired: 'expired — the block finished first',
};

export const SteerComposer: React.FC<Props> = ({
  targets, notes = [], busy = false, onSend,
}) => {
  const [text, setText] = useState('');
  const [targetKey, setTargetKey] = useState<string>('');

  const keyOf = (t: SteerTarget) => `${t.blockId}:${t.index}`;

  // Keep the selection valid as iterations start and finish under it.
  useEffect(() => {
    if (targets.length === 0) { setTargetKey(''); return; }
    if (!targets.some(t => keyOf(t) === targetKey)) {
      setTargetKey(keyOf(targets[0]));
    }
  }, [targets, targetKey]);

  const target = targets.find(t => keyOf(t) === targetKey) ?? targets[0];

  const submit = useCallback((hold: boolean) => {
    const body = text.trim();
    if (!body || !target || busy) return;
    onSend(target, body, hold);
    setText('');
  }, [text, target, busy, onSend]);

  if (targets.length === 0) return null;

  // Notes for the selected target only, newest last, so "did it see my
  // note" is answered next to where the next one is typed.
  const mine = notes.filter(n =>
    n.block_id === target.blockId
    && (n.index ?? 0) === target.index);

  return (
    <div className="tc-steer" data-testid="steer-composer">
      {mine.length > 0 && (
        <ul className="tc-steer__notes">
          {mine.slice(-5).map(n => (
            <li key={n.id} className={`tc-steer__note tc-steer__note--${n.status}`}>
              <span className="tc-steer__note-text">{n.text}</span>
              <span className="tc-steer__note-status">{statusLabel[n.status]}</span>
            </li>
          ))}
        </ul>
      )}
      <textarea
        className="tc-steer__input"
        rows={2}
        value={text}
        disabled={busy}
        placeholder={`Say something to ${target.label}… lands before its next model round`}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit(false);
          }
        }}
      />
      <div className="tc-steer__actions">
        {targets.length > 1 && (
          <select
            className="tc-steer__target"
            value={targetKey}
            onChange={e => setTargetKey(e.target.value)}
            title="Which live block or iteration receives the note"
          >
            {targets.map(t => (
              <option key={keyOf(t)} value={keyOf(t)}>{t.label}</option>
            ))}
          </select>
        )}
        <button
          className="tc-steer__send"
          disabled={busy || !text.trim()}
          onClick={() => submit(false)}
          title="Deliver before the block's next model round (⌘↵)"
        >
          Send
        </button>
        <button
          className="tc-steer__send tc-steer__send--hold"
          disabled={busy || !text.trim()}
          onClick={() => submit(true)}
          title="Deliver the note, then hold the run at the next block boundary so you can read the reaction"
        >
          Send &amp; hold
        </button>
        <span className="tc-steer__hint">⌘↵</span>
      </div>
    </div>
  );
};

export default SteerComposer;

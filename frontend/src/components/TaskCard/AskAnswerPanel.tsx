/**
 * AskAnswerPanel — the human-in-the-loop control that answers the Ask
 * checkpoint a run is holding at.
 *
 * Why it exists: the Ask block landed backend-complete (executor holds
 * at 'awaiting_input', storage records the answer, POST .../ask/{block}
 * accepts it) but with NO way to answer from the browser.  A run that
 * reached an Ask block was stuck until answered by a raw HTTP call.
 * This is that control.
 *
 * Two answer shapes, from the backend semantics (block_executor
 * ::_execute_ask):
 *   - decision 'approve' → the run continues; ``answer`` is bound to the
 *     block's ask_variable and threaded into later blocks as context.
 *   - decision 'reject'  → a failed artifact, so the enclosing
 *     on_failure governs (stop halts, continue proceeds with the
 *     rejection recorded).  There is no branch primitive; reject IS the
 *     "no" path.
 *
 * When the checkpoint declares ``choices``, each choice is an approve
 * with that answer — the common "pick one and continue" case — and
 * Reject remains the separate abort.  With no choices, a free-text field
 * feeds both Approve (as the value) and Reject (as the reason).
 */

import React, { useState } from 'react';
import { Button, Input } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import type { PendingAsk } from '../../types/task_run';

interface Props {
  pending: PendingAsk;
  /** True while an answer request is in flight. */
  busy?: boolean;
  onAnswer: (decision: 'approve' | 'reject', answer: string) => void;
}

export const AskAnswerPanel: React.FC<Props> = ({ pending, busy = false, onAnswer }) => {
  const [text, setText] = useState('');
  const choices = pending.choices ?? [];
  const hasChoices = choices.length > 0;

  return (
    <div
      className="tc-ask-panel"
      role="group"
      aria-label="Task checkpoint — awaiting your answer"
    >
      <div className="tc-ask-panel__q">
        <span aria-hidden className="tc-ask-panel__icon">?</span>
        <span className="tc-ask-panel__question">
          {pending.question?.trim() || 'This task is waiting for your input to continue.'}
        </span>
      </div>

      {hasChoices ? (
        <div className="tc-ask-panel__choices">
          {choices.map((c) => (
            <Button
              key={c}
              size="small"
              type="primary"
              disabled={busy}
              onClick={() => onAnswer('approve', c)}
            >
              {c}
            </Button>
          ))}
          {/* Reject stays available even with fixed choices: the choices
              are ways to CONTINUE, and a human must always be able to say
              "do not proceed" instead of being forced to pick one. */}
          <Button
            size="small"
            danger
            icon={<CloseCircleOutlined />}
            disabled={busy}
            onClick={() => onAnswer('reject', '')}
          >
            Reject
          </Button>
        </div>
      ) : (
        <>
          <Input.TextArea
            className="tc-ask-panel__text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Your answer (bound to the block's variable and passed to later steps). Optional for a plain approve; a reason for a reject."
            autoSize={{ minRows: 2, maxRows: 8 }}
            disabled={busy}
          />
          <div className="tc-ask-panel__actions">
            <Button
              type="primary"
              size="small"
              icon={<CheckCircleOutlined />}
              loading={busy}
              onClick={() => onAnswer('approve', text.trim())}
            >
              Approve &amp; continue
            </Button>
            <Button
              danger
              size="small"
              icon={<CloseCircleOutlined />}
              disabled={busy}
              onClick={() => onAnswer('reject', text.trim())}
            >
              Reject
            </Button>
          </div>
        </>
      )}
    </div>
  );
};

export default AskAnswerPanel;

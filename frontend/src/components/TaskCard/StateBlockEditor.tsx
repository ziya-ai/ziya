/**
 * Editor for a State block (teal leaf) — the conversational baseline
 * for setting up a task's assumptions and givens.
 *
 * PRIMARY: a freeform prose "Assumptions / Context" field.  Whatever
 * you write flows into every in-scope task automatically — no {{var}}
 * templating required.  This is how most cards use State: state the
 * givens in plain English ("assume prod, migration already ran").
 *
 * ADJUNCT (under Advanced): named variables read as {{var.NAME}}, for
 * the minority of cases wanting a reusable value referenced by name.
 *
 * Placement is the reset policy (see app/agents/block_executor.py
 * ::_execute_state): at the top of a once-running body it sets once
 * per run; inside a Repeat/Until body it re-applies each iteration,
 * resetting to baseline.  Read-only — nothing writes back, so the
 * sandbox invariant (only artifacts cross task boundaries) holds.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { DragHandle } from './DragContext';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

/** Render a stored value back to an editable string. */
function valueToText(v: unknown): string {
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch { return String(v); }
}

/** Parse an edited string: JSON if it parses, else the raw string. */
function textToValue(s: string): unknown {
  const t = s.trim();
  if (t === '') return '';
  try { return JSON.parse(t); } catch { return s; }
}

export const StateBlockEditor: React.FC<Props> = ({ block, onChange, onDelete, isRoot }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  const prose = block.state_context ?? '';
  // Stable row list from the variables object.  Editing happens against
  // an array of [key, valueText] pairs so a key can be blanked/retyped
  // without losing its row; the object is rebuilt on every change.
  const vars = block.state_variables ?? {};
  const rows = Object.entries(vars).map(([k, v]) => [k, valueToText(v)] as [string, string]);

  const commit = (next: [string, string][]) => {
    const obj: Record<string, unknown> = {};
    for (const [k, vText] of next) {
      const key = k.trim();
      if (!key) continue;             // skip blank keys (row kept in UI via local state below)
      obj[key] = textToValue(vText);
    }
    update({ state_variables: obj });
  };

  const setRow = (idx: number, key: string, vText: string) => {
    const next = rows.slice();
    next[idx] = [key, vText];
    commit(next);
  };
  const addRow = () => commit([...rows, [`var${rows.length + 1}`, '']]);
  const removeRow = (idx: number) => {
    const next = rows.slice();
    next.splice(idx, 1);
    commit(next);
  };

  return (
    <div className="tc-block tc-block-state">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
        <span className="tc-emoji">📌</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="State name"
        />
        <span className="tc-block-label tc-block-label-state">State</span>
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-state">
        <div className="tc-sequence-label">Assumptions &amp; context (flows into every task here):</div>
        <AutoGrowTextarea
          className="tc-instructions"
          value={prose}
          onChange={e => update({ state_context: e.target.value })}
          placeholder="State the givens in plain English — e.g. &quot;Assume we're deploying to prod, the migration already ran, and the feature flag is off. Don't re-verify these.&quot;"
          minRows={2}
        />
        <details className="tc-state-advanced">
          <summary>▸ Advanced — named variables ({rows.length})</summary>
          <div className="tc-state-vars-hint">
            Reusable values referenced as {'{{var.NAME}}'} in task instructions.
          </div>
        {rows.length === 0 && (
          <div className="tc-state-empty">No variables — most cards only need the prose above.</div>
        )}
        {rows.map(([k, vText], idx) => (
          <div className="tc-state-row" key={idx}>
            <input
              className="tc-state-key"
              value={k}
              onChange={e => setRow(idx, e.target.value, vText)}
              placeholder="name"
              spellCheck={false}
            />
            <span className="tc-state-eq">=</span>
            <input
              className="tc-state-val"
              value={vText}
              onChange={e => setRow(idx, k, e.target.value)}
              placeholder='value (e.g. "prod", 42, true, ["a","b"])'
              spellCheck={false}
            />
            <button
              className="tc-icon-btn tc-icon-btn-delete"
              onClick={() => removeRow(idx)}
              title="Remove variable"
            >×</button>
          </div>
        ))}
        <div className="tc-add-row">
          <button className="tc-add-btn" onClick={addRow}>+ Variable</button>
        </div>
        </details>
        <div className="tc-state-hint">
          Placement is the reset policy: at the top of the card this sets
          once per run; inside a Repeat/Until body it resets to these
          values each iteration.
        </div>
      </div>
    </div>
  );
};

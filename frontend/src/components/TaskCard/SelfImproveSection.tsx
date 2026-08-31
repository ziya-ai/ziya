/**
 * Self-improvement authoring controls, mounted on every container
 * block editor (Repeat / Until / Parallel) and on the card root via
 * TaskCardEditor.  One component so the four mount points cannot
 * drift apart.
 *
 * The knobs mirror app/models/task_card.py:
 *   - self_improve: the toggle.
 *   - improve_criterion: explicit acceptance criterion.  Strongly
 *     recommended — an authored criterion converges far better than
 *     one inferred from the task text.
 *   - improve_max: edits per run.  BLANK → null → backend default
 *     (2).  0 is meaningful and distinct: observe-only, where the
 *     judge runs and records lessons but never edits — the risk-free
 *     trial mode.  Same empty-vs-zero discipline as
 *     repeat_max_concurrency (see repeatConcurrencyControl.test.tsx).
 *   - improve_drift: 'conservative' corrects toward the ask;
 *     'expansive' is the opt-in for growing beyond it.
 *
 * Text-only by construction: nothing here (and nothing the judge
 * does at run time) can change scope — the backend patch path is
 * whitelisted to instructions/state_context on existing block ids.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (patch: Partial<Block>) => void;
}

export const SelfImproveSection: React.FC<Props> = ({ block, onChange }) => {
  const enabled = !!block.self_improve;
  const drift = block.improve_drift ?? 'conservative';
  const observeOnly = block.improve_max === 0;

  return (
    <div className={`tc-improve-section${enabled ? ' tc-improve-section--on' : ''}`}>
      <label className="tc-checkbox-label tc-improve-toggle">
        <input
          type="checkbox"
          checked={enabled}
          onChange={e => onChange({ self_improve: e.target.checked })}
          title="After each run, a judge decides whether a tangible, outcome-affecting text improvement exists. If so, the card's text is revised (never its permissions) and this level restarts. Lessons persist across runs."
        />
        <span className="tc-improve-emoji">🌱</span> Self-improve after each run
      </label>
      {enabled && (
        <div className="tc-improve-controls">
          <AutoGrowTextarea
            className="tc-text-input tc-text-input--multiline tc-improve-criterion"
            placeholder="Acceptance criterion (recommended) — e.g. 'all tests pass and no file outside app/ was touched'"
            value={block.improve_criterion ?? ''}
            onChange={e => onChange({ improve_criterion: e.target.value || null })}
            title="What 'good enough' means for this level. Leave blank to let the judge infer the objective from the task text — authored criteria converge much better."
            minRows={1}
          />
          <div className="tc-improve-row">
            <span className="tc-label-dim">max edits/run</span>
            <input
              type="number" min={0}
              className="tc-num-input"
              placeholder="2"
              value={block.improve_max ?? ''}
              onChange={e => {
                const raw = e.target.value.trim();
                // Blank clears to the backend default (2).  0 is kept:
                // observe-only, NOT "no default" — collapsing them
                // would silently discard the trial mode.
                if (!raw) { onChange({ improve_max: null }); return; }
                const n = parseInt(raw, 10);
                onChange({ improve_max: Number.isNaN(n) ? null : Math.max(0, n) });
              }}
              title="Card edits this level may apply per run. Blank uses the default (2). 0 = observe-only: the judge runs and records lessons, but never edits — a risk-free way to trial self-improvement on an existing card."
            />
            <select
              className="tc-select"
              value={drift}
              onChange={e => onChange({
                improve_drift: e.target.value === 'expansive' ? 'expansive' : null,
              })}
              title="Conservative (default): revisions may only correct toward the stated objective. Expansive: revisions may strengthen the task beyond the original ask when it serves the criterion."
            >
              <option value="conservative">correct toward the ask</option>
              <option value="expansive">may grow beyond the ask</option>
            </select>
          </div>
          {observeOnly && (
            <div className="tc-improve-hint">
              Observe-only: the judge records lessons after each run but
              never edits the card.
            </div>
          )}
        </div>
      )}
    </div>
  );
};

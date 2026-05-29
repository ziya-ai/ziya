/**
 * Editor for a Schedule block — the "outer-outer" trigger decorator.
 *
 * A Schedule wraps any body and fires recurring TaskRuns according
 * to its mode: interval / at / daily_at / cron.  See
 * app/agents/task_scheduler.py for execution semantics.
 *
 * Schedules can nest like any other block.  Nested schedules are
 * legal (they execute as passthrough when run directly) but only the
 * topmost schedule in a card's tree is honored by the fire loop.
 */

import React from 'react';
import type {
  Block, ScheduleMode, IntervalUnit,
} from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import {
  makeTaskBlock, makeRepeatBlock, makeParallelBlock,
  makeUntilBlock, makeScheduleBlock,
} from '../../utils/taskCardBlocks';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
}

export const ScheduleBlockEditor: React.FC<Props> = ({ block, onChange, onDelete }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });
  const updateChild = (idx: number, child: Block) => {
    const body = block.body.slice();
    body[idx] = child;
    update({ body });
  };
  const removeChild = (idx: number) => {
    const body = block.body.slice();
    body.splice(idx, 1);
    update({ body });
  };
  const addChild = (kind: 'task' | 'repeat' | 'parallel' | 'until' | 'schedule') => {
    const child =
      kind === 'task' ? makeTaskBlock() :
      kind === 'repeat' ? makeRepeatBlock() :
      kind === 'parallel' ? makeParallelBlock() :
      kind === 'until' ? makeUntilBlock() :
      makeScheduleBlock();
    update({ body: [...block.body, child] });
  };

  const mode: ScheduleMode = block.schedule_mode ?? 'interval';

  return (
    <div className="tc-block tc-block-schedule">
      <div className="tc-block-header">
        <span className="tc-emoji">⏰</span>
        <span className="tc-block-label tc-block-label-schedule">Schedule</span>
        <select
          className="tc-select"
          value={mode}
          onChange={e => update({ schedule_mode: e.target.value as ScheduleMode })}
        >
          <option value="interval">every N minutes/hours/days</option>
          <option value="at">once at datetime</option>
          <option value="daily_at">daily at HH:MM</option>
          <option value="cron">cron expression</option>
        </select>

        {mode === 'interval' && (
          <>
            <span className="tc-label-dim">every</span>
            <input
              type="number" min={1}
              className="tc-num-input"
              value={block.schedule_interval_value ?? 1}
              onChange={e => update({
                schedule_interval_value: parseInt(e.target.value, 10) || 1,
              })}
            />
            <select
              className="tc-select"
              value={block.schedule_interval_unit ?? 'hours'}
              onChange={e => update({
                schedule_interval_unit: e.target.value as IntervalUnit,
              })}
            >
              <option value="minutes">minutes</option>
              <option value="hours">hours</option>
              <option value="days">days</option>
            </select>
          </>
        )}
        {mode === 'at' && (
          <input
            type="datetime-local"
            className="tc-text-input"
            value={block.schedule_at_iso ?? ''}
            onChange={e => update({ schedule_at_iso: e.target.value || null })}
          />
        )}
        {mode === 'daily_at' && (
          <input
            type="time"
            className="tc-text-input"
            value={block.schedule_daily_at ?? '09:00'}
            onChange={e => update({ schedule_daily_at: e.target.value })}
          />
        )}
        {mode === 'cron' && (
          <input
            type="text"
            className="tc-text-input tc-flex-grow"
            placeholder="*/15 * * * *"
            value={block.schedule_cron ?? ''}
            onChange={e => update({ schedule_cron: e.target.value })}
            title="Standard 5-field cron expression (min hour day month weekday)"
          />
        )}

        <label className="tc-checkbox-label" title="Disable to pause without deleting the schedule">
          <input
            type="checkbox"
            checked={block.schedule_enabled !== false}
            onChange={e => update({ schedule_enabled: e.target.checked })}
          /> enabled
        </label>
        <label className="tc-checkbox-label" title="Run once on recovery if a fire was missed (default cron behavior)">
          <input
            type="checkbox"
            checked={block.schedule_catch_up !== false}
            onChange={e => update({ schedule_catch_up: e.target.checked })}
          /> catch-up
        </label>
        <span className="tc-label-dim">max runs</span>
        <input
          type="number" min={0}
          className="tc-num-input"
          placeholder="∞"
          value={block.schedule_max_runs ?? ''}
          onChange={e => update({
            schedule_max_runs: e.target.value ? parseInt(e.target.value, 10) : null,
          })}
        />
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-schedule">
        <div className="tc-sequence-label">When triggered, run:</div>
        {block.body.map((child, idx) => (
          <BlockEditor
            key={child.id}
            block={child}
            onChange={next => updateChild(idx, next)}
            onDelete={() => removeChild(idx)}
          />
        ))}
        <div className="tc-add-row">
          <button className="tc-add-btn" onClick={() => addChild('task')}>+ Task</button>
          <button className="tc-add-btn" onClick={() => addChild('repeat')}>+ Repeat</button>
          <button className="tc-add-btn" onClick={() => addChild('parallel')}>+ Parallel</button>
          <button className="tc-add-btn" onClick={() => addChild('until')}>+ Until</button>
        </div>
      </div>
    </div>
  );
};

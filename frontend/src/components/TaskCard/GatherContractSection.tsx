/**
 * Gather contract authoring, mounted on the Parallel / Repeat / Until
 * editors.  One component so the mount points cannot drift apart.
 *
 * Mirrors app/models/task_card.py::GatherContract.  Fields the backend
 * declares non-optional (``per``, ``min``, ``rerun_missing``) are DELETED
 * when cleared, never set to null: null would fail validation there,
 * while an absent key takes the backend default.  ``max`` / ``min_units``
 * / ``producer`` are Optional and clear to null.  Unknown keys on an
 * entry (``require_claims``, ``container``) and an inline schema object
 * are carried through untouched.
 */

import React from 'react';
import type { Block, GatherContract } from '../../types/task_card';
import './task-card-editor.css';

type Per = 'child' | 'iteration' | 'total';

interface Props {
  block: Block;
  onChange: (patch: Partial<Block>) => void;
}

/** ``per`` values the validator accepts for this container type. */
export function gatherPerOptions(blockType: string): Per[] {
  return blockType === 'repeat' || blockType === 'until'
    ? ['iteration', 'total'] : ['child', 'total'];
}

/** Units a gather is judged over, when knowable before the run.
 *  Mirrors app/utils/output_contracts.py::gather_unit_count. */
export function gatherUnitCount(block: Block, g: GatherContract): number | null {
  const per = g.per ?? defaultPer(block.block_type);
  if (per === 'child') return (block.body || []).length;
  if (per !== 'iteration') return null;
  if (block.block_type === 'repeat' && (block.repeat_mode ?? 'count') === 'count') {
    return block.repeat_count || null;
  }
  if (block.block_type === 'until') return block.until_max || null;
  return null;
}

function defaultPer(blockType: string): Per {
  return gatherPerOptions(blockType)[0];
}

/** One sentence saying when this entry fails the container. */
export function gatherSummary(block: Block, g: GatherContract): string {
  const per = g.per ?? defaultPer(block.block_type);
  const lo = g.min ?? 1;
  const range = g.max != null ? `${lo}-${g.max}` : `at least ${lo}`;
  const name = g.name || '(unnamed)';
  if (per === 'total') {
    return `Fails unless the whole container emits ${range} valid '${name}' part(s) in total.`;
  }
  const unit = per === 'child' ? 'child' : 'iteration';
  const n = gatherUnitCount(block, g);
  const of = n != null ? ` of ${n}` : '';
  if (g.min_units != null && (n == null || g.min_units < n)) {
    return `Passes when at least ${g.min_units}${of} ${unit} unit(s) each emit ${range} `
      + `valid '${name}' part(s); a short ${unit} is recorded, not failed.`;
  }
  return `Fails unless every ${unit}${of ? ` (${n})` : ''} emits ${range} valid '${name}' part(s).`;
}

const optInt = (raw: string, floor: number): number | null => {
  const s = raw.trim();
  if (!s) return null;
  const n = parseInt(s, 10);
  return Number.isNaN(n) ? null : Math.max(floor, n);
};

export const GatherContractSection: React.FC<Props> = ({ block, onChange }) => {
  const entries: GatherContract[] = block.gather_contract || [];
  const perOptions = gatherPerOptions(block.block_type);
  const canRerun = block.block_type === 'parallel' || block.block_type === 'repeat';

  const write = (next: GatherContract[]) =>
    onChange({ gather_contract: next.length ? next : null });

  // ``undefined`` removes the key so the backend default applies.
  const setField = (i: number, patch: Partial<Record<keyof GatherContract, unknown>>) => {
    const e: Record<string, unknown> = { ...entries[i] };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete e[k]; else e[k] = v;
    }
    write(entries.map((x, j) => (j === i ? (e as unknown as GatherContract) : x)));
  };

  const add = () => write([
    ...entries,
    { name: 'finding', schema: 'ziya:finding/v1', per: defaultPer(block.block_type) },
  ]);

  return (
    <div className={`tc-gather-section${entries.length ? ' tc-gather-section--on' : ''}`}>
      <div className="tc-gather-head">
        <span className="tc-label-dim" title="Outputs this container requires from its units. Checked when the container ends; a shortfall fails it by name.">
          📥 Required outputs{entries.length ? ` (${entries.length})` : ''}
        </span>
        <button type="button" className="tc-icon-btn" onClick={add}
          title="Add a gather contract: an output each child or iteration must emit">+ add</button>
      </div>
      {entries.map((g, i) => {
        const per = g.per ?? defaultPer(block.block_type);
        const inlineSchema = typeof g.schema !== 'string';
        const n = gatherUnitCount(block, g);
        return (
          <div key={i} className="tc-gather-entry">
            <div className="tc-improve-row">
              <input className="tc-text-input tc-gather-name" value={g.name}
                placeholder="part name" title="Part name the units must emit (emit_artifact name)"
                onChange={e => setField(i, { name: e.target.value })} />
              {inlineSchema ? (
                <span className="tc-label-dim" title="Inline JSON Schema - edit it in the card JSON">inline schema</span>
              ) : (
                <input className="tc-text-input tc-gather-schema" value={g.schema as string}
                  placeholder="ziya:finding/v1" title="Schema: ziya:<name>/v<n> or project:<name>"
                  onChange={e => setField(i, { schema: e.target.value })} />
              )}
              <select className="tc-select" value={per} title="Unit the contract is judged over"
                onChange={e => {
                  const next = e.target.value as Per;
                  // min_units and rerun are refused on per 'total'.
                  setField(i, next === 'total'
                    ? { per: next, min_units: undefined, rerun_missing: undefined }
                    : { per: next });
                }}>
                {perOptions.map(p => (
                  <option key={p} value={p}>{p === 'total' ? 'in total' : `per ${p}`}</option>
                ))}
              </select>
              <button type="button" className="tc-icon-btn tc-icon-btn-delete"
                onClick={() => write(entries.filter((_, j) => j !== i))}
                title="Remove this gather contract">×</button>
            </div>
            <div className="tc-improve-row">
              <span className="tc-label-dim">min</span>
              <input type="number" min={0} className="tc-num-input" placeholder="1"
                value={g.min ?? ''} title="Valid parts each unit must emit (blank = 1)"
                onChange={e => setField(i, { min: optInt(e.target.value, 0) ?? undefined })} />
              <span className="tc-label-dim">max</span>
              <input type="number" min={1} className="tc-num-input" placeholder="-"
                value={g.max ?? ''} title="Most valid parts a unit may emit (blank = no limit)"
                onChange={e => setField(i, { max: optInt(e.target.value, 1) })} />
              {per !== 'total' && (
                <>
                  <span className="tc-label-dim">need</span>
                  <input type="number" min={1} max={n ?? undefined} className="tc-num-input"
                    placeholder="all" value={g.min_units ?? ''}
                    title="Quorum: how many units must meet min (blank = every unit). Below the unit count, a silent unit is recorded instead of failing the container."
                    onChange={e => setField(i, { min_units: optInt(e.target.value, 1) })} />
                  <span className="tc-label-dim">{n != null ? `of ${n}` : 'units'}</span>
                </>
              )}
              {canRerun && per !== 'total' && (
                <label className="tc-checkbox-label">
                  <input type="checkbox" checked={!!g.rerun_missing}
                    title="Re-run short units once, told why (only while the quorum is unmet)"
                    onChange={e => setField(i, { rerun_missing: e.target.checked ? 1 : undefined })} />
                  re-run short
                </label>
              )}
              <input className="tc-text-input tc-gather-producer"
                value={(g.producer || []).join(', ')} placeholder="producer ids (if a unit has several tasks)"
                title="Ids of the task(s) inside a unit that must emit; needed when a unit holds more than one task"
                onChange={e => {
                  const ids = e.target.value.split(',').map(s => s.trim()).filter(Boolean);
                  setField(i, { producer: ids.length ? ids : null });
                }} />
            </div>
            <div className="tc-improve-hint">{gatherSummary(block, g)}</div>
          </div>
        );
      })}
    </div>
  );
};

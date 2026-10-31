/**
 * @jest-environment jsdom
 *
 * Gather contract authoring in the deck editor.
 *
 * Before: no editor surface for gather_contract (or min_units) existed;
 * a card could only get one through its JSON or task_card_write.
 *
 * The subtle requirements, all from app/models/task_card.py::GatherContract:
 *   - per / min / rerun_missing are NOT Optional there: clearing one must
 *     delete the key (backend default), never write null (a 422).
 *   - max / min_units / producer ARE Optional: clearing writes null.
 *   - per 'total' refuses min_units and rerun_missing, so switching to it
 *     drops both.
 *   - keys this editor does not show (require_claims, container) and an
 *     inline schema object survive every edit.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render, screen, fireEvent } from '@testing-library/react';

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import type { Block, GatherContract } from '../../../types/task_card';
import {
  GatherContractSection, gatherPerOptions, gatherSummary, gatherUnitCount,
} from '../GatherContractSection';

const task = (id: string): Block => ({ block_type: 'task', id, name: id, body: [] } as Block);
const par = (gather?: GatherContract[]): Block => ({
  block_type: 'parallel', id: 'lanes', name: 'Lanes',
  body: [task('a'), task('b'), task('c')],
  ...(gather ? { gather_contract: gather } : {}),
} as Block);
const G: GatherContract = { name: 'finding', schema: 'ziya:finding/v1', per: 'child' };

function mount(block: Block) {
  const patches: Partial<Block>[] = [];
  render(<GatherContractSection block={block} onChange={p => patches.push(p)} />);
  return patches;
}
const last = (p: Partial<Block>[]) => p[p.length - 1].gather_contract as GatherContract[] | null;

describe('pure helpers', () => {
  it('offers per values the validator accepts', () => {
    expect(gatherPerOptions('parallel')).toEqual(['child', 'total']);
    expect(gatherPerOptions('repeat')).toEqual(['iteration', 'total']);
    expect(gatherPerOptions('until')).toEqual(['iteration', 'total']);
  });

  it('counts units like output_contracts.gather_unit_count', () => {
    expect(gatherUnitCount(par(), G)).toBe(3);
    const rep = { block_type: 'repeat', id: 'r', name: 'r', body: [], repeat_count: 4 } as unknown as Block;
    expect(gatherUnitCount(rep, { ...G, per: 'iteration' })).toBe(4);
    expect(gatherUnitCount({ ...rep, repeat_mode: 'for_each' } as Block, { ...G, per: 'iteration' })).toBeNull();
    expect(gatherUnitCount(par(), { ...G, per: 'total' })).toBeNull();
  });

  it('summarises all-units, quorum and total', () => {
    expect(gatherSummary(par(), G)).toMatch(/every child \(3\) emits at least 1/);
    expect(gatherSummary(par(), { ...G, min_units: 2 })).toMatch(/at least 2 of 3 child/);
    // A quorum at the unit count is just "every unit" again.
    expect(gatherSummary(par(), { ...G, min_units: 3 })).toMatch(/every child/);
    expect(gatherSummary(par(), { ...G, per: 'total', min: 3 })).toMatch(/at least 3 .* in total/);
  });
});

describe('GatherContractSection', () => {
  it('starts empty and add creates a per-child finding contract', () => {
    const p = mount(par());
    expect(screen.queryByTitle(/Remove this gather contract/)).toBeNull();
    fireEvent.click(screen.getByText('+ add'));
    expect(last(p)).toEqual([{ name: 'finding', schema: 'ziya:finding/v1', per: 'child' }]);
  });

  it('a repeat defaults to per iteration', () => {
    const p = mount({ block_type: 'repeat', id: 'r', name: 'r', body: [] } as unknown as Block);
    fireEvent.click(screen.getByText('+ add'));
    expect(last(p)![0].per).toBe('iteration');
  });

  it('sets min_units', () => {
    const p = mount(par([G]));
    expect(screen.getByText('of 3')).toBeTruthy();
    fireEvent.change(screen.getByTitle(/Quorum: how many units/), { target: { value: '2' } });
    expect(last(p)![0].min_units).toBe(2);
  });

  it('clears min_units to null (Optional), not by deleting it', () => {
    // Controlled input: start from a set value so clearing is a change.
    const p = mount(par([{ ...G, min_units: 2 }]));
    fireEvent.change(screen.getByTitle(/Quorum: how many units/), { target: { value: '' } });
    expect(last(p)![0]).toHaveProperty('min_units', null);
  });

  it('clearing min deletes the key instead of writing null', () => {
    const p = mount(par([{ ...G, min: 2 }]));
    fireEvent.change(screen.getByTitle(/Valid parts each unit must emit/), { target: { value: '' } });
    expect(last(p)![0]).not.toHaveProperty('min');
    expect(last(p)![0].name).toBe('finding');
  });

  it('switching to total drops min_units and rerun_missing and hides their inputs', () => {
    const p = mount(par([{ ...G, min_units: 2, rerun_missing: 1 }]));
    expect(screen.getByTitle(/Re-run short units once/)).toBeTruthy();
    fireEvent.change(screen.getByTitle('Unit the contract is judged over'), { target: { value: 'total' } });
    const e = last(p)![0];
    expect(e.per).toBe('total');
    expect(e).not.toHaveProperty('min_units');
    expect(e).not.toHaveProperty('rerun_missing');
  });

  it('a group offers per child / total and no rerun (the validator refuses rerun on a group)', () => {
    expect(gatherPerOptions('group')).toEqual(['child', 'total']);
    mount({ block_type: 'group', id: 'g', name: 'g', body: [task('a'), task('b')],
            gather_contract: [G] } as unknown as Block);
    expect(screen.queryByTitle(/Re-run short units once/)).toBeNull();
    expect(screen.getByText('of 2')).toBeTruthy();
  });

  it('rerun is offered on a parallel but not on an until', () => {
    mount({ block_type: 'until', id: 'u', name: 'u', body: [], gather_contract: [{ ...G, per: 'iteration' }] } as unknown as Block);
    expect(screen.queryByTitle(/Re-run short units once/)).toBeNull();
    // Positive: the entry itself rendered.
    expect(screen.getByTitle(/Remove this gather contract/)).toBeTruthy();
  });

  it('producer parses a comma list and clears to null', () => {
    const p = mount(par([G]));
    const prod = screen.getByTitle(/Ids of the task\(s\) inside a unit/);
    fireEvent.change(prod, { target: { value: 'a, b ,' } });
    expect(last(p)![0].producer).toEqual(['a', 'b']);
    fireEvent.change(prod, { target: { value: ' ' } });
    expect(last(p)![0].producer).toBeNull();
  });

  it('keeps keys it does not show and an inline schema', () => {
    const inline = { type: 'object' };
    const p = mount(par([{ ...G, schema: inline, require_claims: true, container: 'any' }]));
    expect(screen.getByText('inline schema')).toBeTruthy();
    fireEvent.change(screen.getByTitle(/Quorum: how many units/), { target: { value: '2' } });
    expect(last(p)![0]).toMatchObject({ schema: inline, require_claims: true, container: 'any', min_units: 2 });
  });

  it('removing the last entry writes null, not []', () => {
    const p = mount(par([G]));
    fireEvent.click(screen.getByTitle(/Remove this gather contract/));
    expect(last(p)).toBeNull();
  });
});

describe('wiring', () => {
  const dir = path.join(__dirname, '..');
  it.each(['ParallelBlockEditor', 'RepeatBlockEditor', 'UntilBlockEditor'])(
    '%s mounts the section', name => {
      const src = fs.readFileSync(path.join(dir, `${name}.tsx`), 'utf8');
      expect(src).toMatch(/import \{ GatherContractSection \} from '\.\/GatherContractSection';/);
      expect(src).toMatch(/<GatherContractSection block=\{block\} onChange=\{update\} \/>/);
    });

  it('TaskCardEditor mounts the section on the group root', () => {
    const src = fs.readFileSync(path.join(dir, 'TaskCardEditor.tsx'), 'utf8');
    expect(src).toMatch(/import \{ GatherContractSection \} from '\.\/GatherContractSection';/);
    // Inside the same group-root guard as SelfImproveSection.
    const guard = src.indexOf("{card.root.block_type === 'group' && (");
    const mount = src.indexOf('<GatherContractSection', guard);
    expect(guard).toBeGreaterThan(-1);
    expect(mount).toBeGreaterThan(guard);
    expect(src.slice(guard, mount)).not.toMatch(/\)\}\s*\{/);
  });

  it('the Block type declares gather_contract', () => {
    const t = fs.readFileSync(path.join(dir, '..', '..', 'types', 'task_card.ts'), 'utf8');
    expect(t).toMatch(/export interface GatherContract \{/);
    expect(t).toMatch(/gather_contract\?: GatherContract\[\] \| null;/);
    expect(t).toMatch(/min_units\?: number \| null;/);
  });
});

/**
 * The parallel-Repeat concurrency handle.
 *
 * The control exists because the cap was previously reachable only by
 * authoring JSON.  Its one subtle requirement is the empty-vs-zero
 * distinction: blank must clear to null (backend default, 8) while 0 is
 * the explicit unbounded opt-out.  A `parseInt(v) || 0` would collapse
 * those two into "unbounded", turning a cleared field into the exact
 * 60-concurrent-stream fan-out the cap was added to prevent.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

// ``uuid`` is ESM-only and the CRA jest transform won't process it;
// stub at module scope for any transitive importer.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
jest.mock('../BlockBody', () => ({ BlockBody: () => null }));
jest.mock('../BlockScopeButton', () => ({ BlockScopeButton: () => null }));
jest.mock('../DragContext', () => ({ DragHandle: () => null }));

import type { Block } from '../../../types/task_card';

const base = (over: Partial<Block> = {}): Block => ({
  block_type: 'repeat',
  id: 'r1',
  name: '',
  body: [],
  repeat_mode: 'for_each',
  ...over,
} as Block);

const conc = () => screen.queryByTitle(/Maximum iterations running at once/i);

async function mount(block: Block, onChange: (b: Block) => void = () => {}) {
  const { RepeatBlockEditor } = await import('../RepeatBlockEditor');
  return render(<RepeatBlockEditor block={block} onChange={onChange} />);
}

describe('RepeatBlockEditor concurrency handle', () => {
  it('is hidden for a serial Repeat', async () => {
    await mount(base({ repeat_parallel: false }));
    expect(conc()).not.toBeInTheDocument();
  });

  it('appears once parallel is enabled', async () => {
    await mount(base({ repeat_parallel: true }));
    expect(conc()).toBeInTheDocument();
  });

  it('shows blank when unset, with the default surfaced as placeholder', async () => {
    await mount(base({ repeat_parallel: true }));
    const input = conc() as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input.placeholder).toBe('8');
  });

  it('propagates an explicit limit', async () => {
    const onChange = jest.fn();
    await mount(base({ repeat_parallel: true }), onChange);
    fireEvent.change(conc()!, { target: { value: '3' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ repeat_max_concurrency: 3 }),
    );
  });

  it('clears to null — NOT 0 — when emptied', async () => {
    const onChange = jest.fn();
    await mount(base({ repeat_parallel: true, repeat_max_concurrency: 5 }), onChange);
    fireEvent.change(conc()!, { target: { value: '' } });
    const patch = onChange.mock.calls[0][0];
    expect(patch.repeat_max_concurrency).toBeNull();
    // null means "default 8"; 0 means unbounded.  Conflating them is
    // silent and expensive, which is the whole point of this test.
    expect(patch.repeat_max_concurrency).not.toBe(0);
  });

  it('preserves an explicit 0 as the unbounded opt-out', async () => {
    const onChange = jest.fn();
    await mount(base({ repeat_parallel: true }), onChange);
    fireEvent.change(conc()!, { target: { value: '0' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ repeat_max_concurrency: 0 }),
    );
  });

  it('round-trips an existing value into the input', async () => {
    await mount(base({ repeat_parallel: true, repeat_max_concurrency: 12 }));
    expect((conc() as HTMLInputElement).value).toBe('12');
  });
});

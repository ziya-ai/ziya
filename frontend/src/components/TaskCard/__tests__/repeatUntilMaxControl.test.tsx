/**
 * The until-mode Repeat iteration cap.
 *
 * The control displayed `block.repeat_max ?? 3` while the backend planned
 * `int(repeat_max or 1)` iterations, so an author who never touched the
 * field was shown 3 and got 1.  (The adjacent Until BLOCK defaults to 5,
 * making three different numbers for one concept.)
 *
 * The displayed default is corrected to the runtime's real one rather
 * than the runtime being raised to match: raising it would silently
 * change spend on every existing until-mode Repeat that never set a cap.
 *
 * The cross-language half of this -- that the number below still equals
 * the number `_plan_iterations` actually uses -- is asserted in
 * tests/test_repeat_until_default_agreement.py, which reads this file.
 * Neither test alone can catch the two halves drifting apart.
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

/** Must match EFFECTIVE_UNTIL_DEFAULT in the python sibling test. */
const RUNTIME_DEFAULT = 1;

const base = (over: Partial<Block> = {}): Block => ({
  block_type: 'repeat',
  id: 'r1',
  name: '',
  body: [],
  repeat_mode: 'until',
  ...over,
} as Block);

const maxInput = () =>
  screen.queryByTitle(/Maximum iterations before the loop gives up/i);

async function mount(block: Block, onChange: (b: Block) => void = () => {}) {
  const { RepeatBlockEditor } = await import('../RepeatBlockEditor');
  return render(<RepeatBlockEditor block={block} onChange={onChange} />);
}

describe('RepeatBlockEditor until-mode iteration cap', () => {
  it('is present in until mode', async () => {
    await mount(base());
    expect(maxInput()).toBeInTheDocument();
  });

  it('is absent in count mode', async () => {
    // Negative control: without it, a control rendered unconditionally
    // would satisfy the test above.
    await mount(base({ repeat_mode: 'count' }));
    expect(maxInput()).not.toBeInTheDocument();
  });

  it('displays the number the backend will actually use when unset', async () => {
    await mount(base());
    expect((maxInput() as HTMLInputElement).value).toBe(String(RUNTIME_DEFAULT));
  });

  it('displays an authored cap unchanged', async () => {
    // Guards against "fixing" the default by hardcoding the display.
    await mount(base({ repeat_max: 7 }));
    expect((maxInput() as HTMLInputElement).value).toBe('7');
  });

  it('propagates an edited cap', async () => {
    const onChange = jest.fn();
    await mount(base(), onChange);
    fireEvent.change(maxInput()!, { target: { value: '6' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ repeat_max: 6 }),
    );
  });

  it('coerces a cleared field to the runtime floor, never 0', async () => {
    // 0 is NOT an uncapped opt-out in until mode -- the backend reads
    // `repeat_max or 1`, so 0 would mean 1 anyway; emitting 0 would
    // merely record a number that misdescribes the run.  (Contrast the
    // for_each cap, where 0 legitimately means "whole roster".)
    const onChange = jest.fn();
    await mount(base({ repeat_max: 5 }), onChange);
    fireEvent.change(maxInput()!, { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ repeat_max: RUNTIME_DEFAULT }),
    );
  });
});

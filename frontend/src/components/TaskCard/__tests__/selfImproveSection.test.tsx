/**
 * The self-improvement authoring section.
 *
 * The subtle requirements, mirroring repeat_max_concurrency's
 * empty-vs-zero discipline:
 *   - blank max-edits must clear to null (backend default, 2), while
 *     0 is the explicit observe-only mode.  `parseInt(v) || 0` would
 *     collapse a cleared field into observe-only; `|| null` would
 *     collapse observe-only into the default.  Both are wrong in
 *     opposite directions.
 *   - selecting 'conservative' drift stores null (the default), not
 *     the literal string — so an untouched card and an explicitly
 *     conservative card serialize identically.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import type { Block } from '../../../types/task_card';

const base = (over: Partial<Block> = {}): Block => ({
  block_type: 'repeat',
  id: 'r1',
  name: '',
  body: [],
  ...over,
} as Block);

const toggle = () => screen.getByTitle(/judge decides whether a tangible/i);
const maxEdits = () => screen.queryByTitle(/Card edits this level may apply per run/i);
const criterion = () => screen.queryByTitle(/What 'good enough' means/i);

async function mount(
  block: Block,
  onChange: (patch: Partial<Block>) => void = () => {},
) {
  const { SelfImproveSection } = await import('../SelfImproveSection');
  return render(<SelfImproveSection block={block} onChange={onChange} />);
}

describe('SelfImproveSection', () => {
  it('renders only the toggle when disabled', async () => {
    await mount(base());
    expect(toggle()).toBeInTheDocument();
    expect(maxEdits()).not.toBeInTheDocument();
    expect(criterion()).not.toBeInTheDocument();
  });

  it('enabling the toggle emits self_improve: true', async () => {
    const patches: Partial<Block>[] = [];
    await mount(base(), p => patches.push(p));
    fireEvent.click(toggle());
    expect(patches).toEqual([{ self_improve: true }]);
  });

  it('shows the full controls when enabled', async () => {
    await mount(base({ self_improve: true }));
    expect(maxEdits()).toBeInTheDocument();
    expect(criterion()).toBeInTheDocument();
  });

  it('criterion text is stored, blank clears to null', async () => {
    const patches: Partial<Block>[] = [];
    await mount(base({ self_improve: true, improve_criterion: 'old' }),
      p => patches.push(p));
    fireEvent.change(criterion()!, { target: { value: 'tests pass' } });
    fireEvent.change(criterion()!, { target: { value: '' } });
    expect(patches).toEqual([
      { improve_criterion: 'tests pass' },
      { improve_criterion: null },
    ]);
  });

  it('blank max-edits clears to null (backend default), not 0', async () => {
    const patches: Partial<Block>[] = [];
    await mount(base({ self_improve: true, improve_max: 3 }),
      p => patches.push(p));
    fireEvent.change(maxEdits()!, { target: { value: '' } });
    expect(patches).toEqual([{ improve_max: null }]);
  });

  it('0 max-edits is preserved as observe-only, not collapsed to null', async () => {
    const patches: Partial<Block>[] = [];
    await mount(base({ self_improve: true }), p => patches.push(p));
    fireEvent.change(maxEdits()!, { target: { value: '0' } });
    expect(patches).toEqual([{ improve_max: 0 }]);
  });

  it('observe-only mode shows its explanatory hint', async () => {
    await mount(base({ self_improve: true, improve_max: 0 }));
    expect(screen.getByText(/Observe-only/)).toBeInTheDocument();
  });

  it('conservative drift stores null (the default), expansive stores the literal', async () => {
    const patches: Partial<Block>[] = [];
    await mount(base({ self_improve: true }), p => patches.push(p));
    const drift = screen.getByTitle(/Conservative \(default\)/i);
    fireEvent.change(drift, { target: { value: 'expansive' } });
    fireEvent.change(drift, { target: { value: 'conservative' } });
    expect(patches).toEqual([
      { improve_drift: 'expansive' },
      { improve_drift: null },
    ]);
  });
});

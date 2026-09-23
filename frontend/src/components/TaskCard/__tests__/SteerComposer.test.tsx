/**
 * SteerComposer — the in-place "say something to this block" control that
 * sits under the Inspector's Live output tab.  These tests assert what the
 * backend depends on: the chosen target (blockId, index) and the hold flag
 * reach ``onSend`` unchanged, since the server keys delivery on exactly
 * that pair (app/utils/task_steer.steer_key) and ``hold`` maps to a step
 * credit.  They also pin the wording that keeps the latency honest and
 * the per-target scoping of the note history.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { SteerComposer, type SteerTarget } from '../SteerComposer';
import type { SteerNote } from '../../../types/task_run';

const one: SteerTarget[] = [{ blockId: 't1', index: 0, label: 'audit' }];
const many: SteerTarget[] = [
  { blockId: 'loop', index: 7, label: 'migrate · #7' },
  { blockId: 'loop', index: 8, label: 'migrate · #8' },
];

const note = (over: Partial<SteerNote>): SteerNote => ({
  id: 'n1', block_id: 'loop', index: 7, text: 'hello', hold: false,
  status: 'queued', created_at: 1, delivered_at: null, ...over,
});

describe('SteerComposer', () => {
  it('renders nothing with no live targets', () => {
    const { container } = render(<SteerComposer targets={[]} onSend={jest.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it('Send passes the target and hold=false; clears the box', () => {
    const onSend = jest.fn();
    render(<SteerComposer targets={one} onSend={onSend} />);
    const box = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'use the seal test' } });
    fireEvent.click(screen.getByText('Send'));
    expect(onSend).toHaveBeenCalledWith(one[0], 'use the seal test', false);
    expect(box.value).toBe('');
  });

  it('Send & hold passes hold=true', () => {
    const onSend = jest.fn();
    render(<SteerComposer targets={one} onSend={onSend} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'stop after this' } });
    fireEvent.click(screen.getByText(/Send & hold/));
    expect(onSend).toHaveBeenCalledWith(one[0], 'stop after this', true);
  });

  it('⌘↵ sends without holding', () => {
    const onSend = jest.fn();
    render(<SteerComposer targets={one} onSend={onSend} />);
    const box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: 'go' } });
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    expect(onSend).toHaveBeenCalledWith(one[0], 'go', false);
  });

  it('does not send whitespace-only text', () => {
    const onSend = jest.fn();
    render(<SteerComposer targets={one} onSend={onSend} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '   ' } });
    expect(screen.getByText('Send')).toBeDisabled();
    fireEvent.click(screen.getByText('Send'));
    expect(onSend).not.toHaveBeenCalled();
  });

  it('single target: no selector; placeholder promises the next model round, not instant delivery', () => {
    render(<SteerComposer targets={one} onSend={jest.fn()} />);
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByPlaceholderText(/audit… lands before its next model round/)).toBeInTheDocument();
  });

  it('multiple targets: selector chooses which iteration receives the note', () => {
    const onSend = jest.fn();
    render(<SteerComposer targets={many} onSend={onSend} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'loop:8' } });
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'skip render tests' } });
    fireEvent.click(screen.getByText('Send'));
    expect(onSend).toHaveBeenCalledWith(many[1], 'skip render tests', false);
  });

  it('shows only the selected target\u2019s notes with their fate', () => {
    const notes = [
      note({ id: 'a', text: 'for seven', status: 'delivered' }),
      note({ id: 'b', text: 'for eight', index: 8, status: 'expired' }),
    ];
    render(<SteerComposer targets={many} notes={notes} onSend={jest.fn()} />);
    expect(screen.getByText('for seven')).toBeInTheDocument();
    expect(screen.getByText('delivered ✓')).toBeInTheDocument();
    expect(screen.queryByText('for eight')).toBeNull();
  });

  it('busy disables the controls', () => {
    render(<SteerComposer targets={one} busy onSend={jest.fn()} />);
    expect(screen.getByRole('textbox')).toBeDisabled();
    expect(screen.getByText('Send')).toBeDisabled();
  });
});

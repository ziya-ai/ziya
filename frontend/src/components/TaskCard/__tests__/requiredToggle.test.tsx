/**
 * @jest-environment jsdom
 *
 * Block.required existed on the model (app/models/task_card.py) and in the
 * executor, but nothing in the editor could set it: a card could only say
 * required: false by hand-editing JSON.  The toggle lives in each
 * non-root block header; unchecking writes `required: false`, re-checking
 * removes the key (the default is true, so a saved card stays minimal).
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
jest.mock('../../../context/ProjectContext', () => ({
  useProject: () => ({ skills: [], currentProject: null }),
}));
jest.mock('../../Permissions/PermissionsDialog', () => ({ PermissionsDialog: () => null }));
jest.mock('../../DirectoryBrowserModal', () => ({ DirectoryBrowserModal: () => null }));
jest.mock('../ModelTierPicker', () => ({ ModelTierPicker: () => null }));
jest.mock('../DragContext', () => ({
  DragHandle: () => null,
  useTaskCardDrag: () => ({ draggingId: null, canDropInto: () => false, drop: () => {} }),
}));
jest.mock('../AutoGrowTextarea', () => ({
  AutoGrowTextarea: ({ minRows, ...rest }: any) => <textarea {...rest} />,
}));
jest.mock('../SelfImproveSection', () => ({ SelfImproveSection: () => null }));
jest.mock('../BlockScopeButton', () => ({ BlockScopeButton: () => null }));
jest.mock('../../../services/taskCardApi', () => ({ taskCardApi: { list: async () => [] } }));

import { RequiredToggle } from '../RequiredToggle';
import { BlockEditor } from '../BlockEditor';
import type { Block } from '../../../types/task_card';

const task = (extra: Partial<Block> = {}): Block => ({
  block_type: 'task', id: 't1', name: 'T', instructions: 'x', body: [], ...extra,
} as unknown as Block);

describe('RequiredToggle', () => {
  it('is checked by default and writes required:false when unchecked', () => {
    const onChange = jest.fn();
    render(<RequiredToggle block={task()} onChange={onChange} />);
    const box = screen.getByTestId('tc-required') as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledWith({ required: false });
  });

  it('reflects required:false and clears the key when re-checked', () => {
    const onChange = jest.fn();
    render(<RequiredToggle block={task({ required: false })} onChange={onChange} />);
    const box = screen.getByTestId('tc-required') as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledWith({ required: undefined });
  });
});

describe('block editors mount the toggle', () => {
  const kinds: Array<[string, Block]> = [
    ['task', task()],
    ['call', { block_type: 'call', id: 'c', name: 'C', call_target: 'x', body: [] } as unknown as Block],
    ['parallel', { block_type: 'parallel', id: 'p', name: 'P', body: [] } as unknown as Block],
    ['repeat', { block_type: 'repeat', id: 'r', name: 'R', repeat_mode: 'count', repeat_count: 2, body: [] } as unknown as Block],
    ['until', { block_type: 'until', id: 'u', name: 'U', until_max: 2, body: [] } as unknown as Block],
  ];

  it.each(kinds)('%s editor shows the toggle for a non-root block and writes through onChange', (_k, block) => {
    const onChange = jest.fn();
    render(<BlockEditor block={block} onChange={onChange} onDelete={() => {}} />);
    const box = screen.getByTestId('tc-required') as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toMatchObject({ id: block.id, required: false });
  });

  it('the root block has no toggle (required is ignored on a root)', () => {
    render(<BlockEditor block={task()} onChange={() => {}} isRoot />);
    expect(screen.queryByTestId('tc-required')).toBeNull();
  });
});

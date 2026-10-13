/**
 * @jest-environment jsdom
 *
 * scope.cwd on every layer.  merge_scopes already carried a container's
 * cwd down to its leaves (innermost wins), but only TaskBlockEditor could
 * show or set it, so a cwd a model put on a Group was invisible in the
 * editor and absent from the run map's Configuration lines.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('../../Permissions/PermissionsDialog', () => ({ PermissionsDialog: () => null }));
jest.mock('../../DirectoryBrowserModal', () => ({ DirectoryBrowserModal: () => null }));
jest.mock('../ModelTierPicker', () => ({ ModelTierPicker: () => null }));

import { BlockScopeButton } from '../BlockScopeButton';
import { blockConfigLines } from '../runMapModel';
import type { Block, TaskScope } from '../../../types/task_card';

describe('BlockScopeButton working directory', () => {
  it('shows the inherited placeholder when unset', () => {
    render(<BlockScopeButton scope={null} onChange={() => {}} title="Group" />);
    expect(screen.getByTestId('tc-scope-cwd').textContent).toContain('inherited');
    expect(screen.queryByText('reset')).toBeNull();
  });

  it('shows the cwd and reset writes cwd:null keeping the rest of the scope', () => {
    const onChange = jest.fn();
    const scope: TaskScope = { paths: [], tools: ['git'], skills: [], cwd: 'services/api' };
    render(<BlockScopeButton scope={scope} onChange={onChange} title="Group" />);
    expect(screen.getByTestId('tc-scope-cwd').textContent).toBe('services/api');
    fireEvent.click(screen.getByText('reset'));
    expect(onChange).toHaveBeenCalledWith({ ...scope, cwd: null });
  });
});

describe('blockConfigLines', () => {
  it('names the cwd in the Scope line', () => {
    const block = {
      block_type: 'group', id: 'g', name: 'G', body: [],
      scope: { paths: [], tools: [], skills: [], cwd: 'services/api' },
    } as unknown as Block;
    const scopeLine = blockConfigLines(block).find(l => l.label === 'Scope');
    expect(scopeLine?.value).toContain('cwd: services/api');
  });
});

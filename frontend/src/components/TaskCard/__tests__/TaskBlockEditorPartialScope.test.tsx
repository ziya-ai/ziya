/**
 * Regression tests for partial / model-authored TaskScope objects.
 *
 * A card authored via task_card_write (or a hand-edited card file) can
 * carry a scope object that omits the tools/skills/paths arrays.
 * TaskBlockEditor previously read scope.tools.length directly, crashing
 * the entire Task Cards editor with
 *   TypeError: Cannot read properties of undefined (reading 'length')
 * the moment such a card was opened for preview/edit.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
jest.mock('../../../context/ProjectContext', () => ({
  useProject: () => ({ skills: [] }),
}));
jest.mock('../../Permissions/PermissionsDialog', () => ({
  PermissionsDialog: () => null,
}));
jest.mock('../../DirectoryBrowserModal', () => ({
  DirectoryBrowserModal: () => null,
}));
jest.mock('../ModelTierPicker', () => ({ ModelTierPicker: () => null }));
jest.mock('../DragContext', () => ({ DragHandle: () => null }));
jest.mock('../AutoGrowTextarea', () => ({
  AutoGrowTextarea: ({ minRows, ...rest }: any) => <textarea {...rest} />,
}));

describe('normalizeTaskScope (pure)', () => {
  it('fills missing arrays on a partial scope', async () => {
    const { normalizeTaskScope } = await import('../../../utils/taskCardBlocks');
    const s = normalizeTaskScope({ paths: [{ path: 'a.py' }] } as any);
    expect(s.paths).toEqual([{ path: 'a.py' }]);
    expect(s.tools).toEqual([]);
    expect(s.skills).toEqual([]);
  });

  it('handles null and undefined scope', async () => {
    const { normalizeTaskScope } = await import('../../../utils/taskCardBlocks');
    for (const input of [null, undefined]) {
      const s = normalizeTaskScope(input as any);
      expect(s.paths).toEqual([]);
      expect(s.tools).toEqual([]);
      expect(s.skills).toEqual([]);
    }
  });

  it('preserves populated fields and non-array extras', async () => {
    const { normalizeTaskScope } = await import('../../../utils/taskCardBlocks');
    const s = normalizeTaskScope({
      paths: [{ path: 'x' }], tools: ['grep'], skills: ['sk-1'],
      cwd: 'sub/dir', shell_commands: ['pytest'], model_tier: 'small',
    } as any);
    expect(s.tools).toEqual(['grep']);
    expect(s.skills).toEqual(['sk-1']);
    expect(s.cwd).toBe('sub/dir');
    expect(s.shell_commands).toEqual(['pytest']);
    expect((s as any).model_tier).toBe('small');
  });
});

describe('TaskBlockEditor with partial scope', () => {
  const baseBlock: any = {
    block_type: 'task', id: 't-1', name: 'T', instructions: '', body: [],
  };

  it('renders without crashing when scope omits tools/skills (regression)', async () => {
    const { TaskBlockEditor } = await import('../TaskBlockEditor');
    render(
      <TaskBlockEditor
        block={{ ...baseBlock, scope: { paths: [] } }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText('Permissions')).toBeInTheDocument();
  });

  it('renders when scope is entirely absent', async () => {
    const { TaskBlockEditor } = await import('../TaskBlockEditor');
    render(<TaskBlockEditor block={{ ...baseBlock }} onChange={() => {}} />);
    expect(screen.getByText('Permissions')).toBeInTheDocument();
  });

  it('still renders tool/skill chips when arrays are present', async () => {
    const { TaskBlockEditor } = await import('../TaskBlockEditor');
    render(
      <TaskBlockEditor
        block={{ ...baseBlock, scope: { paths: [], tools: ['grep'], skills: [] } }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/grep/)).toBeInTheDocument();
  });
});

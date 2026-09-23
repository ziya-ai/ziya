/**
 * The Permissions dialog's Shell tab must expose ``shell_timeout_secs``.
 *
 * Until now the field existed only on the backend model and could be set
 * only through the API or a staged card; both block editors saved through
 * PermissionsDialog, whose payload carried shellCommands and nothing about
 * the timeout.  The GFX Stage 2 card's full-corpus pytest sweep needs
 * 5400 s and there was no control to give it one.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

// PermissionsDialog reaches the ESM-only ``uuid`` through the
// conversation utilities; the sibling editor tests mock it the same way.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
jest.mock('../../../context/ProjectContext', () => ({
  useProject: () => ({ skills: [], mcpTools: [], projectRoot: '/p' }),
}));

import { parseShellTimeout } from '../shellTimeout';

describe('parseShellTimeout (pure)', () => {
  it('reads a positive integer', () => {
    expect(parseShellTimeout('5400')).toBe(5400);
    expect(parseShellTimeout(' 120 ')).toBe(120);
  });
  it('treats blank, zero, negative and junk as "unset" (null)', () => {
    for (const s of ['', '   ', '0', '-5', 'abc', '12abc']) {
      expect(parseShellTimeout(s)).toBeNull();
    }
  });
  it('truncates a fractional value rather than rejecting it', () => {
    expect(parseShellTimeout('90.7')).toBe(90);
  });
});

describe('PermissionsDialog shell tab', () => {
  const setup = async (initial?: number | null) => {
    const { PermissionsDialog } = await import('../PermissionsDialog');
    const onSave = jest.fn();
    render(
      <PermissionsDialog
        open
        entries={[]}
        tools={[]}
        skills={[]}
        shellCommands={['npm']}
        shellTimeoutSecs={initial ?? undefined}
        onClose={() => undefined}
        onSave={onSave}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: /shell/i }));
    return { onSave };
  };

  it('shows the existing timeout and saves an edited one', async () => {
    const { onSave } = await setup(1200);
    const input = screen.getByLabelText(/shell timeout/i) as HTMLInputElement;
    expect(input.value).toBe('1200');
    fireEvent.change(input, { target: { value: '5400' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];
    expect(payload.shellTimeoutSecs).toBe(5400);
    // The existing pieces still ride along — the timeout did not
    // displace the grants.
    expect(payload.shellCommands).toEqual(['npm']);
  });

  it('saves null when the field is cleared, so the base ceiling applies', async () => {
    const { onSave } = await setup(1200);
    const input = screen.getByLabelText(/shell timeout/i);
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onSave.mock.calls[0][0].shellTimeoutSecs).toBeNull();
  });
});

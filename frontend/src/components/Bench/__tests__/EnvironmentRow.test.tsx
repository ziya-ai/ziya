/**
 * @jest-environment jsdom
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { EnvironmentRow } from '../EnvironmentRow';
import { mk } from '../benchTestUtils';

const env = () => [
    { ...mk('mcp:shell', { tier: 'environment' }), tokens: 1000 },
    { ...mk('builtin:fileio' as any, { tier: 'environment' }), kind: 'builtin' as const, name: 'File I/O',
      tokens: 2000, health: { enabled: true, toggleable: true, tool_count: 3 } },
    { ...mk('builtin:skills' as any, { tier: 'environment' }), kind: 'builtin' as const, name: 'Skill Discovery',
      tokens: 500, health: { enabled: false, toggleable: true, tool_count: 1 } },
];

describe('EnvironmentRow', () => {
    it('total and summary count only enabled categories; a disabled one is listed dimmed', () => {
        render(<EnvironmentRow items={env()} onToggle={() => {}} />);
        const row = screen.getByTestId('environment-row');
        expect(row).toHaveTextContent('3.0K');                       // 1000 + 2000, not 3500
        expect(row.firstChild).toHaveTextContent('shell · File I/O');
        expect(row.firstChild).not.toHaveTextContent('Skill Discovery');
        fireEvent.click(screen.getByText('inspect ▸'));
        const skills = screen.getByTestId('env-builtin:skills');
        expect(skills).toHaveTextContent('Skill Discovery');
        expect(skills.style.opacity).toBe('0.5');
    });

    it('the checkbox reports the category and the new state; MCP servers get no checkbox', () => {
        const onToggle = jest.fn();
        render(<EnvironmentRow items={env()} onToggle={onToggle} />);
        fireEvent.click(screen.getByText('inspect ▸'));
        expect(within(screen.getByTestId('env-mcp:shell')).queryByRole('checkbox')).toBeNull();
        const fileio = screen.getByRole('checkbox', { name: 'File I/O enabled' });
        expect(fileio).toBeChecked();
        fireEvent.click(fileio);
        expect(onToggle).toHaveBeenCalledWith('fileio', false);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Skill Discovery enabled' }));
        expect(onToggle).toHaveBeenLastCalledWith('skills', true);
    });

    it('without onToggle the row is read-only', () => {
        render(<EnvironmentRow items={env()} />);
        fireEvent.click(screen.getByText('inspect ▸'));
        expect(screen.queryByRole('checkbox')).toBeNull();
    });

    it('the warning is shown as a status', () => {
        render(<EnvironmentRow items={env()} warning="Skill discovery is off: dead weight" />);
        expect(screen.getByRole('status')).toHaveTextContent('Skill discovery is off');
    });

    it('shell attention shows a badge on the collapsed row that opens shell config', () => {
        const open = jest.fn();
        const { rerender } = render(<EnvironmentRow items={env()} shellAttention={{ level: 'none', reasons: [] }} onOpenShellConfig={open} />);
        expect(screen.queryByTestId('environment-shell-badge')).toBeNull();
        rerender(<EnvironmentRow items={env()} onOpenShellConfig={open}
                                 shellAttention={{ level: 'warning', reasons: ['escalation unsigned'] }} />);
        const badge = screen.getByTestId('environment-shell-badge');
        expect(badge).toHaveAttribute('title', 'escalation unsigned');
        fireEvent.click(badge);
        expect(open).toHaveBeenCalledTimes(1);
    });
});

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

    // Shell Configuration lost its toolbar button when the Bench absorbed it
    // (c9434722); the environment row is now its only entry point, so the
    // control must exist in the healthy state too, not only under attention.
    it('shell config is reachable from the collapsed row even with no attention', () => {
        const open = jest.fn();
        render(<EnvironmentRow items={env()} shellAttention={{ level: 'none', reasons: [] }} onOpenShellConfig={open} />);
        const btn = screen.getByTestId('environment-shell-config');
        expect(btn).toHaveAttribute('data-attention', 'none');
        expect(btn).toHaveAttribute('title', 'Shell configuration');
        expect(btn).toHaveTextContent('⚙ shell');
        fireEvent.click(btn);
        expect(open).toHaveBeenCalledTimes(1);
    });

    it('shell config is also reachable from the expanded list with no attention', () => {
        const open = jest.fn();
        render(<EnvironmentRow items={env()} onOpenShellConfig={open} />);
        fireEvent.click(screen.getByText('inspect ▸'));
        fireEvent.click(within(screen.getByTestId('env-mcp:shell')).getByText('configure'));
        expect(open).toHaveBeenCalledTimes(1);
    });

    it('shell attention turns the row control into a badge carrying the reasons', () => {
        const open = jest.fn();
        render(<EnvironmentRow items={env()} onOpenShellConfig={open}
                               shellAttention={{ level: 'warning', reasons: ['escalation unsigned'] }} />);
        const badge = screen.getByTestId('environment-shell-config');
        expect(badge).toHaveAttribute('data-attention', 'warning');
        expect(badge).toHaveAttribute('title', 'escalation unsigned');
        expect(badge).toHaveTextContent('⚠ shell');
        fireEvent.click(badge);
        expect(open).toHaveBeenCalledTimes(1);
    });

    it('the shell control is absent without an opener or without a shell item', () => {
        render(<EnvironmentRow items={env()} shellAttention={{ level: 'warning', reasons: ['x'] }} />);
        expect(screen.queryByTestId('environment-shell-config')).toBeNull();
        const open = jest.fn();
        render(<EnvironmentRow items={env().filter(i => i.key !== 'mcp:shell')} onOpenShellConfig={open} />);
        expect(screen.queryByTestId('environment-shell-config')).toBeNull();
    });
});

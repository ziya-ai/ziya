/**
 * @jest-environment jsdom
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPalette, filterItems } from '../BenchPalette';
import { moveOptions } from '../laneDnd';
import { mk } from '../benchTestUtils';

const items = () => [
    mk('skill:Concise', { project: 'always', def: 'off', provenance: 'builtin' }),
    mk('skill:Code Review', { provenance: 'project' }),
    mk('mcp:jira', { provenance: 'user' }),
    mk('mcp:shell', { tier: 'environment' }),
];

function setup(over: Partial<React.ComponentProps<typeof BenchPalette>['bench']> = {}) {
    const place = jest.fn(async () => {});
    const setLens = jest.fn();
    const bench = { items: items(), lens: 'project' as const, lenses: ['project', 'user'] as const, setLens, place, ...over };
    const onClose = jest.fn();
    render(<BenchPalette bench={bench as any} open onClose={onClose} />);
    return { place, setLens, onClose };
}

it('filters by name/kind/provenance and never lists environment items', () => {
    expect(filterItems(items(), '').map(i => i.key)).toEqual(['skill:Concise', 'skill:Code Review', 'mcp:jira']);
    expect(filterItems(items(), 'mcp').map(i => i.key)).toEqual(['mcp:jira']);
    expect(filterItems(items(), 'PROJECT').map(i => i.key)).toEqual(['skill:Code Review']);
    expect(filterItems(items(), 'shell')).toEqual([]);
});

it('verbs are exactly the board\'s enabled move options for the highlighted item', () => {
    setup();
    fireEvent.change(screen.getByTestId('palette-input'), { target: { value: 'jira' } });
    const shown = within(screen.getByTestId('palette-verbs')).getAllByRole('menuitem').map(b => b.textContent!.replace(' ↵', ''));
    const expected = moveOptions(items()[2]).filter(o => !o.disabled).map(o => o.label);
    expect(shown).toEqual(expected);
    expect(shown.some(l => /coming soon/.test(l))).toBe(false);   // the MCP on-demand refusal is not offered
});

it('↵ applies the highlighted verb through place; ⇥ moves the highlight; ⇧⇥ changes lens', async () => {
    const { place, setLens } = setup();
    const dlg = screen.getByTestId('bench-palette');
    fireEvent.change(screen.getByTestId('palette-input'), { target: { value: 'code' } });
    // Code Review is a ghost at ondemand; first verb is "Move to On hand".
    await act(async () => { fireEvent.keyDown(dlg, { key: 'Enter' }); });
    expect(place).toHaveBeenCalledWith('skill:Code Review', 'always');
    fireEvent.keyDown(dlg, { key: 'Tab' });
    await act(async () => { fireEvent.keyDown(dlg, { key: 'Enter' }); });
    expect(place).toHaveBeenLastCalledWith('skill:Code Review', 'off');
    fireEvent.keyDown(dlg, { key: 'Tab', shiftKey: true });
    expect(setLens).toHaveBeenCalledWith('user');
    expect(screen.getByRole('status')).toHaveTextContent('Code Review →');
});

it('a pinned item dropped on its inherited lane clears, same as the board', async () => {
    const { place } = setup();
    fireEvent.change(screen.getByTestId('palette-input'), { target: { value: 'concise' } });
    const clear = within(screen.getByTestId('palette-verbs')).getByRole('menuitem', { name: /Shelved \(inherit\)/ });
    await act(async () => { fireEvent.click(clear); });
    expect(place).toHaveBeenCalledWith('skill:Concise', null);
});

it('esc closes; a rejected place is reported, not swallowed', async () => {
    const err = Object.assign(new Error('x'), { reason: 'environment_not_placeable' });
    const { onClose } = setup({ place: jest.fn(async () => { throw err; }) });
    const dlg = screen.getByTestId('bench-palette');
    await act(async () => { fireEvent.keyDown(dlg, { key: 'Enter' }); });
    expect(screen.getByRole('status')).toHaveTextContent('part of the environment');
    fireEvent.keyDown(dlg, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
});

it('imports nothing from the board or the API: a second shell over the same model', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'BenchPalette.tsx'), 'utf8');
    expect(src).not.toMatch(/from '\.\/BenchBoard'|from '\.\/CapabilityCard|from '\.\/BenchPanel'/);
    expect(src).not.toMatch(/apis\/benchApi|\bfetch\(/);
});

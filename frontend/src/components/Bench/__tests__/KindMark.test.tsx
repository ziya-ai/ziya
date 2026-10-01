/**
 * @jest-environment jsdom
 *
 * Skills and MCP servers share every lane and every Find column, so each
 * surface must mark the kind unmistakably: one shared chip with a glyph, a
 * colour and the word -- not colour alone, and not a colour the provenance
 * spine already uses.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchFind, DEBOUNCE_MS } from '../BenchFind';
import { CapabilityCard } from '../CapabilityCard';
import { CapabilityDetail } from '../CapabilityDetail';
import { BenchPalette } from '../BenchPalette';
import { KIND_COLOR, KindMark, columnKind } from '../KindMark';
import { mk } from '../benchTestUtils';

const mark = (el: HTMLElement) => within(el).getByTestId('kind-mark');

it('glyph + word differ per kind', () => {
    render(<><KindMark kind="skill" /><KindMark kind="mcp" /></>);
    const [s, m] = screen.getAllByTestId('kind-mark');
    expect(s).toHaveAttribute('data-kind', 'skill');
    expect(m).toHaveAttribute('data-kind', 'mcp');
    expect(s).toHaveTextContent('◆');  expect(s).toHaveTextContent('skill');
    expect(m).toHaveTextContent('⚙');  expect(m).toHaveTextContent('MCP');
    expect(s.textContent).not.toEqual(m.textContent);
    expect(KIND_COLOR.skill).not.toEqual(KIND_COLOR.mcp);
});

it('no card carries a coloured left bar or a per-card "inherited" word; every card shows its tokens', () => {
    const zero = { ...mk('skill:Mine', { provenance: 'project' }), tokens: 0 };
    render(<><CapabilityCard item={mk('mcp:jira', { provenance: 'user' })} onIntent={() => {}} />
             <CapabilityCard item={mk('skill:Concise', { provenance: 'builtin' })} onIntent={() => {}} />
             <CapabilityCard item={zero} onIntent={() => {}} /></>);
    for (const k of ['mcp:jira', 'skill:Concise', 'skill:Mine']) {
        const card = screen.getByTestId(`card-${k}`);
        expect(card.style.borderLeftWidth).not.toBe('4px');
        expect(card).not.toHaveTextContent('inherited');
        expect(within(card).getByTestId(`tokens-${k}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('tokens-skill:Mine')).toHaveTextContent('0t');
});

it('the card marks its kind', () => {
    render(<><CapabilityCard item={mk('skill:Concise')} onIntent={() => {}} />
             <CapabilityCard item={mk('mcp:jira')} onIntent={() => {}} /></>);
    expect(mark(screen.getByTestId('card-skill:Concise'))).toHaveAttribute('data-kind', 'skill');
    expect(mark(screen.getByTestId('card-mcp:jira'))).toHaveAttribute('data-kind', 'mcp');
});

it('the drawer header marks its kind', () => {
    render(<CapabilityDetail item={mk('mcp:jira')} onClose={() => {}} />);
    expect(mark(screen.getByTestId('bench-detail'))).toHaveAttribute('data-kind', 'mcp');
});

it('palette rows mark their kind', () => {
    const bench = { items: [mk('skill:Concise'), mk('mcp:jira')], lens: 'project' as const,
                    lenses: ['project', 'user'] as const, setLens: () => {}, place: async () => {} };
    render(<BenchPalette bench={bench as any} open onClose={() => {}} />);
    expect(mark(screen.getByTestId('palette-row-skill:Concise'))).toHaveAttribute('data-kind', 'skill');
    expect(mark(screen.getByTestId('palette-row-mcp:jira'))).toHaveAttribute('data-kind', 'mcp');
});

it('Find columns and hits mark their kind; the MCP registry is the only server column', async () => {
    expect(columnKind('mcp')).toBe('mcp');
    for (const k of ['folder', 'git', 'aim']) expect(columnKind(k)).toBe('skill');
    const hit = (source: string, id: string) => ({ source, id, name: id, description: '', installed: false, missing_requires: [] });
    const find = jest.fn(async (q: string) => (q
        ? { q, per_source: [
            { source: 'aim', name: 'AIM', kind: 'aim', total: 1, hits: [hit('aim', 'SetA')] },
            { source: 'mcp-registry', name: 'MCP registry', kind: 'mcp', total: 1, hits: [hit('mcp-registry', 'svc-1')] } ] }
        : { needs: [], updates: [], pairs_with: [hit('local', 'gamma')] }));
    render(<BenchFind model={{ find, lens: 'project' }} />);
    expect(mark(await screen.findByTestId('hit-local:gamma'))).toHaveAttribute('data-kind', 'skill');   // pairs-with are skills
    fireEvent.change(screen.getByTestId('find-input'), { target: { value: 'x' } });
    await screen.findByTestId('find-columns', {}, { timeout: DEBOUNCE_MS + 1000 });
    const aim = screen.getByTestId('find-col-aim');
    const mcp = screen.getByTestId('find-col-mcp-registry');
    expect(aim).toHaveAttribute('data-kind', 'skill');
    expect(mcp).toHaveAttribute('data-kind', 'mcp');
    expect(mark(screen.getByTestId('hit-aim:SetA'))).toHaveAttribute('data-kind', 'skill');
    expect(mark(screen.getByTestId('hit-mcp-registry:svc-1'))).toHaveAttribute('data-kind', 'mcp');
    expect(aim).toHaveTextContent('skills · aim');
    expect(mcp).toHaveTextContent('servers');
});

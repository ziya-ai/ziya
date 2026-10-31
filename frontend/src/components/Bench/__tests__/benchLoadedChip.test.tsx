/**
 * @jest-environment jsdom
 *
 * A within-reach MCP server the conversation has LOADED (design/mcp-on-demand.md)
 * says so on its card: the whole server, or how many of its tools.  The
 * value comes from the server view-model (``loaded`` on the item, read from
 * the chat record's ``loadedMcp``); the card derives nothing.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { CapabilityCard } from '../CapabilityCard';
import { mk } from '../benchTestUtils';

const card = (loaded: string[] | '*' | null | undefined, state: 'ondemand' | 'always' = 'ondemand') => {
    const item = { ...mk('mcp:jira', { project: state, provenance: 'user' }), loaded };
    render(<CapabilityCard item={item} onIntent={() => {}} />);
};

it('a whole-server load reads "loaded"', () => {
    card('*');
    expect(screen.getByTestId('loaded-mcp:jira')).toHaveTextContent(/^loaded$/);
});

it('a partial load counts the tools', () => {
    card(['a', 'b', 'c']);
    expect(screen.getByTestId('loaded-mcp:jira')).toHaveTextContent('3 tools loaded');
    expect(screen.getByTestId('loaded-mcp:jira')).toHaveAttribute('title', expect.stringContaining('a, b, c'));
});

it('an unloaded or on-hand card shows no chip', () => {
    card(null);
    expect(screen.queryByTestId('loaded-mcp:jira')).toBeNull();
});

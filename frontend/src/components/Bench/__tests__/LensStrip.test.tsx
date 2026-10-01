/**
 * @jest-environment jsdom
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { LensStrip, pinnedSummary } from '../LensStrip';
import { mk } from '../benchTestUtils';

const items = () => [
    { ...mk('skill:Tests', { project: 'always', def: 'off' }), meta: { project: { via: 'migration', at: 1 } } },
    { ...mk('skill:Docs', { project: 'always', def: 'off' }), meta: { project: { via: 'hub', at: 2 } } },
    mk('skill:Other'),
];

it('the delta tooltip names each pin at that layer and how it got there', () => {
    expect(pinnedSummary(items(), 'project')).toEqual([
        'Tests · always · migrated from your previous settings',
        'Docs · always · placed in the bench',
    ]);
    expect(pinnedSummary(items(), 'user')).toEqual([]);
    render(<LensStrip lenses={['project', 'user']} lens="project" onChange={() => {}}
                      deltas={{ conversation: 0, project: 2, user: 0 }} items={items()} />);
    const delta = screen.getByTestId('lens-delta-project');
    expect(delta).toHaveTextContent('+2');
    expect(delta).toHaveAttribute('title', expect.stringContaining('migrated from your previous settings'));
    expect(screen.queryByTestId('lens-delta-user')).toBeNull();
});

it('the delta badge is not a superscript (it must not shift the tab baseline)', () => {
    render(<LensStrip lenses={['project']} lens="project" onChange={() => {}}
                      deltas={{ conversation: 0, project: 1, user: 0 }} items={items()} />);
    const delta = screen.getByTestId('lens-delta-project');
    expect(delta.tagName).not.toBe('SUP');
    expect(delta.style.lineHeight).toBe('1');
});

/**
 * Render tests for MemoryCard (design A1).
 *
 * Asserts the seam the formatter tests cannot: that the card surfaces the
 * content, layer, tags and id from MemoryCardData; that `saved` omits the
 * probation count; and that no layer colour (in particular the amber
 * LAYER_COLORS.decision) drives the card's chrome.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryCard, MEMORY_ACCENT } from '../MemoryCard';
import { LAYER_COLORS } from '../../api/memoryApi';
import type { MemoryCardData } from '../../utils/mcpFormatter';

const proposed: MemoryCardData = {
    kind: 'proposed',
    content: 'memory_propose writes to the probationary ProposalsStore.',
    layer: 'decision',
    tags: ['memory', 'lifecycle'],
    id: 'prop_a91c3f20',
    pendingCount: 13,
};

describe('MemoryCard', () => {
    it('shows header, content, layer chip, tags, id and probation count for a proposal', () => {
        render(<MemoryCard data={proposed} isDarkMode={false} />);
        expect(screen.getByText(/memory proposed/i)).toBeInTheDocument();
        expect(screen.getByTestId('memory-card-content')).toHaveTextContent(proposed.content);
        expect(screen.getByTestId('memory-card-layer')).toHaveTextContent('Decisions');
        expect(screen.getAllByTestId('memory-card-tag').map(el => el.textContent)).toEqual(['memory', 'lifecycle']);
        expect(screen.getByTestId('memory-card-pending')).toHaveTextContent('13 on probation');
        expect(screen.getByTitle('prop_a91c3f20')).toBeInTheDocument();
        expect(screen.getByTestId('memory-card')).toHaveAttribute('data-memory-kind', 'proposed');
    });

    it('renders "Memory saved" without a probation count', () => {
        render(<MemoryCard data={{ ...proposed, kind: 'saved', id: 'mem_77', pendingCount: undefined }} isDarkMode={false} />);
        expect(screen.getByText(/memory saved/i)).toBeInTheDocument();
        expect(screen.queryByText(/memory proposed/i)).toBeNull();
        expect(screen.queryByTestId('memory-card-pending')).toBeNull();
        expect(screen.getByTestId('memory-card')).toHaveAttribute('data-memory-kind', 'saved');
    });

    it('never uses the layer colour for the accent (decision layer is amber, reserved for errors)', () => {
        const { container } = render(<MemoryCard data={proposed} isDarkMode={false} />);
        const card = screen.getByTestId('memory-card');
        expect(card.style.borderLeft).toContain(MEMORY_ACCENT);
        // Amber must not appear anywhere in the card's inline styles.
        const amber = LAYER_COLORS.decision.toLowerCase();
        const styles = Array.from(container.querySelectorAll<HTMLElement>('[style]'))
            .map(el => el.getAttribute('style')!.toLowerCase());
        expect(styles.some(s => s.includes(amber))).toBe(false);
        // and the same accent applies regardless of layer
        const { container: c2 } = render(<MemoryCard data={{ ...proposed, layer: 'architecture' }} isDarkMode={false} />);
        const card2 = c2.querySelector<HTMLElement>('[data-testid="memory-card"]')!;
        expect(card2.style.borderLeft).toContain(MEMORY_ACCENT);
    });

    it('falls back to the raw layer name when the layer is unknown', () => {
        render(<MemoryCard data={{ ...proposed, layer: 'some_new_layer' }} isDarkMode />);
        expect(screen.getByTestId('memory-card-layer')).toHaveTextContent('some new layer');
    });

    it('shows the verified lock when the tool result was verified', () => {
        render(<MemoryCard data={proposed} isDarkMode verified />);
        expect(screen.getByTitle('Verified tool result')).toBeInTheDocument();
    });
});

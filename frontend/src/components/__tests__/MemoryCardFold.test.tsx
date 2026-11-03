/**
 * Fold / unfold behaviour of MemoryCard and the recall list.
 *
 * Single-memory cards open by default; the recall card opens folded with
 * the count and unfolds to one row per memory (id, layer, flag, tags,
 * text).  The toggle is the whole point of the change: before it, recall
 * and delete results were opaque in the chat.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryCard } from '../MemoryCard';
import type { MemoryCardData } from '../../utils/mcpFormatter';

const recalled: MemoryCardData = {
    kind: 'recalled', content: 'raw text', layer: '', tags: [], id: '',
    count: 3, subject: 'toolInput fallback',
    entries: [
        { id: 'm_1a2b3c', layer: 'architecture', content: 'ToolBlock never receives toolInput.', tags: ['frontend'] },
        { id: 'm_9f8e7d', layer: 'decision', flag: 'contested', content: 'Hunk headers are recomputed.', tags: [] },
        { id: 'prop_55aa', layer: 'lexicon', flag: 'probationary — unverified', content: 'A bead is a parked subtask.', tags: ['beads'] },
    ],
};

describe('MemoryCard — recall', () => {
    it('starts folded showing the count and subject, no entries', () => {
        render(<MemoryCard data={recalled} isDarkMode={false} />);
        expect(screen.getByText(/memory recalled/i)).toBeInTheDocument();
        expect(screen.getByTestId('memory-card-count')).toHaveTextContent('3 memories');
        expect(screen.getByTestId('memory-card-subject')).toHaveTextContent('toolInput fallback');
        expect(screen.getByTestId('memory-card')).toHaveAttribute('data-memory-open', 'false');
        expect(screen.queryByTestId('memory-card-entries')).toBeNull();
        expect(screen.getByTestId('memory-card-toggle')).toHaveAttribute('aria-expanded', 'false');
    });

    it('unfolds to one row per memory with id, layer, flag and tags, and folds back', () => {
        render(<MemoryCard data={recalled} isDarkMode={false} />);
        fireEvent.click(screen.getByTestId('memory-card-toggle'));
        expect(screen.getByTestId('memory-card')).toHaveAttribute('data-memory-open', 'true');
        const rows = screen.getAllByTestId('memory-card-entry');
        expect(rows).toHaveLength(3);
        expect(rows[0]).toHaveTextContent('ToolBlock never receives toolInput.');
        expect(rows[0]).toHaveTextContent('Architecture');
        expect(rows[0]).toHaveTextContent('frontend');
        expect(screen.getByTitle('m_1a2b3c')).toBeInTheDocument();
        const flags = screen.getAllByTestId('memory-card-entry-flag').map(el => el.textContent);
        expect(flags).toEqual(['contested', 'probationary — unverified']);
        fireEvent.click(screen.getByTestId('memory-card-toggle'));
        expect(screen.queryByTestId('memory-card-entries')).toBeNull();
    });

    it('with no parsed entries, unfolding shows the raw text', () => {
        render(<MemoryCard data={{ ...recalled, entries: [], count: 0, content: 'No memories found matching your search.' }} isDarkMode />);
        expect(screen.getByTestId('memory-card-count')).toHaveTextContent('0 memories');
        fireEvent.click(screen.getByTestId('memory-card-toggle'));
        expect(screen.getByTestId('memory-card-content')).toHaveTextContent('No memories found');
    });
});

describe('MemoryCard — single-memory kinds', () => {
    const base: MemoryCardData = {
        kind: 'deleted', content: 'stale fact', layer: 'architecture', tags: ['x'], id: 'm_77', reason: 'superseded',
    };

    it('deleted opens by default with the text and the reason, and folds on click', () => {
        render(<MemoryCard data={base} isDarkMode={false} />);
        expect(screen.getByText(/memory deleted/i)).toBeInTheDocument();
        expect(screen.getByTestId('memory-card-content')).toHaveTextContent('stale fact');
        expect(screen.getByTestId('memory-card-reason')).toHaveTextContent('superseded');
        expect(screen.getByTestId('memory-card-layer')).toHaveTextContent('Architecture');
        fireEvent.click(screen.getByTestId('memory-card-toggle'));
        expect(screen.queryByTestId('memory-card-content')).toBeNull();
        expect(screen.getByTestId('memory-card')).toHaveAttribute('data-memory-open', 'false');
        // Header stays visible when folded.
        expect(screen.getByText(/memory deleted/i)).toBeInTheDocument();
    });

    it('retracted shows its own header and no reason line', () => {
        render(<MemoryCard data={{ ...base, kind: 'retracted', id: 'prop_1', reason: undefined }} isDarkMode={false} />);
        expect(screen.getByText(/proposal retracted/i)).toBeInTheDocument();
        expect(screen.queryByTestId('memory-card-reason')).toBeNull();
        expect(screen.queryByTestId('memory-card-pending')).toBeNull();
    });

    it('proposed and saved keep their existing open-by-default rendering', () => {
        render(<MemoryCard data={{ ...base, kind: 'proposed', reason: undefined, pendingCount: 2 }} isDarkMode={false} />);
        expect(screen.getByTestId('memory-card')).toHaveAttribute('data-memory-open', 'true');
        expect(screen.getByTestId('memory-card-content')).toHaveTextContent('stale fact');
        expect(screen.getByTestId('memory-card-pending')).toHaveTextContent('2 on probation');
    });
});

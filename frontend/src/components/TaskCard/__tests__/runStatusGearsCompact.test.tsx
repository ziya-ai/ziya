/**
 * @jest-environment jsdom
 *
 * RunStatusGears `compact` seam (design/visual-refresh.md, stage 2): in the
 * Compact sidebar density the needs-human chips shorten to one word while
 * the full label survives in the tooltip; the default stays verbose.  Red
 * until the stage-2 diffs to RunStatusGears / runStatusVocabulary land.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import RunStatusGears from '../RunStatusGears';
import { RUN_STATUS_CHIP_LABEL, RUN_STATUS_CHIP_LABEL_COMPACT } from '../runStatusVocabulary';

const counts = { awaiting_input: 1, held: 1 };

it('default density renders the full chip labels', () => {
    render(<RunStatusGears counts={counts} />);
    expect(screen.getByText(RUN_STATUS_CHIP_LABEL.awaiting_input!)).toBeTruthy();
    expect(screen.getByText(RUN_STATUS_CHIP_LABEL.held!)).toBeTruthy();
});

it('compact renders the one-word labels', () => {
    render(<RunStatusGears counts={counts} compact />);
    expect(screen.getByText(RUN_STATUS_CHIP_LABEL_COMPACT.awaiting_input!)).toBeTruthy();
    expect(screen.getByText(RUN_STATUS_CHIP_LABEL_COMPACT.held!)).toBeTruthy();
    expect(screen.queryByText(RUN_STATUS_CHIP_LABEL.awaiting_input!)).toBeNull();
});

it('every compact label has a verbose counterpart (no status shortens to nothing)', () => {
    for (const k of Object.keys(RUN_STATUS_CHIP_LABEL_COMPACT)) {
        expect(RUN_STATUS_CHIP_LABEL[k as keyof typeof RUN_STATUS_CHIP_LABEL]).toBeTruthy();
    }
});

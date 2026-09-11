/**
 * @jest-environment jsdom
 *
 * BeadTree chip semantics + the chip→sidebar seam.
 *
 * Both indicators must mean the same thing by "amber N": N parked threads.
 * Before this the chip showed amber=parked but green=TOTAL beads (completed
 * included), while the sidebar's openBeadCount was active+parked — so one
 * tree read "3 green" in the conversation and "2 amber" in the chat list.
 *
 * Chip rule now:
 *   parked > 0            → amber, number = parked
 *   else active > 0       → green, number = active
 *   else (all done)       → dim,   number = completed (history still openable)
 *
 * Seam: after loading the tree the chip writes parked_count onto the
 * conversation's openBeadCount in ChatContext, so the sidebar agrees
 * immediately — including mid-stream, when the server poll is suppressed.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../../context/StreamingContext', () => ({
  useStreamingContext: () => ({ streamingConversations: new Set<string>() }),
}));
jest.mock('../../hooks/useBranchFromBead', () => ({
  useBranchFromBead: () => jest.fn(),
}));

const mockSetConversations = jest.fn();
jest.mock('../../context/ChatContext', () => ({
  useChatContext: () => ({ setConversations: mockSetConversations }),
}));

const mockGetBeadTree = jest.fn();
jest.mock('../../api/beadApi', () => ({
  getBeadTree: (...a: any[]) => mockGetBeadTree(...a),
  resumeBead: jest.fn(),
}));

import BeadTree from '../BeadTree';

type Status = 'active' | 'parked' | 'completed' | 'abandoned';
function tree(statuses: Status[]) {
  const beads = statuses.map((status, i) => ({
    id: `b${i}`, parent_id: null, content: `bead ${i}`, status,
    created_at: 1, message_index: null, context_hint: null,
  }));
  return {
    beads,
    active_id: beads.find(b => b.status === 'active')?.id ?? null,
    parked_count: statuses.filter(s => s === 'parked').length,
    completed_count: statuses.filter(s => s === 'completed').length,
  };
}

// jsdom normalizes inline colors to rgb().
const AMBER = 'rgb(245, 158, 11)';  // #f59e0b
const GREEN = 'rgb(16, 185, 129)';  // #10b981

/** The chip is the element carrying the branch icon; its number is its text. */
async function renderChip(statuses: Status[]) {
  mockGetBeadTree.mockResolvedValue(tree(statuses));
  render(<BeadTree conversationId="conv-1" />);
  const icon = await screen.findByRole('img', { name: 'branches' });
  return icon.parentElement as HTMLElement;
}

beforeEach(() => {
  mockGetBeadTree.mockReset();
  mockSetConversations.mockReset();
});

describe('BeadTree chip — number and color', () => {
  it('parked present → amber with the PARKED count (not total)', async () => {
    const chip = await renderChip(['active', 'parked', 'parked', 'completed']);
    expect(chip).toHaveTextContent(/^2$/);
    expect(chip.style.color).toBe(AMBER);
  });

  it('active only → green with the ACTIVE count, completed excluded', async () => {
    // The reported shape: 2 active + 1 completed rendered "3 green".
    const chip = await renderChip(['active', 'active', 'completed']);
    expect(chip).toHaveTextContent(/^2$/);
    expect(chip.style.color).toBe(GREEN);
  });

  it('all completed → neither amber nor green; shows the done count dimmed', async () => {
    const chip = await renderChip(['completed', 'completed']);
    expect(chip).toHaveTextContent(/^2$/);
    expect(chip.style.color).not.toBe(AMBER);
    expect(chip.style.color).not.toBe(GREEN);
    expect(chip.style.opacity).toBe('0.6');
  });
});

describe('BeadTree → sidebar seam (openBeadCount overlay)', () => {
  /** Apply the functional updater the chip handed to mockSetConversations. */
  function applyLastUpdate(prev: any[]) {
    const updater = mockSetConversations.mock.calls.at(-1)?.[0];
    expect(typeof updater).toBe('function');
    return updater(prev);
  }

  it('writes parked_count onto the matching conversation only', async () => {
    await renderChip(['active', 'parked', 'parked']);
    await waitFor(() => expect(mockSetConversations).toHaveBeenCalled());
    const prev = [
      { id: 'conv-1', openBeadCount: 0 },
      { id: 'conv-2', openBeadCount: 5 },
    ];
    const next = applyLastUpdate(prev);
    expect(next.find((c: any) => c.id === 'conv-1').openBeadCount).toBe(2);
    expect(next.find((c: any) => c.id === 'conv-2')).toBe(prev[1]);
  });

  it('clears a stale sidebar count when every bead is completed', async () => {
    // The observed lag: sidebar still amber "2" from mid-turn beads that the
    // model has since completed.
    await renderChip(['completed', 'completed']);
    await waitFor(() => expect(mockSetConversations).toHaveBeenCalled());
    const next = applyLastUpdate([{ id: 'conv-1', openBeadCount: 2 }]);
    expect(next[0].openBeadCount).toBe(0);
  });

  it('returns the SAME array when the count already matches (no state churn)', async () => {
    await renderChip(['parked']);
    await waitFor(() => expect(mockSetConversations).toHaveBeenCalled());
    const prev = [{ id: 'conv-1', openBeadCount: 1 }];
    expect(applyLastUpdate(prev)).toBe(prev);
  });
});

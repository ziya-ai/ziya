/**
 * LessonsPanel — the card learning-history surface.
 *
 * The seams that matter:
 *   - gating: a card with zero ledger records renders NOTHING (the
 *     deck badge is the invitation; a lesson-less card carries no
 *     extra chrome);
 *   - fetch-on-expand: no request until the user opens the panel;
 *   - revert wiring: the button posts the record's (patch_hash,
 *     block_id) — the content-hash key, not an index — and then
 *     calls onReverted so the owner reloads the card;
 *   - pre-image-less records render a DISABLED revert (they 409 on
 *     the server; the disabled state says so up front).
 *
 * The mock handles below MUST keep their `mock` PREFIX.  jest.mock()
 * factories are hoisted above const initialisation, so Jest rejects a
 * factory referencing any out-of-scope variable — with the single
 * exception of names prefixed (not suffixed) with `mock`.  Named
 * `lessonsMock`/`revertMock`, this suite threw at module load and
 * every test in it silently never ran, so the panel read as covered
 * by 137 lines that had never once executed.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

jest.mock('antd', () => {
  const React = require('react');
  return {
    Button: ({ children, loading, ...rest }: any) =>
      React.createElement('button', rest, children),
    Tag: ({ children }: any) => React.createElement('span', null, children),
    Tooltip: ({ children }: any) => React.createElement(React.Fragment, null, children),
    message: { success: jest.fn(), error: jest.fn() },
  };
});

const mockLessons = jest.fn();
const mockRevert = jest.fn();
jest.mock('../../../services/taskCardApi', () => ({
  taskCardApi: {
    lessons: (...a: unknown[]) => mockLessons(...a),
    revertLesson: (...a: unknown[]) => mockRevert(...a),
  },
}));

import { LessonsPanel } from '../LessonsPanel';

const APPLIED_RECORD = {
  card_id: 'c1', block_id: 'b-abc12345', revision: 0,
  verdict: 'revise', rationale: 'was vague', lesson: 'name the file',
  applied: true, persisted: true,
  patch: { 't-1': { instructions: 'v2 text' } },
  pre_image: { 't-1': { instructions: 'v1 text' } },
  patch_hash: 'hash-1', ts: 1700000000,
};

function mount(over: Partial<React.ComponentProps<typeof LessonsPanel>> = {}) {
  const onReverted = jest.fn();
  const utils = render(
    <LessonsPanel
      projectId="p1"
      cardId="c1"
      lessonCount={1}
      onReverted={onReverted}
      {...over}
    />,
  );
  return { ...utils, onReverted };
}

/** jsdom does not fire native toggle; simulate the handler's contract. */
function openPanel(container: HTMLElement) {
  const details = container.querySelector('details')!;
  details.open = true;
  fireEvent(details, new Event('toggle', { bubbles: true }));
}

beforeEach(() => {
  mockLessons.mockReset();
  mockRevert.mockReset();
});

describe('LessonsPanel', () => {
  it('renders nothing when the card has no ledger history', () => {
    const { container } = mount({ lessonCount: 0 });
    expect(container.querySelector('.tc-lessons-panel')).toBeNull();
    expect(mockLessons).not.toHaveBeenCalled();
  });

  it('renders nothing without a saved card id', () => {
    const { container } = mount({ cardId: null });
    expect(container.querySelector('.tc-lessons-panel')).toBeNull();
  });

  it('does not fetch until expanded, then fetches once', async () => {
    mockLessons.mockResolvedValue({
      card_id: 'c1', count: 1, edits_applied: 1, lessons: [APPLIED_RECORD],
    });
    const { container } = mount();
    expect(mockLessons).not.toHaveBeenCalled();
    openPanel(container);
    await waitFor(() => expect(mockLessons).toHaveBeenCalledTimes(1));
    expect(mockLessons).toHaveBeenCalledWith('p1', 'c1');
    expect(await screen.findByText('name the file')).toBeInTheDocument();
  });

  it('shows before/after text for an applied revision', async () => {
    mockLessons.mockResolvedValue({
      card_id: 'c1', count: 1, edits_applied: 1, lessons: [APPLIED_RECORD],
    });
    const { container } = mount();
    openPanel(container);
    expect(await screen.findByText('v1 text')).toBeInTheDocument();
    expect(screen.getByText('v2 text')).toBeInTheDocument();
  });

  it('revert posts the content-hash key and calls onReverted', async () => {
    mockLessons.mockResolvedValue({
      card_id: 'c1', count: 1, edits_applied: 1, lessons: [APPLIED_RECORD],
    });
    mockRevert.mockResolvedValue({ success: true, card_id: 'c1', block_id: 'b-abc12345' });
    const { container, onReverted } = mount();
    openPanel(container);
    const btn = await screen.findByText('Revert');
    fireEvent.click(btn);
    await waitFor(() => expect(mockRevert).toHaveBeenCalledWith(
      'p1', 'c1', { patch_hash: 'hash-1', block_id: 'b-abc12345' },
    ));
    await waitFor(() => expect(onReverted).toHaveBeenCalled());
  });

  it('disables revert for a record without a pre-image', async () => {
    mockLessons.mockResolvedValue({
      card_id: 'c1', count: 1, edits_applied: 1,
      lessons: [{ ...APPLIED_RECORD, pre_image: undefined }],
    });
    const { container } = mount();
    openPanel(container);
    const btn = await screen.findByText('Revert');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(mockRevert).not.toHaveBeenCalled();
  });
});

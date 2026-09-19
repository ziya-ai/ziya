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

  it('renders a judge failure as an error with its raw-reply excerpt, not as an accept', async () => {
    // A judge outage used to be ledgered as verdict "accept" and shown
    // as a green lesson row; the row must now name the failure and
    // expose enough of the reply to diagnose it.
    mockLessons.mockResolvedValue({
      card_id: 'c1', count: 1, edits_applied: 0, judge_errors: 1,
      lessons: [{
        card_id: 'c1', block_id: 'b-err', revision: 0,
        verdict: 'error', error: 'unparseable',
        rationale: 'judge unparseable: no JSON object in reply',
        reply_excerpt: '{"verdict": "revise", "rationale": "the instr',
        reply_len: 8123, applied: false, ts: 1700000000,
      }],
    });
    const { container } = mount();
    openPanel(container);
    expect(await screen.findByText('judge error: unparseable')).toBeInTheDocument();
    expect(screen.getByText('{"verdict": "revise", "rationale": "the instr')).toBeInTheDocument();
    expect(screen.getByText(/1 judge error/)).toBeInTheDocument();
    expect(screen.queryByText('accept')).toBeNull();
    expect(screen.queryByText('Revert')).toBeNull();
  });

  it('folds consecutive stop verdicts into one streak row that expands to its members', async () => {
    // The GFX Stage 2 shape: one block, N stops, N wordings, one cause.
    const stops = [3, 2, 1].map(i => ({
      card_id: 'c1', block_id: 'b-5cc1081c', run_id: `r${i}`, revision: 0,
      verdict: 'stop', rationale: `wording ${i}`, ts: 1700000000 + i * 86400,
    }));
    mockLessons.mockResolvedValue({
      card_id: 'c1', count: 3, edits_applied: 0, judge_errors: 0,
      stop_streaks: {
        'b-5cc1081c': {
          count: 3, first_ts: stops[2].ts, last_ts: stops[0].ts,
          run_ids: ['r1', 'r2', 'r3'],
          rationales: ['wording 3', 'wording 2', 'wording 1'],
        },
      },
      lessons: stops,
    });
    const { container } = mount({ lessonCount: 3 });
    openPanel(container);
    expect(await screen.findByText('stopped 3 runs in a row')).toBeInTheDocument();
    expect(screen.getByText(/blocked by environment 3 runs running/)).toBeInTheDocument();
    // one streak row, not three plain rows
    expect(container.querySelectorAll('.tc-lesson-streak')).toHaveLength(1);
    // the members are all present (inside the details), nothing hidden
    expect(screen.getAllByText(/^wording [123]$/)).toHaveLength(4); // 3 members + lead
    expect(screen.getAllByText('stop')).toHaveLength(3);
  });
});

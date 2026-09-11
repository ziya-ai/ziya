/**
 * A run that waits on a PERSON must be OBVIOUS in the conversation row.
 *
 * Before this, every status rendered as a 12px gear in one of nine colours
 * with a hover tooltip as its only text.  A row with four gear colours
 * carried no reading at a glance — and two of those statuses,
 * 'awaiting_input' (a question put to the user) and 'held' (an
 * infrastructure fault that needs fixing and a resume), are ones where
 * nothing advances until the user notices.  Those now render as filled
 * chips with visible text; everything else stays a gear.  The row's one
 * structural distinction is therefore "you must act" vs "the machine
 * is/was doing something".
 *
 * These tests assert the visible surface (rendered text in the row), not
 * the colour map, because a vocabulary entry nobody renders is exactly
 * the failure that shipped.  Both data paths are covered: the open chat
 * (bindings) and a closed chat (project-wide status counts).  A chip that
 * only appears for the open conversation would miss the case that matters
 * most — the user is elsewhere, which is why they have not acted.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { RunStatusGears } from '../RunStatusGears';
import {
  RUN_STATUS_NEEDS_HUMAN, RUN_STATUS_CHIP_LABEL, RUN_STATUS_CHIP_TEXT,
  RUN_STATUS_FILL, statusClusters, clustersFromCounts, RUN_STATUS_ORDER,
} from '../runStatusVocabulary';
import type { TaskBinding } from '../../../types/task_binding';
import { displayStatus } from '../runStatusVocabulary';

describe('a held run whose Ask is still open reads as waiting on an answer', () => {
  // reconcile_stale_runs turns an unanswered awaiting_input run into held
  // across a restart, keeping the question.  Rendered literally that is
  // the violet "fix & resume" chip — visible, but the wrong instruction.
  it('displayStatus remaps held+open ask to awaiting_input and nothing else', () => {
    expect(displayStatus('held', true)).toBe('awaiting_input');
    expect(displayStatus('held', false)).toBe('held');
    for (const s of RUN_STATUS_ORDER) {
      if (s === 'held') continue;
      expect(displayStatus(s, true)).toBe(s);
    }
  });

  it('bindings path: a held binding with has_open_ask renders the ask chip, not the hold chip', () => {
    const b: TaskBinding = {
      id: 'a', chat_id: 'c1', card_id: 'k1', run_id: 'run-a', created_at: 1,
      run_status: 'held', has_open_ask: true,
    };
    render(<RunStatusGears bindings={[b]} />);
    const chips = screen.getAllByTestId('run-needs-human');
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveAttribute('data-status', 'awaiting_input');
    expect(chips[0]).toHaveTextContent('Waiting on you');
    expect(screen.queryByText(/Held/)).toBeNull();
  });

  it('bindings path: a held binding WITHOUT an open ask keeps the hold chip', () => {
    const b: TaskBinding = {
      id: 'a', chat_id: 'c1', card_id: 'k1', run_id: 'run-a', created_at: 1,
      run_status: 'held',
    };
    render(<RunStatusGears bindings={[b]} />);
    expect(screen.getByTestId('run-needs-human')).toHaveAttribute('data-status', 'held');
  });
});
import type { RunStatus } from '../../../types/task_run';

const binding = (id: string, run_status: string): TaskBinding => ({
  id, chat_id: 'c1', card_id: 'k1', run_id: `run-${id}`, created_at: 1, run_status,
});

const NEEDS_HUMAN: RunStatus[] = ['awaiting_input', 'held'];
const ASK_LABEL = RUN_STATUS_CHIP_LABEL.awaiting_input!;
const HELD_LABEL = RUN_STATUS_CHIP_LABEL.held!;

// Relative luminance per WCAG; enough to check the chip text is on the
// right side of its fill without pulling in a contrast library.
const lum = (hex: string) => {
  const c = (i: number) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * c(1) + 0.7152 * c(3) + 0.0722 * c(5);
};
const contrast = (a: string, b: string) => {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
};

describe('vocabulary: which statuses wait on the reader', () => {
  it('flags awaiting_input and held, and nothing else', () => {
    for (const s of RUN_STATUS_ORDER) {
      expect(RUN_STATUS_NEEDS_HUMAN[s]).toBe(NEEDS_HUMAN.includes(s));
    }
  });

  it('every needs-human status has a distinct chip label', () => {
    const labels = NEEDS_HUMAN.map(s => RUN_STATUS_CHIP_LABEL[s]);
    for (const l of labels) expect(l).toBeTruthy();
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('chip text is legible on its fill (WCAG AA for small bold text)', () => {
    // The two fills sit on opposite sides of mid-grey, which is why the
    // text colour is per status: one constant cannot pass on both.
    for (const s of NEEDS_HUMAN) {
      expect(contrast(RUN_STATUS_CHIP_TEXT[s], RUN_STATUS_FILL[s])).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('both cluster builders carry the flag', () => {
    const fromBindings = statusClusters([
      binding('a', 'awaiting_input'), binding('h', 'held'), binding('b', 'done'),
    ]);
    const fromCounts = clustersFromCounts({ awaiting_input: 1, held: 1, done: 1 });
    for (const clusters of [fromBindings, fromCounts]) {
      expect(clusters.find(c => c.status === 'awaiting_input')?.needsHuman).toBe(true);
      expect(clusters.find(c => c.status === 'held')?.needsHuman).toBe(true);
      expect(clusters.find(c => c.status === 'done')?.needsHuman).toBe(false);
    }
  });
});

describe('RunStatusGears renders a labelled chip for a run waiting on a human', () => {
  it('open chat (bindings): visible text, not just a tooltip', () => {
    render(<RunStatusGears bindings={[binding('a', 'awaiting_input')]} />);
    const chip = screen.getByTestId('run-needs-human');
    expect(chip).toHaveTextContent(ASK_LABEL);
    expect(chip).toHaveAttribute('role', 'status');
    expect(chip).toHaveAttribute('aria-label', 'Task waiting on you');
  });

  it('closed chat (index counts): the same chip', () => {
    render(<RunStatusGears counts={{ awaiting_input: 1 }} />);
    expect(screen.getByTestId('run-needs-human')).toHaveTextContent(ASK_LABEL);
  });

  it('an infrastructure hold is a chip too, with its own label', () => {
    render(<RunStatusGears counts={{ held: 1 }} />);
    const chip = screen.getByTestId('run-needs-human');
    expect(chip).toHaveTextContent(HELD_LABEL);
    expect(chip).toHaveAttribute('data-status', 'held');
    expect(chip).toHaveAttribute('aria-label', 'Task held');
  });

  it('ask and hold on one row are two chips the reader can tell apart by text', () => {
    render(<RunStatusGears counts={{ awaiting_input: 1, held: 1 }} />);
    const chips = screen.getAllByTestId('run-needs-human');
    expect(chips).toHaveLength(2);
    expect(chips.map(c => c.textContent)).toEqual(
      expect.arrayContaining([ASK_LABEL, HELD_LABEL]),
    );
  });

  it('counts two or more in the label', () => {
    render(<RunStatusGears counts={{ awaiting_input: 2 }} />);
    const chip = screen.getByTestId('run-needs-human');
    expect(chip).toHaveTextContent(`2 × ${ASK_LABEL}`);
    expect(chip).toHaveAttribute('aria-label', '2 tasks waiting on you');
  });

  it('is not ALSO drawn as a gear — one indicator per status', () => {
    render(<RunStatusGears counts={{ awaiting_input: 1, held: 1 }} />);
    expect(screen.getAllByLabelText('Task waiting on you')).toHaveLength(1);
    expect(screen.getAllByLabelText('Task held')).toHaveLength(1);
  });

  it('positive control: machine-state statuses still render as gears with no visible text', () => {
    render(<RunStatusGears counts={{ done: 1, failed: 1, paused: 1 }} />);
    expect(screen.queryByTestId('run-needs-human')).toBeNull();
    expect(screen.getByLabelText('Task done')).toBeInTheDocument();
    expect(screen.getByLabelText('Task failed')).toBeInTheDocument();
    expect(screen.getByLabelText('Task paused')).toBeInTheDocument();
    expect(screen.queryByText(ASK_LABEL)).toBeNull();
    expect(screen.queryByText(HELD_LABEL)).toBeNull();
  });

  it('chips come first when mixed with gears, so they cannot be clipped behind successes', () => {
    render(<RunStatusGears counts={{ done: 3, held: 1 }} />);
    const chip = screen.getByTestId('run-needs-human');
    const gear = screen.getByLabelText('3 tasks done');
    // compareDocumentPosition: bit 4 = "other follows this"
    expect(chip.compareDocumentPosition(gear) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

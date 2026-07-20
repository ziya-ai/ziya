/**
 * @jest-environment jsdom
 *
 * Action-guard tests for BeadPeekDrawer (design/bead-backlog-browser.md):
 *   - Resume is disabled while the target conversation is streaming.
 *   - Jump-to-seam and Branch are hidden when the bead has no recorded
 *     seam (can_branch is false — Bead.message_index is nullable).
 *   - Abandon triggers the onAbandon callback (which the browser wires to
 *     setBeadStatus); Restore (shown once abandoned) triggers onRestore.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));

import BeadPeekDrawer from '../BeadPeekDrawer';
import type { BacklogItem } from '../../../api/backlogApi';

function makeItem(overrides: Partial<BacklogItem> = {}): BacklogItem {
  return {
    bead: {
      id: 'bead-1',
      parent_id: null,
      content: 'Investigate flaky retry',
      status: 'parked',
      created_at: Date.now() - 1000,
      message_index: 5,
      context_hint: null,
    },
    conversation_id: 'conv-1',
    conversation_title: 'Retry logic overhaul',
    folder_id: null,
    breadcrumb: ['Retry logic overhaul', 'Investigate flaky retry'],
    descendant_parked_count: 0,
    seam_snippet: { role: 'assistant', text: 'Here is the seam context.' },
    age_ms: 1000,
    can_branch: true,
    origin: null,
    ...overrides,
  };
}

function noop() {}

describe('BeadPeekDrawer action guards', () => {
  test('Resume is enabled when the target conversation is not streaming', () => {
    render(
      <BeadPeekDrawer
        item={makeItem()}
        open={true}
        onClose={noop}
        isTargetStreaming={false}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={noop}
      />
    );

    const resumeBtn = screen.getByRole('button', { name: /resume/i });
    expect(resumeBtn).toBeEnabled();
  });

  test('Resume is disabled while the target conversation is streaming', () => {
    render(
      <BeadPeekDrawer
        item={makeItem()}
        open={true}
        onClose={noop}
        isTargetStreaming={true}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={noop}
      />
    );

    const resumeBtn = screen.getByRole('button', { name: /resume/i });
    expect(resumeBtn).toBeDisabled();
  });

  test('a disabled Resume button does not invoke onResume when clicked', () => {
    const onResume = jest.fn();
    render(
      <BeadPeekDrawer
        item={makeItem()}
        open={true}
        onClose={noop}
        isTargetStreaming={true}
        onResume={onResume}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={noop}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /resume/i }));
    expect(onResume).not.toHaveBeenCalled();
  });

  test('Jump and Branch are shown when the bead has a recorded seam (can_branch true)', () => {
    render(
      <BeadPeekDrawer
        item={makeItem({ can_branch: true })}
        open={true}
        onClose={noop}
        isTargetStreaming={false}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={noop}
      />
    );

    expect(screen.getByRole('button', { name: /jump to seam/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /branch/i })).toBeInTheDocument();
  });

  test('Jump and Branch are hidden when can_branch is false (no seam)', () => {
    render(
      <BeadPeekDrawer
        item={makeItem({ can_branch: false })}
        open={true}
        onClose={noop}
        isTargetStreaming={false}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={noop}
      />
    );

    expect(screen.queryByRole('button', { name: /jump to seam/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /branches/i })).not.toBeInTheDocument();
  });

  // NOTE: antd's accessible-name computation prefixes a button's visible
  // text with its icon's aria-label (e.g. "delete Abandon"), so these
  // assertions intentionally do NOT anchor the regex to the full string.
  test('clicking Abandon invokes onAbandon with the item', () => {
    const onAbandon = jest.fn();
    const item = makeItem();
    render(
      <BeadPeekDrawer
        item={item}
        open={true}
        onClose={noop}
        isTargetStreaming={false}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={onAbandon}
        onRestore={noop}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /abandon/i }));
    expect(onAbandon).toHaveBeenCalledWith(item);
  });

  test('an abandoned bead shows Restore instead of Abandon, and it invokes onRestore', () => {
    const onRestore = jest.fn();
    const item = makeItem({ bead: { ...makeItem().bead, status: 'abandoned' } });
    render(
      <BeadPeekDrawer
        item={item}
        open={true}
        onClose={noop}
        isTargetStreaming={false}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={onRestore}
      />
    );

    expect(screen.queryByRole('button', { name: /abandon/i })).not.toBeInTheDocument();
    const restoreBtn = screen.getByRole('button', { name: /restore/i });
    fireEvent.click(restoreBtn);
    expect(onRestore).toHaveBeenCalledWith(item);
  });

  test('an abandoned bead also hides Resume (only parked<->abandoned is offered here)', () => {
    const item = makeItem({ bead: { ...makeItem().bead, status: 'abandoned' } });
    render(
      <BeadPeekDrawer
        item={item}
        open={true}
        onClose={noop}
        isTargetStreaming={false}
        onResume={noop}
        onBranch={noop}
        onJump={noop}
        onAbandon={noop}
        onRestore={noop}
      />
    );

    expect(screen.queryByRole('button', { name: /play-circle resume/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^resume$/i })).not.toBeInTheDocument();
  });
});

/**
 * @jest-environment jsdom
 *
 * ConsentPreflight — a git plan is read as a sentence plus a labelled grid,
 * not as JSON.  Fixture is the exact push plan app/utils/git_ops.py emits
 * (plan_push), as it appeared in the consent prompt that motivated this.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ConsentPreflight, humanizeKey, summarizeGit } from '../ConsentPreflight';

jest.mock('../consent-preflight.css', () => ({}), { virtual: true });

const pushPlan = {
  op: 'push',
  remote: 'origin',
  branch: 'dcohn/sat-bfd-mac-gate',
  set_upstream: false,
  new_remote_branch: true,
  commits: ['d1797750 fix(SWM): SAT - gate path-protect LOC and handover release'],
};

describe('ConsentPreflight', () => {
  it('summarizes a push plan in one sentence and lists the commit with its sha', () => {
    render(<ConsentPreflight tool="git" op="push" detail={pushPlan} />);
    const summary = screen.getByText(/Push 1 commit from/);
    expect(summary.textContent).toBe(
      'Push 1 commit from dcohn/sat-bfd-mac-gate to origin, creating the remote branch.');
    // Grid rows carry humanized labels; the op itself is not repeated.
    expect(screen.getByText('New remote branch')).toBeInTheDocument();
    expect(screen.queryByText('Op')).toBeNull();
    expect(screen.getByText('d1797750')).toHaveClass('consent-pf__sha');
    expect(screen.getByText(/gate path-protect LOC/)).toBeInTheDocument();
    // Booleans are words, not JSON literals.
    expect(screen.getByText('✓ yes')).toBeInTheDocument();
    expect(screen.getByText('no')).toBeInTheDocument();
    expect(screen.queryByText(/"new_remote_branch"/)).toBeNull();
  });

  it('keeps the raw plan one click away', () => {
    render(<ConsentPreflight tool="git" op="push" detail={pushPlan} />);
    fireEvent.click(screen.getByText('Raw plan'));
    expect(screen.getByText(/"new_remote_branch": true/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Hide JSON'));
    expect(screen.queryByText(/"new_remote_branch": true/)).toBeNull();
  });

  it('renders a commit message as a block and {path, change} rows readably', () => {
    render(<ConsentPreflight tool="git" op="add" detail={{
      op: 'add', to_stage: [{ path: 'a.py', change: 'modified' }], refused: [],
      counts: { to_stage: 1, refused: 0 },
    }} />);
    expect(screen.getByText('Stage 1 change.')).toBeInTheDocument();
    expect(screen.getByText('a.py')).toBeInTheDocument();
    expect(screen.getByText('(modified)')).toBeInTheDocument();
    expect(screen.getByText('none')).toBeInTheDocument();
  });

  it('an editable message block is a textarea that reports edits, and reset clears them', () => {
    const onEdit = jest.fn();
    const detail = { op: 'commit', branch: 'main', files: ['a.py'], message: 'feat: model text\n' };
    const { rerender } = render(
      <ConsentPreflight tool="git" op="commit" detail={detail} editable={['message']} edits={{}} onEdit={onEdit} />);
    const box = screen.getByLabelText('Edit message') as HTMLTextAreaElement;
    expect(box.tagName).toBe('TEXTAREA');
    expect(box.value).toBe('feat: model text');           // trailing newline dropped
    expect(screen.queryByText('Message', { selector: 'dt' })).toBeNull(); // not also a grid row
    fireEvent.change(box, { target: { value: 'fix: human text' } });
    expect(onEdit).toHaveBeenLastCalledWith('message', 'fix: human text');

    // Controlled: the parent feeds the edit back in.
    rerender(<ConsentPreflight tool="git" op="commit" detail={detail} editable={['message']}
      edits={{ message: 'fix: human text' }} onEdit={onEdit} />);
    expect((screen.getByLabelText('Edit message') as HTMLTextAreaElement).value).toBe('fix: human text');
    expect(screen.getByText(/· edited/)).toBeInTheDocument();
    // Typing back to the original clears the edit rather than sending it.
    fireEvent.change(screen.getByLabelText('Edit message'), { target: { value: 'feat: model text' } });
    expect(onEdit).toHaveBeenLastCalledWith('message', undefined);
    fireEvent.click(screen.getByText('reset'));
    expect(onEdit).toHaveBeenLastCalledWith('message', undefined);
  });

  it('without onEdit the message stays a read-only block', () => {
    render(<ConsentPreflight tool="git" op="commit" editable={['message']}
      detail={{ op: 'commit', branch: 'main', files: [], message: 'feat: x\n' }} />);
    expect(screen.queryByLabelText('Edit message')).toBeNull();
    expect(screen.getByText(/feat: x/).tagName).toBe('PRE');
  });

  it('summarizes a fixup plan with its target and the fold warning', () => {
    render(<ConsentPreflight tool="git" op="fixup" detail={{
      op: 'fixup', branch: 'main', target: 'a1b2c3d4e5', target_subject: 'add b',
      files: ['b.py'], partially_committed: [], commits_after_target: ['c0ffee1 add c'],
      message: 'fixup! add b\n', fold_in: 'GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash a1b2c3d~1',
    }} />);
    expect(screen.getByText(/Commit 1 file as a fixup of/).textContent).toBe(
      'Commit 1 file as a fixup of a1b2c3d (add b); 1 commit after it. History is not rewritten.');
    expect(screen.getByText('c0ffee1')).toHaveClass('consent-pf__sha');
  });

  it('shows no sentence for raw args or unknown tools, and labels the toggle accordingly', () => {
    render(<ConsentPreflight tool="shell" op="run" detail={{ command: 'ls' }} isArgs />);
    expect(screen.queryByText(/\.$/)).toBeNull();
    expect(screen.getByText('Command')).toBeInTheDocument();
    expect(screen.getByText('Raw arguments')).toBeInTheDocument();
    expect(summarizeGit('nonsense', {})).toBeNull();
    expect(humanizeKey('set_upstream')).toBe('Set upstream');
  });
});

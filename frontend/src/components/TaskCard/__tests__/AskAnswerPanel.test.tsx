/**
 * AskAnswerPanel — the control that answers the Ask checkpoint a run is
 * holding at.  This is the load-bearing gap the Ask feature shipped with:
 * the backend could drive a run to 'awaiting_input' but nothing in the
 * browser could answer it.  These tests assert the two answer shapes and
 * that each maps to the right (decision, answer) the backend expects
 * (block_executor::_execute_ask — approve continues and binds the answer;
 * reject produces a failed artifact).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { AskAnswerPanel } from '../AskAnswerPanel';
import type { PendingAsk } from '../../../types/task_run';

const freeText: PendingAsk = {
  block_id: 'b-ask-1', question: 'Proceed to prod?', choices: [], opened_at: 1,
};
const withChoices: PendingAsk = {
  block_id: 'b-ask-2', question: 'Which environment?',
  choices: ['staging', 'prod'], opened_at: 1,
};

describe('AskAnswerPanel', () => {
  it('shows the question', () => {
    render(<AskAnswerPanel pending={freeText} onAnswer={jest.fn()} />);
    expect(screen.getByText('Proceed to prod?')).toBeInTheDocument();
  });

  it('free text: Approve sends approve with the typed answer', () => {
    const onAnswer = jest.fn();
    render(<AskAnswerPanel pending={freeText} onAnswer={onAnswer} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'go ahead' } });
    fireEvent.click(screen.getByText(/Approve/));
    expect(onAnswer).toHaveBeenCalledWith('approve', 'go ahead');
  });

  it('free text: Reject sends reject with the typed reason', () => {
    const onAnswer = jest.fn();
    render(<AskAnswerPanel pending={freeText} onAnswer={onAnswer} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'not safe' } });
    fireEvent.click(screen.getByText('Reject'));
    expect(onAnswer).toHaveBeenCalledWith('reject', 'not safe');
  });

  it('choices: renders a button per choice and no free-text box', () => {
    render(<AskAnswerPanel pending={withChoices} onAnswer={jest.fn()} />);
    expect(screen.getByText('staging')).toBeInTheDocument();
    expect(screen.getByText('prod')).toBeInTheDocument();
    // The free-text branch must not also render — the two are exclusive.
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('choices: clicking a choice approves with that choice as the answer', () => {
    const onAnswer = jest.fn();
    render(<AskAnswerPanel pending={withChoices} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByText('prod'));
    expect(onAnswer).toHaveBeenCalledWith('approve', 'prod');
  });

  it('choices: Reject is still offered (a human can always decline)', () => {
    const onAnswer = jest.fn();
    render(<AskAnswerPanel pending={withChoices} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByText('Reject'));
    expect(onAnswer).toHaveBeenCalledWith('reject', '');
  });

  it('busy disables the actions so a double click cannot double-answer', () => {
    const onAnswer = jest.fn();
    render(<AskAnswerPanel pending={withChoices} busy onAnswer={onAnswer} />);
    fireEvent.click(screen.getByText('prod'));
    expect(onAnswer).not.toHaveBeenCalled();
  });

  it('falls back to a legible prompt when the question is blank', () => {
    render(
      <AskAnswerPanel
        pending={{ ...freeText, question: '' }}
        onAnswer={jest.fn()}
      />,
    );
    expect(screen.getByText(/waiting for your input/i)).toBeInTheDocument();
  });
});

/**
 * failureReasonOf mirrors app/utils/task_run_read.failure_reason_of (and
 * task_executor._failure_reason for the verdict case) so the run tile says
 * WHY a block failed in the same words the task_finished event and the
 * task_run_read tool use.  The nightly audit card's "partial: lane 1B skipped"
 * was on disk and in the journal but on no screen until the run was opened
 * in a tool; the detail panel reads it from the artifact.
 */
import { failureReasonOf } from '../failureReason';
import type { Artifact } from '../../../types/task_card';

const base: Artifact = {
  summary: 's', decisions: [], outputs: [], tokens: 0, tool_calls: 0, duration_ms: 0, created_at: 0,
};

describe('failureReasonOf', () => {
  it('is null for a passing or absent artifact', () => {
    expect(failureReasonOf(null)).toBeNull();
    expect(failureReasonOf({ ...base, failed: false, signature: 'x' })).toBeNull();
  });

  it('phrases a self-assessment verdict with its rationale', () => {
    expect(failureReasonOf({
      ...base, failed: true, signature: 'self_assessment_partial',
      self_assessment: { objective_met: 'partial', rationale: ' lane 1B skipped ' },
    })).toBe('self_assessment objective_met=partial: lane 1B skipped');
    expect(failureReasonOf({
      ...base, failed: true, signature: 'self_assessment_false',
      self_assessment: { objective_met: 'false' },
    })).toBe('self_assessment objective_met=false');
  });

  it('returns the decision whose prefix is the signature kind', () => {
    expect(failureReasonOf({
      ...base, failed: true, signature: 'output_contract:finding',
      decisions: ['something else', 'output_contract: required part "finding" was not emitted'],
    })).toBe('output_contract: required part "finding" was not emitted');
    expect(failureReasonOf({
      ...base, failed: true, signature: 'empty_summary', decisions: ['empty_summary: no prose'],
    })).toBe('empty_summary: no prose');
  });

  it("falls back to a container's first failed-child decision, then the signature", () => {
    expect(failureReasonOf({
      ...base, failed: true, signature: null,
      decisions: ['child a ok', 'child b failed: self_assessment_partial'],
    })).toBe('child b failed: self_assessment_partial');
    expect(failureReasonOf({ ...base, failed: true, signature: 'weird:sig' })).toBe('weird:sig');
    expect(failureReasonOf({ ...base, failed: true })).toBe('failed');
  });
});

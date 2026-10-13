/**
 * One line saying why an artifact is failed.
 *
 * Mirrors app/utils/task_run_read.failure_reason_of (and, for the verdict
 * case, task_executor._failure_reason, which puts the same string on the
 * task_finished event) so the run tile, the task_run_read tool and the
 * schedule editor all describe a failure in the same words.  Derived from
 * the persisted artifact rather than the live event so a run opened days
 * later gets the same answer as one watched live.
 *
 * Null for a passing artifact.
 */
import type { Artifact } from '../../types/task_card';

export function failureReasonOf(art: Artifact | null | undefined): string | null {
  if (!art || !art.failed) return null;
  const sig = art.signature || '';
  const sa = art.self_assessment || null;
  if (sig.startsWith('self_assessment_') && sa) {
    const verdict = sa.objective_met ?? 'unknown';
    const rationale = (sa.rationale || '').trim();
    return `self_assessment objective_met=${verdict}` + (rationale ? `: ${rationale}` : '');
  }
  // Non-verdict failures record their explanation as a decision whose
  // prefix is the signature kind ("empty_summary:", "output_contract:").
  const kind = sig.split(':', 1)[0];
  const decisions = art.decisions || [];
  if (kind) {
    const hit = decisions.find(d => d.startsWith(kind));
    if (hit) return hit;
  }
  // A container's failure is one of its children's; the first decision
  // naming a failed child is the most specific thing on record.
  const child = decisions.find(d => d.includes('failed'));
  if (child) return child;
  return sig || 'failed';
}

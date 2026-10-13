/**
 * Finding cards in the run inspector (design/typed-findings.md, step 8).
 *
 * Seam: ArtifactViewer (what the run tile mounts) -> PartBody ->
 * isFindingPart -> FindingCard.  Asserts on what the user sees: the
 * finding's own words and numbers, where each number came from, and that
 * the raw JSON is still reachable.
 */

import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ArtifactViewer } from '../ArtifactViewer';
import type { ArtifactPart } from '../../../types/task_card';
import { distributionView, isFindingPart, numberView } from '../../../utils/findingView';

jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => <div>{markdown}</div>,
}));

const FINDING = {
  id: 'F-RETRY-FLUSH',
  title: 'Retry queue flushed on config reload',
  verdict: 'confirmed',
  signal: 'timeouts after every config reload',
  impact: {
    population: { n_total: { value: 5000 }, n_affected: { claim: 'ns.s1.n' } },
    per_unit: { claim: 'ns.s1.timeouts', field: 'distribution' },
    fleet_total: { low: { value: 200000 }, mid: { claim: 'ns.s1.timeouts' }, high: { value: 300000 }, unit: 'host-s/day' },
  },
  cross_validation: [{ method: 'split_half', outcome: 'agrees', detail: 'both halves within 3%' }],
  evidence: [{ claim: 'ns.s1.timeouts', role: 'primary' }],
  next_steps: ['make the reload preserve the queue'],
};

function findingPart(over: Partial<ArtifactPart> = {}): ArtifactPart {
  return {
    part_type: 'data', name: 'finding', data: FINDING,
    contract: 'finding', schema_id: 'project:audit-finding',
    schema_lineage: ['project:audit-finding', 'ziya:finding/v1'],
    schema_sha256: 'a'.repeat(64),
    resolved_claims: [
      { pointer: '/impact/population/n_affected', claim_id: 'ns.s1.n', version: 1, status: 'verified', value: 640 },
      { pointer: '/impact/per_unit', claim_id: 'ns.s1.timeouts', version: 2, status: 'verified', field: 'distribution',
        unit: 'host-s/day', value: { n: 640, mean: 512.8, median: 418.4, p10: 95.5, p90: 910.2, sd: 300 } },
      { pointer: '/impact/fleet_total/mid', claim_id: 'ns.s1.timeouts', version: 2, status: 'verified',
        unit: 'host-s/day', value: 248000 },
      { pointer: '/evidence/0', claim_id: 'ns.s1.timeouts', version: 2, status: 'verified', value: 248000 },
    ],
    unverified_numbers: ['/impact/population/n_total', '/impact/fleet_total/low', '/impact/fleet_total/high'],
    ...over,
  } as ArtifactPart;
}

describe('FindingCard through ArtifactViewer', () => {
  it('renders a contracted finding as a card, not JSON', () => {
    render(<ArtifactViewer parts={[findingPart()]} projectId="p" runId="r" />);
    expect(screen.getByTestId('finding-card')).toBeTruthy();
    expect(screen.getByText('Retry queue flushed on config reload')).toBeTruthy();
    expect(screen.getByText('confirmed')).toBeTruthy();
    expect(screen.getByText('F-RETRY-FLUSH')).toBeTruthy();
    expect(screen.queryByText(/"verdict": "confirmed"/)).toBeNull();
  });

  it('shows claimed numbers resolved, with their provenance, and literals as such', () => {
    render(<ArtifactViewer parts={[findingPart()]} projectId="p" runId="r" />);
    const fleet = screen.getByText('248,000 host-s/day');
    expect(fleet.getAttribute('title')).toMatch(/ledger claim ns\.s1\.timeouts v2 \(verified\)/);
    expect(screen.getByText('418.4')).toBeTruthy();              // per-unit median from the claim's distribution
    const affected = screen.getByText('Affected').closest('tr') as HTMLElement;
    expect(within(affected).getByText('640').getAttribute('title')).toMatch(/ns\.s1\.n v1/);
    expect(screen.getByText('200,000').getAttribute('title')).toMatch(/literal, not a ledger claim/);
    expect(screen.getByText(/3 literal numbers \(not ledger claims\)/)).toBeTruthy();
    expect(screen.getByText(/primary: ns\.s1\.timeouts \(verified\)/)).toBeTruthy();
    expect(screen.getByText('agrees')).toBeTruthy();
  });

  it('keeps the raw JSON one click away', () => {
    render(<ArtifactViewer parts={[findingPart()]} projectId="p" runId="r" />);
    fireEvent.click(screen.getByText('JSON'));
    expect(screen.getByText(/"verdict": "confirmed"/)).toBeTruthy();
  });

  it('leaves an uncontracted data part as JSON (positive control)', () => {
    const plain = { part_type: 'data', name: 'finding', data: FINDING } as ArtifactPart;
    render(<ArtifactViewer parts={[plain]} projectId="p" runId="r" />);
    expect(screen.queryByTestId('finding-card')).toBeNull();
    expect(screen.getByText(/"verdict": "confirmed"/)).toBeTruthy();
  });
});

describe('findingView', () => {
  const part = findingPart();
  it('recognises a finding by lineage, not by name', () => {
    expect(isFindingPart(part)).toBe(true);
    expect(isFindingPart({ ...part, schema_lineage: ['project:other'], schema_id: 'project:other' })).toBe(false);
    expect(isFindingPart({ ...part, schema_lineage: null, schema_id: 'ziya:finding/v1' })).toBe(true);
    expect(isFindingPart({ ...part, part_type: 'text' })).toBe(false);
  });

  it('distinguishes claim, literal, plain and unresolved numbers', () => {
    expect(numberView({ claim: 'ns.s1.timeouts' }, '/impact/fleet_total/mid', part)?.kind).toBe('claim');
    expect(numberView({ value: 3, unit: 's' }, '/x', part)).toMatchObject({ kind: 'literal', text: '3 s' });
    expect(numberView(0.05, '/x', part)).toMatchObject({ kind: 'plain', text: '0.05' });
    expect(numberView({ claim: 'ns.gone' }, '/nowhere', part)?.kind).toBe('unresolved');
    expect(numberView(undefined, '/x', part)).toBeNull();
  });

  it('reads a distribution field by field or from one claim', () => {
    const fromClaim = distributionView({ claim: 'ns.s1.timeouts' }, '/impact/per_unit', part);
    expect(fromClaim?.median?.text).toBe('418.4');
    expect(fromClaim?.unit).toBe('host-s/day');
    const literal = distributionView({ median: { value: 5 }, p10: { value: 1 }, p90: { value: 9 }, unit: 's' }, '/d', part);
    expect([literal?.median?.text, literal?.p10?.text, literal?.unit]).toEqual(['5', '1', 's']);
  });
});

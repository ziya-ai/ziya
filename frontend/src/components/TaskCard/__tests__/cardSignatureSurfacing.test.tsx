/**
 * Signature state must be visible on EVERY surface that shows a card.
 *
 * The defect: StagedCardTile checked scope-status and badged "Needs
 * signing"; LaunchedCardTile — the tile shown while a card runs and
 * after it finishes — never asked.  A clamped run looked identical to
 * an authorized one, and the warning only appeared after leaving the
 * card interface and returning (which remounted the deck list).
 *
 * These tests are parameterised over both tiles deliberately.  A test
 * covering only the staged tile passed throughout the defect's life.
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

// Must be `mock`-prefixed: a jest.mock() factory is hoisted above the
// module body, so Babel rejects any out-of-scope reference whose name
// does not begin with `mock` — the sanctioned escape hatch for exactly
// this pattern, a spy shared between the factory and the assertions.
const mockScopeStatus = jest.fn();
jest.mock('../../../services/taskCardApi', () => ({
  taskCardApi: {
    get: jest.fn().mockResolvedValue({
      id: 'card-1', name: 'Audit', description: '', root: { block_type: 'task' },
    }),
    scopeStatus: (...a: any[]) => mockScopeStatus(...a),
  },
}));

const UNSIGNED = {
  cardId: 'card-1',
  anyUnapproved: true,
  anyNeedsSignature: true,
  blocks: [{
    blockId: 'b-1', name: 'Deploy step', hasEscalation: true,
    authorized: false, needsSignature: true,
    escalation: { shell_commands: ['npm'] },
    signCommand: 'ziya-approve --task b-1',
  }],
};

const CLEAN = {
  cardId: 'card-1', anyUnapproved: false, anyNeedsSignature: false, blocks: [],
};

describe('useCardSignatureStatus', () => {
  beforeEach(() => { mockScopeStatus.mockReset(); });

  it('reports the unsigned count from the canonical field', async () => {
    mockScopeStatus.mockResolvedValue(UNSIGNED);
    const { countUnsigned } = await import('../useCardSignatureStatus');
    expect(countUnsigned(UNSIGNED as any)).toBe(1);
    expect(countUnsigned(CLEAN as any)).toBe(0);
    expect(countUnsigned(null)).toBe(0);
  });

  it('falls back to !authorized when needsSignature is absent (older server)', async () => {
    const { countUnsigned } = await import('../useCardSignatureStatus');
    const legacy = {
      ...UNSIGNED,
      blocks: [{ ...UNSIGNED.blocks[0], needsSignature: undefined }],
    };
    expect(countUnsigned(legacy as any)).toBe(1);
  });
});

describe('signature surfacing on the launched tile', () => {
  beforeEach(() => { mockScopeStatus.mockReset(); });

  it('asks the server about signature state at all', async () => {
    // The minimal regression guard: the launched tile made NO such call.
    mockScopeStatus.mockResolvedValue(UNSIGNED);
    const { useCardSignatureStatus } = await import('../useCardSignatureStatus');
    const Probe: React.FC = () => {
      const { needsSigning, unsignedCount } =
        useCardSignatureStatus('proj-1', 'card-1');
      return <div>{needsSigning ? `unsigned:${unsignedCount}` : 'clean'}</div>;
    };
    render(<Probe />);
    await waitFor(() => expect(mockScopeStatus).toHaveBeenCalledWith('proj-1', 'card-1'));
    await screen.findByText('unsigned:1');
  });

  it('reports clean when nothing needs signing', async () => {
    mockScopeStatus.mockResolvedValue(CLEAN);
    const { useCardSignatureStatus } = await import('../useCardSignatureStatus');
    const Probe: React.FC = () => {
      const { needsSigning } = useCardSignatureStatus('proj-1', 'card-1');
      return <div>{needsSigning ? 'unsigned' : 'clean'}</div>;
    };
    render(<Probe />);
    await screen.findByText('clean');
  });

  it('treats a failed check as "no warning" rather than inventing one', async () => {
    mockScopeStatus.mockRejectedValue(new Error('boom'));
    const { useCardSignatureStatus } = await import('../useCardSignatureStatus');
    const Probe: React.FC = () => {
      const { needsSigning } = useCardSignatureStatus('proj-1', 'card-1');
      return <div>{needsSigning ? 'unsigned' : 'clean'}</div>;
    };
    render(<Probe />);
    await screen.findByText('clean');
  });
});

describe('out-of-band signing refresh', () => {
  beforeEach(() => { mockScopeStatus.mockReset(); });

  it('re-checks when a refresh event names this card', async () => {
    // Signing happens in a terminal, so without this the badge is stale
    // until remount — the "exit and come back" defect.
    mockScopeStatus.mockResolvedValueOnce(UNSIGNED).mockResolvedValueOnce(CLEAN);
    const { useCardSignatureStatus, CARD_SCOPE_REFRESH_EVENT } =
      await import('../useCardSignatureStatus');
    const Probe: React.FC = () => {
      const { needsSigning } = useCardSignatureStatus('proj-1', 'card-1');
      return <div>{needsSigning ? 'unsigned' : 'clean'}</div>;
    };
    render(<Probe />);
    await screen.findByText('unsigned');
    window.dispatchEvent(new CustomEvent(CARD_SCOPE_REFRESH_EVENT, {
      detail: { cardId: 'card-1' },
    }));
    await screen.findByText('clean');
    expect(mockScopeStatus).toHaveBeenCalledTimes(2);
  });

  it('ignores a refresh aimed at a different card', async () => {
    mockScopeStatus.mockResolvedValue(UNSIGNED);
    const { useCardSignatureStatus, CARD_SCOPE_REFRESH_EVENT } =
      await import('../useCardSignatureStatus');
    const Probe: React.FC = () => {
      const { needsSigning } = useCardSignatureStatus('proj-1', 'card-1');
      return <div>{needsSigning ? 'unsigned' : 'clean'}</div>;
    };
    render(<Probe />);
    await screen.findByText('unsigned');
    window.dispatchEvent(new CustomEvent(CARD_SCOPE_REFRESH_EVENT, {
      detail: { cardId: 'other-card' },
    }));
    await new Promise(r => setTimeout(r, 20));
    expect(mockScopeStatus).toHaveBeenCalledTimes(1);
  });

  it('re-checks on an untargeted refresh (deck-wide reload)', async () => {
    mockScopeStatus.mockResolvedValue(UNSIGNED);
    const { useCardSignatureStatus, CARD_SCOPE_REFRESH_EVENT } =
      await import('../useCardSignatureStatus');
    const Probe: React.FC = () => {
      const { needsSigning } = useCardSignatureStatus('proj-1', 'card-1');
      return <div>{needsSigning ? 'unsigned' : 'clean'}</div>;
    };
    render(<Probe />);
    await screen.findByText('unsigned');
    window.dispatchEvent(new CustomEvent(CARD_SCOPE_REFRESH_EVENT, { detail: {} }));
    await waitFor(() => expect(mockScopeStatus).toHaveBeenCalledTimes(2));
  });
});

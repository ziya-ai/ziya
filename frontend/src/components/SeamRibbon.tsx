/**
 * SeamRibbon — persistent inline marker rendered directly below the message
 * where a bead thread was parked (its "seam").
 *
 * Set via utils/seamHighlight by the Backlog Browser's Jump/Resume actions.
 * It answers "where exactly is the seam, and what can I do from here?":
 *   - mode 'jump'    → thread still parked: offer Resume + Branch.
 *   - mode 'resumed' → thread just resumed: confirm it, offer Branch only
 *     (the resume endpoint 400s on an already-active bead).
 * Resume flips the ribbon to 'resumed' rather than removing it, so the
 * anchor stays visible while the user reviews the pre-filled pickup message.
 * Dismiss (×) clears the marker.
 */
import React, { useState } from 'react';
import { Button, message } from 'antd';
import { PlayCircleOutlined, BranchesOutlined, CloseOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import * as beadApi from '../api/beadApi';
import { useBranchFromBead } from '../hooks/useBranchFromBead';
import { dispatchComposerInject } from '../utils/composerInject';
import {
  SeamHighlight, setSeamHighlight, clearSeamHighlight,
} from '../utils/seamHighlight';

const SeamRibbon: React.FC<{ seam: SeamHighlight }> = ({ seam }) => {
  const { isDarkMode } = useTheme();
  const branchFromBead = useBranchFromBead();
  const [busy, setBusy] = useState(false);

  const isResumed = seam.mode === 'resumed';

  const handleResume = async () => {
    setBusy(true);
    try {
      const result = await beadApi.resumeBead(seam.conversationId, seam.beadId);
      dispatchComposerInject(seam.conversationId, result.suggested_message);
      // Keep the anchor visible but flip to the resumed presentation —
      // resume on an already-active bead would 400, so the button swaps out.
      setSeamHighlight({ ...seam, mode: 'resumed' });
      message.success(`Resumed: ${result.resumed_bead.content}`);
    } catch {
      message.error('Failed to resume thread');
    } finally {
      setBusy(false);
    }
  };

  const handleBranch = async () => {
    setBusy(true);
    try {
      await branchFromBead(seam.conversationId, seam.beadId);
      clearSeamHighlight();
    } catch {
      message.error('Failed to branch from thread');
    } finally {
      setBusy(false);
    }
  };

  // Colour language: amber = parked (matches the backlog's parked chips);
  // green = resumed (matches BeadTree's active indicator).  The ribbon is
  // deliberately quiet — a tinted strip, not a modal or banner.
  const accent = isResumed ? '#10b981' : '#f59e0b';
  const bg = isResumed
    ? (isDarkMode ? 'rgba(16,185,129,0.08)' : 'rgba(16,185,129,0.06)')
    : (isDarkMode ? 'rgba(245,158,11,0.08)' : 'rgba(245,158,11,0.06)');
  const border = isResumed
    ? (isDarkMode ? '#10b98155' : '#10b98144')
    : (isDarkMode ? '#f59e0b55' : '#f59e0b44');

  return (
    <div
      data-seam-ribbon
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        margin: '4px 0 12px', padding: '8px 12px',
        border: `1px solid ${border}`, borderLeft: `3px solid ${accent}`,
        borderRadius: 6, background: bg, fontSize: 12,
      }}
    >
      <span style={{ color: accent, fontSize: 13, flexShrink: 0 }}>
        {isResumed ? '▶' : '⏸'}
      </span>
      <span style={{ color: isDarkMode ? '#e2e8f0' : '#1e293b' }}>
        {isResumed ? 'Thread resumed from here' : 'Thread parked here'}
        {' — '}<strong>{seam.label}</strong>
      </span>
      {seam.contextHint && (
        <span style={{ color: isDarkMode ? '#94a3b8' : '#64748b', fontStyle: 'italic' }}>
          {seam.contextHint}
        </span>
      )}
      <span style={{ flex: 1 }} />
      {!isResumed && (
        <Button size="small" type="primary" icon={<PlayCircleOutlined />}
          disabled={busy} onClick={handleResume}>
          Resume
        </Button>
      )}
      {seam.canBranch && (
        <Button size="small" icon={<BranchesOutlined />} disabled={busy}
          onClick={handleBranch} title="Split this thread into its own conversation from here">
          Branch from here
        </Button>
      )}
      <Button
        size="small" type="text" icon={<CloseOutlined />}
        onClick={clearSeamHighlight}
        title="Dismiss marker"
      />
    </div>
  );
};

export default SeamRibbon;

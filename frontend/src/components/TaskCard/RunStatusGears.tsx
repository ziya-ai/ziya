/**
 * RunStatusGears — per-status task-run indicators for a conversation row.
 *
 * Supersedes the single "Task running…" line the sidebar carried.  That
 * line was one bit ("something is running") derived through ``isRunOver``,
 * which meant every terminal state — done, failed, cancelled, partial,
 * held — collapsed to "not running" and rendered as nothing at all.  A
 * conversation whose overnight study died on a dead credential looked
 * identical to one that had never run anything.
 *
 * Three deliberate departures from that line:
 *
 *   1. One indicator PER STATUS, not one for the conversation.  A chat
 *      can hold several cards, and "2 done, 1 held" is a different
 *      situation from either "3 done" or "1 held" — collapsing to a
 *      single winner would hide whichever the user was looking for.
 *   2. A COUNT beside each, from 2 upward.  Suppressed at 1 because "1"
 *      next to a lone gear is noise in a narrow row.
 *   3. Animation only for genuinely live states.  A spinning glyph is how
 *      a user decides to keep waiting instead of intervening, so spinning
 *      on a stopped run is the most costly thing this component could do.
 *
 * Colours, ordering, animation and hints all come from
 * runStatusVocabulary so this cannot drift from the tile's own chrome.
 */

import React from 'react';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import Tooltip from '@mui/material/Tooltip';
import SettingsIcon from '@mui/icons-material/Settings';
import { styled } from '@mui/material/styles';
import type { TaskBinding } from '../../types/task_binding';
import { statusClusters, clustersFromCounts, showCount } from './runStatusVocabulary';

// Slower rotation than the chat-streaming spinner so it reads as
// "machinery" rather than "thinking" — the distinction that lets a user
// tell at a glance which kind of work the row is waiting on.
const SpinningGear = styled(SettingsIcon)({
  animation: 'gear-spin 4s linear infinite',
  '@keyframes gear-spin': {
    '0%': { transform: 'rotate(0deg)' },
    '100%': { transform: 'rotate(360deg)' },
  },
});

interface Props {
  /**
   * This conversation's bindings, server-enriched with run_status.
   * Optional because only the OPEN conversation has them loaded.
   */
  bindings?: ReadonlyArray<TaskBinding> | null;
  /**
   * Pre-counted statuses from the project-wide index, used when this row
   * is not the open conversation.  Ignored when ``bindings`` is present:
   * the open chat's bindings are fresher than a polled projection, and
   * preferring them keeps the row from disagreeing with the tile while a
   * run is actively changing.
   */
  counts?: Record<string, number> | null;
}

export const RunStatusGears: React.FC<Props> = ({ bindings, counts }) => {
  // Live clusters are never suppressed.  A task run drives the chat, so
  // the conversation is streaming for almost the whole time a run is
  // live; filtering animated clusters while streaming hid the gear in
  // precisely the case it exists for, leaving the chat spinner as the
  // only motion and no way to tell agent work from a plain reply.  The
  // two indicators encode different work, sit on separate rows, and
  // differ in colour and rotation period, so both in motion at once is
  // the intended reading.
  const visible = bindings && bindings.length > 0
    ? statusClusters(bindings)
    : clustersFromCounts(counts);
  if (visible.length === 0) return null;

  return (
    <Box sx={{
      display: 'flex', alignItems: 'center', gap: 0.75,
      mt: 0.5, flexWrap: 'wrap',
    }}>
      {visible.map(c => (
        <Tooltip
          key={c.status}
          title={c.count > 1 ? `${c.count} × ${c.hint}` : c.hint}
          placement="top"
        >
          <Box
            sx={{ display: 'flex', alignItems: 'center', color: c.color }}
            // The status word is in the accessible name, not only the
            // colour: a colour-only encoding is unreadable to a
            // colour-blind user and invisible to a screen reader, and
            // this row's whole job is signalling state.
            aria-label={
              c.count > 1
                ? `${c.count} tasks ${c.label}`
                : `Task ${c.label}`
            }
          >
            {c.animate
              ? <SpinningGear sx={{ fontSize: '12px' }} />
              : <SettingsIcon sx={{ fontSize: '12px' }} />}
            {showCount(c) && (
              <Typography
                variant="caption"
                sx={{ fontSize: '10px', ml: 0.25, fontWeight: 600 }}
              >
                {c.count}
              </Typography>
            )}
          </Box>
        </Tooltip>
      ))}
    </Box>
  );
};

export default RunStatusGears;

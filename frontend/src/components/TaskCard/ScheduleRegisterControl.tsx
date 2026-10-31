/**
 * "Is this card registered to fire?" — the one line every surface that
 * shows a scheduled card needs, with the actions that change the answer.
 *
 *   not registered — will not fire  [Register] [Fire now]
 *   registered · [Manage schedules] [Fire now]
 *   registered · disabled · [Manage schedules]
 *
 * Used by ScheduleHistory (the Schedule block's status line in the editor)
 * and TaskCardInlineTile (a card staged in chat, which never reaches the
 * deck).  The tile was the hole: it showed "Schedule: daily at 02:00" and
 * a Run button that did a one-off, and the model's skill text had told
 * the user staging the card registered it.
 */
import React from 'react';
import { Button, message } from 'antd';
import { taskCardApi, type ScheduleStateRecord } from '../../services/taskCardApi';
import { scheduleApi } from '../../services/scheduleApi';
import { registrationPhrase, SCHEDULES_OPEN_EVENT } from './scheduleCadence';

const REFRESH_MS = 30_000;

/** Poll GET …/schedule-state; `reload()` forces a re-read after an action. */
export function useScheduleState(projectId?: string, cardId?: string) {
  const [rec, setRec] = React.useState<ScheduleStateRecord | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [key, setKey] = React.useState(0);
  React.useEffect(() => {
    if (!projectId || !cardId || cardId === 'draft') { setRec(null); return; }
    let cancelled = false;
    const load = async () => {
      try {
        const r = await taskCardApi.scheduleState(projectId, cardId);
        if (!cancelled) { setRec(r); setErr(null); }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => { cancelled = true; window.clearInterval(t); };
  }, [projectId, cardId, key]);
  return { rec, err, reload: () => setKey(k => k + 1) };
}

interface Props {
  projectId: string;
  cardId: string;
  rec: ScheduleStateRecord | null;
  onChanged?: () => void;
  /** Offer "Fire now" (headless, counted) beside the registration state. */
  allowFire?: boolean;
}

const linkStyle = { padding: 0, height: 'auto' } as const;

export const ScheduleRegisterControl: React.FC<Props> = ({
  projectId, cardId, rec, onChanged, allowFire = true,
}) => {
  const [busy, setBusy] = React.useState<'register' | 'fire' | null>(null);
  const reg = registrationPhrase(rec);
  if (!reg.text) return null;

  // Registration copies the SAVED card's cadence; unsaved edits are not
  // seen by the server.
  const register = async () => {
    setBusy('register');
    try {
      await scheduleApi.register(projectId, cardId);
      message.success('Registered — this card will now fire on its schedule');
      onChanged?.();
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const fire = async () => {
    setBusy('fire');
    try {
      const r = await scheduleApi.fire(projectId, cardId);
      message.success(`Fired — run ${r.run_id.slice(0, 8)} started (counts as a scheduled fire)`);
      onChanged?.();
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const openSchedules = () => window.dispatchEvent(new CustomEvent(SCHEDULES_OPEN_EVENT));

  return (
    <span data-testid="schedule-register-control">
      <span data-testid="schedule-registration">{reg.text} </span>
      {reg.canRegister ? (
        <Button type="link" size="small" style={linkStyle} loading={busy === 'register'}
          onClick={() => { void register(); }}
          title="Copies this card's SAVED cadence into a registration you own. Save the card first if you changed it.">
          Register
        </Button>
      ) : (
        <Button type="link" size="small" style={linkStyle} onClick={openSchedules}>
          Manage schedules
        </Button>
      )}
      {allowFire && (
        <>
          {' '}
          <Button type="link" size="small" style={linkStyle} loading={busy === 'fire'}
            onClick={() => { void fire(); }}
            title="Run the scheduled body now, headless, and count it as a fire. Works before registering.">
            Fire now
          </Button>
        </>
      )}
    </span>
  );
};

export default ScheduleRegisterControl;

/**
 * SchedulesPanel — the Schedules tab of the Task Cards dialog.  Every card
 * registered to run on a timer in this project, and cards whose Schedule
 * block asks for a cadence nobody has registered.
 *
 * A Schedule block is only the author's request; the registration
 * (app/agents/schedule_registry.py) is what the scheduler fires, and the
 * user owns it here: pause/resume, change the schedule, fire now,
 * unregister.  None of these edit the card.
 *
 * One row per schedule, three short lines (scheduleDisplay.ts): what it
 * does, when it runs next, how it last went.  The first version was a
 * five-column table whose every cell wrapped.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Switch, Button, Tag, Empty, Tooltip, Select, InputNumber, Input, Checkbox,
  Space, Dropdown, Modal, Alert, message,
} from 'antd';
import {
  ReloadOutlined, MoreOutlined, ThunderboltOutlined, CheckCircleFilled,
  CloseCircleFilled, PauseCircleFilled, MinusCircleOutlined, LoadingOutlined,
  ExclamationCircleFilled,
} from '@ant-design/icons';
import {
  scheduleApi, type ScheduleCadence, type ScheduleListing,
  type ScheduleRegistrationRow, type UnregisteredScheduleRequest,
} from '../../services/scheduleApi';
import { cadenceError, normalizeCadence } from './scheduleCadence';
import {
  humanizeCadence, lastOutcome, lockLine, nextLine, runCountText,
  type LineTone, type OutcomeKind,
} from './scheduleDisplay';

const REFRESH_MS = 30_000;

export const CadenceForm: React.FC<{
  value: ScheduleCadence; onChange: (c: ScheduleCadence) => void;
}> = ({ value, onChange }) => {
  const set = (patch: Partial<ScheduleCadence>) => onChange({ ...value, ...patch });
  const mode = value.schedule_mode ?? 'interval';
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space wrap>
        <Select value={mode} style={{ width: 220 }} onChange={m => set({ schedule_mode: m })}
          options={[
            { value: 'interval', label: 'every N minutes/hours/days' },
            { value: 'daily_at', label: 'daily at HH:MM' },
            { value: 'at', label: 'once at a date and time' },
            { value: 'cron', label: 'cron expression' },
          ]} />
        {mode === 'interval' && (
          <>
            <InputNumber min={1} value={value.schedule_interval_value ?? 1}
              onChange={v => set({ schedule_interval_value: typeof v === 'number' ? v : 1 })} />
            <Select value={value.schedule_interval_unit ?? 'hours'} style={{ width: 110 }}
              onChange={u => set({ schedule_interval_unit: u })}
              options={[
                { value: 'minutes', label: 'minutes' },
                { value: 'hours', label: 'hours' },
                { value: 'days', label: 'days' },
              ]} />
          </>
        )}
        {mode === 'daily_at' && (
          <Input type="time" style={{ width: 130 }} value={value.schedule_daily_at ?? '09:00'}
            onChange={e => set({ schedule_daily_at: e.target.value })} />
        )}
        {mode === 'at' && (
          <Input type="datetime-local" style={{ width: 220 }} value={value.schedule_at_iso ?? ''}
            onChange={e => set({ schedule_at_iso: e.target.value || null })} />
        )}
        {mode === 'cron' && (
          <Input style={{ width: 200 }} placeholder="*/15 * * * *" value={value.schedule_cron ?? ''}
            onChange={e => set({ schedule_cron: e.target.value })} />
        )}
      </Space>
      <Checkbox checked={value.schedule_catch_up !== false}
        onChange={e => set({ schedule_catch_up: e.target.checked })}>
        run once on recovery if a slot was missed
      </Checkbox>
      <Space wrap>
        <span>max runs</span>
        <InputNumber min={1} placeholder="∞" value={value.schedule_max_runs ?? null}
          onChange={v => set({ schedule_max_runs: typeof v === 'number' ? v : null })} />
        <span>if a run is still live</span>
        <Select value={value.schedule_concurrency ?? 'skip'} style={{ width: 180 }}
          onChange={c => set({ schedule_concurrency: c })}
          options={[
            { value: 'skip', label: 'skip the slot' },
            { value: 'queue', label: 'queue until it ends' },
            { value: 'parallel', label: 'fire anyway' },
          ]} />
      </Space>
      {mode === 'cron' && (
        <div style={{ fontSize: 12, opacity: 0.7 }}>
          5 fields: minute hour day-of-month month day-of-week. The expression is checked when you save.
        </div>
      )}
    </Space>
  );
};

const OUTCOME_ICON: Record<OutcomeKind, React.ReactNode> = {
  never: <MinusCircleOutlined style={{ opacity: 0.5 }} />,
  unknown: <MinusCircleOutlined style={{ opacity: 0.5 }} />,
  ok: <CheckCircleFilled style={{ color: '#52c41a' }} />,
  failed: <CloseCircleFilled style={{ color: '#ff4d4f' }} />,
  held: <PauseCircleFilled style={{ color: '#9254de' }} />,
  partial: <ExclamationCircleFilled style={{ color: '#faad14' }} />,
  cancelled: <MinusCircleOutlined style={{ color: '#faad14' }} />,
  running: <LoadingOutlined style={{ color: '#1677ff' }} />,
};
const TONE_COLOR: Record<LineTone, string | undefined> = {
  normal: undefined, muted: undefined, warn: '#d48806', error: '#cf1322',
};
const LOCK_DOT = { ok: '#52c41a', info: '#1677ff', warn: '#faad14' } as const;

const rowStyle: React.CSSProperties = {
  display: 'flex', gap: 12, alignItems: 'flex-start', padding: '10px 12px',
  borderRadius: 6, border: '1px solid rgba(128,128,128,0.22)',
};
const sub: React.CSSProperties = { fontSize: 12, lineHeight: '18px' };
const dim: React.CSSProperties = { opacity: 0.7 };
const clamp: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };
const sectionHead: React.CSSProperties = {
  fontSize: 11, fontWeight: 600, letterSpacing: 0.4, opacity: 0.55, margin: '4px 0 2px',
};

interface EditTarget {
  kind: 'register' | 'edit';
  cardId: string;
  cardName: string;
  cadence: ScheduleCadence;
}

interface Props {
  projectId: string;
  /** Poll only while the tab is showing. */
  active: boolean;
  /** Switch the dialog to the Cards tab with this card open. */
  onOpenCard?: (cardId: string) => void;
}

export const SchedulesPanel: React.FC<Props> = ({ projectId, active, onOpenCard }) => {
  const [listing, setListing] = useState<ScheduleListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [now, setNow] = useState(() => Date.now());

  const reload = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      setListing(await scheduleApi.list(projectId));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setNow(Date.now());
    }
  }, [projectId]);

  useEffect(() => {
    if (!active) return;
    void reload();
    const t = window.setInterval(() => { void reload(); }, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [active, reload]);

  const run = async (cardId: string, fn: () => Promise<unknown>, done?: string): Promise<boolean> => {
    setBusy(cardId);
    try {
      await fn();
      if (done) message.success(done);
      await reload();
      return true;
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const toggle = (id: string) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const editCadence = editing ? normalizeCadence(editing.cadence) : null;
  const editError = editCadence ? cadenceError(editCadence) : null;
  const saveEdit = async () => {
    if (!editing || !editCadence || editError) return;
    const { kind, cardId } = editing;
    const ok = await run(cardId, () => (kind === 'register'
      ? scheduleApi.register(projectId, cardId, { cadence: editCadence })
      : scheduleApi.update(projectId, cardId, { cadence: editCadence })),
      kind === 'register' ? 'Registered' : 'Schedule changed');
    if (ok) setEditing(null);
  };

  const confirmUnregister = (r: ScheduleRegistrationRow) => Modal.confirm({
    title: `Stop running "${r.card_name ?? r.card_id}" on a schedule?`,
    content: 'The card itself is not changed. Its run count starts over if you register it again.',
    okText: 'Unregister', okButtonProps: { danger: true },
    onOk: () => run(r.card_id, () => scheduleApi.unregister(projectId, r.card_id), 'Unregistered'),
  });

  const nameEl = (id: string, name: string | null, draft: boolean, missing = false) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
      {missing || !onOpenCard
        ? <span style={{ fontWeight: 600, ...clamp }}>{name ?? '(deleted card)'}</span>
        : <a onClick={() => onOpenCard(id)} title="Open card" style={{ fontWeight: 600, ...clamp }}>{name}</a>}
      {draft && (
        <Tooltip title="Lives only in the conversation that staged it; still runs while registered">
          <Tag bordered={false} style={{ marginInlineEnd: 0, flexShrink: 0 }}>chat only</Tag>
        </Tooltip>
      )}
    </div>
  );

  const renderRegistration = (r: ScheduleRegistrationRow) => {
    const next = nextLine(r, now);
    const out = lastOutcome(r, now);
    const open = expanded.has(r.card_id);
    return (
      <div key={r.card_id} style={{ ...rowStyle, opacity: r.enabled ? 1 : 0.75 }} data-testid="schedule-row">
        <Tooltip title={r.enabled ? 'Pause' : 'Resume'}>
          <Switch size="small" style={{ marginTop: 3, flexShrink: 0 }} checked={r.enabled}
            loading={busy === r.card_id} disabled={r.card_missing}
            onChange={v => { void run(r.card_id, () => scheduleApi.update(projectId, r.card_id, { enabled: v })); }} />
        </Tooltip>
        <div style={{ flex: 1, minWidth: 0 }}>
          {nameEl(r.card_id, r.card_name, r.card_draft, r.card_missing)}
          <div style={{ ...sub, ...clamp }}>
            <span style={dim}>{humanizeCadence(r.cadence)} · </span>
            <span style={{ ...(next.tone === 'normal' || next.tone === 'muted' ? dim : {}), color: TONE_COLOR[next.tone] }}
              data-testid={next.tone === 'error' ? 'schedule-problem' : undefined}>
              {next.text}
            </span>
          </div>
          <div style={{ ...sub, display: 'flex', gap: 6, alignItems: 'baseline', minWidth: 0 }}>
            <span style={{ flexShrink: 0 }}>{OUTCOME_ICON[out.kind]}</span>
            <span style={{ flexShrink: 0 }}>{out.label}</span>
            {out.reason && (
              <span role="button" tabIndex={0} title={open ? 'Show less' : out.reason}
                onClick={() => toggle(r.card_id)}
                onKeyDown={e => { if (e.key === 'Enter') toggle(r.card_id); }}
                style={{ ...dim, cursor: 'pointer', minWidth: 0, flex: '0 1 auto', ...(open ? { whiteSpace: 'normal' } : clamp) }}>
                — {out.reason}
              </span>
            )}
            <span style={{ ...dim, flexShrink: 0, marginLeft: 'auto', paddingLeft: 8 }}>{runCountText(r)}</span>
          </div>
        </div>
        <Space size={2} style={{ flexShrink: 0 }}>
          <Tooltip title="Run it now as the scheduler would; counts as a run">
            <Button size="small" icon={<ThunderboltOutlined />} disabled={r.card_missing}
              loading={busy === r.card_id}
              onClick={() => { void run(r.card_id, () => scheduleApi.fire(projectId, r.card_id), 'Started — see the card\u2019s runs'); }}>
              Fire now
            </Button>
          </Tooltip>
          <Dropdown trigger={['click']} menu={{ items: [
            { key: 'edit', label: 'Change schedule…', disabled: r.card_missing,
              onClick: () => setEditing({ kind: 'edit', cardId: r.card_id, cardName: r.card_name ?? r.card_id, cadence: r.cadence }) },
            { key: 'open', label: 'Open card', disabled: r.card_missing || !onOpenCard,
              onClick: () => onOpenCard?.(r.card_id) },
            { type: 'divider' },
            { key: 'unregister', label: 'Unregister', danger: true, onClick: () => confirmUnregister(r) },
          ] }}>
            <Button size="small" type="text" icon={<MoreOutlined />} aria-label="More actions" />
          </Dropdown>
        </Space>
      </div>
    );
  };

  const renderRequest = (u: UnregisteredScheduleRequest) => (
    <div key={u.card_id} style={{ ...rowStyle, borderStyle: 'dashed' }} data-testid="schedule-request">
      <div style={{ flex: 1, minWidth: 0 }}>
        {nameEl(u.card_id, u.card_name, u.card_draft)}
        <div style={{ ...sub, ...dim }}>Asks for: {humanizeCadence(normalizeCadence(u.requested))}</div>
      </div>
      <Button size="small" type="primary" loading={busy === u.card_id} style={{ flexShrink: 0 }}
        onClick={() => setEditing({ kind: 'register', cardId: u.card_id, cardName: u.card_name, cadence: u.requested })}>
        Register…
      </Button>
    </div>
  );

  const lock = lockLine(listing?.scheduler);
  const regs = listing?.registrations ?? [];
  const reqs = listing?.unregistered ?? [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 24 }}>
        {lock && (
          <span data-testid="scheduler-lock" style={{ fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 6,
            color: lock.tone === 'warn' ? '#d48806' : undefined, opacity: lock.tone === 'warn' ? 1 : 0.75 }}>
            <span style={{ width: 8, height: 8, borderRadius: 4, background: LOCK_DOT[lock.tone] }} />
            {lock.text}
          </span>
        )}
        <span style={{ flex: 1 }} />
        <Tooltip title="Only registered cards run on a schedule. A Schedule block in a card is a request until it is registered.">
          <span style={{ fontSize: 12, opacity: 0.55, cursor: 'help' }}>How this works</span>
        </Tooltip>
        <Tooltip title="Refresh">
          <Button size="small" type="text" icon={<ReloadOutlined />} loading={loading} onClick={() => { void reload(); }} />
        </Tooltip>
      </div>
      {loadError && <Alert type="error" showIcon message={`Could not load schedules: ${loadError}`} />}
      {listing && regs.length === 0 && reqs.length === 0 && (
        <Empty style={{ marginTop: 40 }}
          description="Nothing is scheduled. Add a Schedule block to a card, save it, then register it here or from the block." />
      )}
      {regs.length > 0 && <div style={sectionHead}>SCHEDULED · {regs.length}</div>}
      {regs.map(renderRegistration)}
      {reqs.length > 0 && <div style={{ ...sectionHead, marginTop: 12 }}>NOT REGISTERED — WON'T RUN · {reqs.length}</div>}
      {reqs.map(renderRequest)}
      <Modal
        title={editing ? `${editing.kind === 'register' ? 'Register' : 'Change schedule'}: ${editing.cardName}` : ''}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => { void saveEdit(); }}
        okText={editing?.kind === 'register' ? 'Register' : 'Save'}
        okButtonProps={{ disabled: !!editError }}
        confirmLoading={!!editing && busy === editing.cardId}
        destroyOnClose
      >
        {editing && editCadence && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <CadenceForm value={editCadence} onChange={c => setEditing({ ...editing, cadence: c })} />
            <div style={{ fontSize: 12, opacity: 0.7 }}>
              {editError ?? `Runs: ${humanizeCadence(editCadence)}. The card itself is not changed.`}
            </div>
          </Space>
        )}
      </Modal>
    </div>
  );
};

export default SchedulesPanel;

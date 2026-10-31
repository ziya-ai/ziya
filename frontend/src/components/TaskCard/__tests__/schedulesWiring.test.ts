/**
 * Wiring for the Schedules surface.  Static source assertions, as in
 * jobRailWiring.test.ts: the API, the panel and the helpers can each be
 * right while nothing opens them -- which is exactly how the schedule
 * registry first shipped with no way to reach it.
 *
 * Schedules live as a tab of the Task Cards dialog (Tasks -> Schedules),
 * not as a top-level header button.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');
const exists = (rel: string) => fs.existsSync(path.join(__dirname, '..', '..', '..', rel));

describe('entry point: the Task Cards dialog', () => {
  it('App has no top-level Schedules button or modal', () => {
    const s = read('components/App.tsx');
    expect(s).not.toMatch(/schedules-button/);
    expect(s).not.toMatch(/SchedulesModal/);
    expect(exists('components/TaskCard/SchedulesModal.tsx')).toBe(false);
  });

  it('the open event opens the deck on the Schedules tab', () => {
    const s = read('components/App.tsx');
    expect(s).toMatch(/addEventListener\(SCHEDULES_OPEN_EVENT/);
    expect(s).toMatch(/setTaskCardInitialView\('schedules'\)/);
    expect(s).toMatch(/initialView=\{taskCardInitialView\}/);
  });

  it('the deck switches between Cards and Schedules and hosts the panel', () => {
    const s = read('components/TaskCard/TaskCardsLibrary.tsx');
    expect(s).toMatch(/import \{ SchedulesPanel \} from '\.\/SchedulesPanel'/);
    expect(s).toMatch(/useState<'cards' \| 'schedules'>\(initialView \?\? 'cards'\)/);
    expect(s).toMatch(/<Segmented[\s\S]*?value=\{view\}/);
    expect(s).toMatch(/view === 'schedules' \?/);
    // Already open (e.g. "Manage schedules" from a block in the editor).
    expect(s).toMatch(/addEventListener\(SCHEDULES_OPEN_EVENT/);
  });

  it('opening a card from the list switches to Cards and loads it', () => {
    const s = read('components/TaskCard/TaskCardsLibrary.tsx');
    const m = s.match(/<SchedulesPanel[\s\S]*?\/>/);
    expect(m).not.toBeNull();
    expect(m![0]).toMatch(/setView\('cards'\)/);
    expect(m![0]).toMatch(/loadCard\(id\)/);
  });

  it('the shared registration control dispatches the open event', () => {
    const ctl = read('components/TaskCard/ScheduleRegisterControl.tsx');
    expect(ctl).toMatch(/dispatchEvent\(new CustomEvent\(SCHEDULES_OPEN_EVENT\)\)/);
  });
});

describe('Schedule block', () => {
  it('shows the shared registration control (one Register path for every surface)', () => {
    const s = read('components/TaskCard/ScheduleHistory.tsx');
    expect(s).toMatch(/useScheduleState\(projectId, cardId\)/);
    expect(s).toMatch(/<ScheduleRegisterControl projectId=\{projectId\} cardId=\{cardId\} rec=\{rec\}/);
    expect(s).toMatch(/cadence_error/);
    expect(s).not.toMatch(/scheduleApi\.register\(/);
    expect(s).not.toMatch(/dispatchEvent\(new CustomEvent\(SCHEDULES_OPEN_EVENT\)\)/);
  });

  it('the shared control registers, fires now and opens the list through the schedules API', () => {
    const s = read('components/TaskCard/ScheduleRegisterControl.tsx');
    expect(s).toMatch(/scheduleApi\.register\(projectId, cardId\)/);
    expect(s).toMatch(/scheduleApi\.fire\(projectId, cardId\)/);
    expect(s).toMatch(/registrationPhrase\(rec\)/);
    expect(s).toMatch(/data-testid="schedule-registration"/);
  });

  it('a card staged in chat with a Schedule root shows the registration line', () => {
    const s = read('components/TaskCard/TaskCardInlineTile.tsx');
    expect(s).toMatch(/block_type === 'schedule' && projectId && binding\.card_id/);
    expect(s).toMatch(/data-testid="tile-schedule-line"/);
    expect(s).toMatch(/<ScheduleRegisterControl projectId=\{projectId\} cardId=\{cardId\} rec=\{rec\}/);
    expect(s).toMatch(/'Run once now' : 'Run'/);
  });

  it('no longer offers an "enabled" checkbox the scheduler ignores', () => {
    const s = read('components/TaskCard/ScheduleBlockEditor.tsx');
    expect(s).not.toMatch(/schedule_enabled: e\.target\.checked/);
    expect(s).toMatch(/schedule_catch_up: e\.target\.checked/);
  });
});

describe('SchedulesPanel', () => {
  const s = () => read('components/TaskCard/SchedulesPanel.tsx');

  it('reads the list and drives register / update / unregister / fire', () => {
    expect(s()).toMatch(/scheduleApi\.list\(projectId\)/);
    expect(s()).toMatch(/scheduleApi\.register\(projectId, /);
    expect(s()).toMatch(/scheduleApi\.update\(projectId, /);
    expect(s()).toMatch(/scheduleApi\.unregister\(projectId, /);
    expect(s()).toMatch(/scheduleApi\.fire\(projectId, r\.card_id\)/);
    expect(s()).toMatch(/normalizeCadence\(/);
  });

  it('renders rows in words, not a table of raw fields', () => {
    expect(s()).not.toMatch(/<Table\b/);
    expect(s()).toMatch(/humanizeCadence\(r\.cadence\)/);
    expect(s()).toMatch(/nextLine\(r, now\)/);
    expect(s()).toMatch(/lastOutcome\(r, now\)/);
    expect(s()).toMatch(/data-testid="schedule-problem"/);
    expect(s()).not.toMatch(/croniter/);
  });

  it('unregistering asks first', () => {
    expect(s()).toMatch(/Modal\.confirm\(/);
  });
});

/**
 * Escape colons inside gantt task names.
 *
 * Mermaid's gantt lexer reads a task as a name (\`[^:\n]+\`) followed by
 * \`:\` and the task data, so the FIRST colon ends the name.  A task named
 * "In service (contacts from 04:29)" is read as name "In service (contacts
 * from 04" with data "29) :10-10 02:16, ...", and the chart fails with
 * "Invalid date:29) :10-10 02:16" (reported 2026-10-11).
 *
 * The real separator is the first colon whose remainder parses as task
 * data.  Colons before it are rewritten to \`#58;\`, which mermaid's entity
 * encoding renders back as ":".  A line whose first colon already yields
 * valid data is returned unchanged, so every line mermaid can parse today
 * is untouched; a line where no colon yields valid data is left alone.
 */

const NON_TASK_LINE = /^(?:gantt|dateFormat|axisFormat|tickInterval|title|section|excludes|includes|todayMarker|weekday|weekend|inclusiveEndDates|topAxis|displayMode|accTitle|accDescr|click)(?=[\s:{]|$)/;

const TAG = /^(?:done|active|crit|milestone|vert)$/;
const DURATION = /^\d+(?:\.\d+)?(?:ms|s|m|h|d|w|M|y)$/;
const AFTER = /^after(?:\s+[A-Za-z_][\w-]*)+$/;
const UNTIL = /^until\s+[A-Za-z_][\w-]*$/;
const ID = /^[A-Za-z_][\w-]*$/;
// 2024-01-01, 10-10, 10-10 02:16, 2024-01-01T08:00:00, 1700000000
const DATE = /^\d[\d\-/.]*(?:[ T]\d{1,2}(?::\d{2}){1,2}(?:\.\d+)?)?$/;
// 04:20, 04:20:06
const TIME = /^\d{1,2}(?::\d{2}){1,2}(?:\.\d+)?$/;

const ESCAPED_COLON = '#58;';

function isDataItem(item: string): boolean {
  return TAG.test(item) || DURATION.test(item) || AFTER.test(item) || UNTIL.test(item)
    || ID.test(item) || DATE.test(item) || TIME.test(item);
}

/** Whether \`data\` (the text after a task's colon) parses as gantt task metadata. */
export function isGanttTaskData(data: string): boolean {
  if (typeof data !== 'string') return false;
  const items = data.split(',').map(s => s.trim());
  return items.length > 0 && items.every(i => i.length > 0 && isDataItem(i));
}

function isGantt(def: string): boolean {
  for (const raw of def.split('\n')) {
    const t = raw.trim();
    if (!t || t.startsWith('%%')) continue;
    return /^gantt\b/.test(t);
  }
  return false;
}

function escapeLine(line: string): string {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('%%') || NON_TASK_LINE.test(trimmed)) return line;
  const first = line.indexOf(':');
  if (first < 0) return line;
  for (let i = first; i >= 0; i = line.indexOf(':', i + 1)) {
    if (isGanttTaskData(line.slice(i + 1))) {
      if (i === first) return line;
      return line.slice(0, i).replace(/:/g, ESCAPED_COLON) + line.slice(i);
    }
  }
  return line;
}

/**
 * Rewrite colons inside gantt task names to \`#58;\`.  Non-gantt input is
 * returned unchanged.  Idempotent: \`#58;\` contains no colon.
 */
export function escapeGanttTaskNameColons(def: string): string {
  if (typeof def !== 'string' || !isGantt(def)) return def;
  let changed = false;
  const out = def.split('\n').map(line => {
    const fixed = escapeLine(line);
    if (fixed !== line) changed = true;
    return fixed;
  });
  if (!changed) return def;
  console.log('🔍 GANTT-COLON-FIX: escaped colon(s) inside gantt task name(s)');
  return out.join('\n');
}

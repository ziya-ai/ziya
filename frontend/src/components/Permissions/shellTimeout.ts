/**
 * Parse the Shell tab's timeout field.
 *
 * ``shell_timeout_secs`` is a per-task ceiling on one shell command (the
 * base ceiling is 300 s; containers merge it as a MAXIMUM).  It is
 * optional: blank means "unset — inherit", and so does anything that is
 * not a positive number, because a zero or negative ceiling would kill
 * every command on arrival and is never what the user meant.
 *
 * Pure so the rule is testable without rendering the dialog.
 */
export function parseShellTimeout(text: string): number | null {
  const t = (text ?? '').trim();
  if (!t || !/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Math.floor(Number(t));
  return n > 0 ? n : null;
}

/** Inverse of parseShellTimeout for the input's initial value. */
export function formatShellTimeout(secs: number | null | undefined): string {
  return secs != null && secs > 0 ? String(secs) : '';
}

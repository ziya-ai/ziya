/**
 * Reasoning display selector: derive the selector value from the two saved
 * settings (thinking_mode on/off, thinking_display tier) and back again.
 *
 * The option list comes from /api/model-capabilities (thinking_options),
 * computed per model by app/utils/thinking_options.py so the UI only ever
 * offers tiers the backend can honour.  `selectorValue` mirrors the
 * server-side `selector_value` so the two derivations cannot drift.
 */

export type ThinkingOption = 'off' | 'on' | 'quiet' | 'summary' | 'full';
export type ThinkingDisplay = 'quiet' | 'summary' | 'full';

export const DEFAULT_THINKING_DISPLAY: ThinkingDisplay = 'summary';
const DISPLAY_TIERS: ThinkingDisplay[] = ['quiet', 'summary', 'full'];

export function normalizeDisplay(value: unknown): ThinkingDisplay {
  return typeof value === 'string' && (DISPLAY_TIERS as string[]).includes(value)
    ? (value as ThinkingDisplay)
    : DEFAULT_THINKING_DISPLAY;
}

/** Which option the selector shows for a saved (mode, display) pair. */
export function selectorValue(
  thinkingMode: boolean | undefined,
  display: string | undefined,
  options: string[],
): ThinkingOption | null {
  if (!options.length) return null;
  if (!thinkingMode && options.includes('off')) return 'off';
  const d = normalizeDisplay(display);
  if (options.includes(d)) return d;
  // An unoffered tier falls back to the default tier when the group has it
  // ("full" on adaptive Claude -> "summary"), never to the quietest option,
  // which would silently hide reasoning the user asked to see.
  if (options.includes(DEFAULT_THINKING_DISPLAY)) return DEFAULT_THINKING_DISPLAY;
  if (options.includes('on')) return 'on';
  const nonOff = options.filter(o => o !== 'off');
  return (nonOff[0] ?? options[0]) as ThinkingOption;
}

export interface ThinkingSelection {
  thinking_mode: boolean;
  thinking_display: ThinkingDisplay;
}

/**
 * The settings a selector click writes.  "off"/"on" leave the tier alone so
 * toggling back restores what the user had; a tier implies mode on.
 */
export function applySelection(
  option: string,
  previousDisplay: string | undefined,
): ThinkingSelection {
  const keep = normalizeDisplay(previousDisplay);
  if (option === 'off') return { thinking_mode: false, thinking_display: keep };
  if (option === 'on') return { thinking_mode: true, thinking_display: keep };
  return { thinking_mode: true, thinking_display: normalizeDisplay(option) };
}

/**
 * Make a (mode, display) pair coherent with the offered options before it is
 * saved.  A group with no "off" (Gemini, OpenAI Responses) always reasons, so
 * a stale `thinking_mode: false` carried over from a previous model would
 * otherwise be posted and silently disable reasoning server-side; a tier the
 * group does not offer is replaced by the one the selector displays.
 */
export function coerceThinkingSettings<T extends { thinking_mode?: boolean; thinking_display?: string }>(
  values: T,
  options: string[],
): T {
  if (!options.length) return values;
  const out: T = { ...values };
  if (!options.includes('off')) out.thinking_mode = true;
  const shown = selectorValue(out.thinking_mode, out.thinking_display, options);
  if (shown && shown !== 'off' && shown !== 'on') out.thinking_display = shown;
  else out.thinking_display = normalizeDisplay(out.thinking_display);
  return out;
}

/** True when the effort/level controls should be greyed out (reasoning off). */
export function reasoningIsOff(thinkingMode: boolean | undefined, options: string[]): boolean {
  return options.includes('off') && !thinkingMode;
}

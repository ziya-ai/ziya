/**
 * The cwd-remapping step of a task-card import (ImportCwdMapModal).
 *
 * An export carries `scope.cwd` values verbatim, and an absolute one is a
 * path on the EXPORTING machine.  The executor confines cwd to the project
 * root, so on another host such a path silently falls back to the root at
 * run time.  The modal shows each distinct path with a target field; these
 * helpers turn the user's entries into the `cwd_map` the import endpoint
 * takes.
 */

/** Per source path, the target the user typed or picked ('' = unmapped). */
export type CwdTargets = Record<string, string>;

/** Start every source path unmapped. */
export function initialTargets(cwds: string[]): CwdTargets {
  const out: CwdTargets = {};
  for (const c of cwds) out[c] = '';
  return out;
}

/** The 1:1 case: every source path goes to the same local directory. */
export function applyToAll(cwds: string[], target: string): CwdTargets {
  const out: CwdTargets = {};
  for (const c of cwds) out[c] = target;
  return out;
}

/** The `cwd_map` to send: only rows with a non-blank target.  A blank row
 *  means "import unchanged", and must NOT be sent as `""` — the server
 *  reads an empty value as "clear the cwd". */
export function buildCwdMap(targets: CwdTargets): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [src, dst] of Object.entries(targets)) {
    const t = dst.trim();
    if (t) out[src] = t;
  }
  return out;
}

export const mappedCount = (targets: CwdTargets): number =>
  Object.keys(buildCwdMap(targets)).length;

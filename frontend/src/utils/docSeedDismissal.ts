/**
 * Auto-seeded documentation-file dismissal.
 *
 * The server reports which AGENTS.md / README.md files should be pre-selected
 * for a project, and FolderContext unions those keys into the selection on
 * load and on project switch.  A plain union cannot tell "the user has never
 * seen this file" from "the user deliberately unchecked it", because both
 * present identically: the key is simply absent from checkedKeys.  The result
 * was that unchecking a doc file survived being written to sessionStorage and
 * was then undone by the very next seed — every reload re-added it.
 *
 * Keeping a record of what has already been seeded resolves the ambiguity:
 * a key we have seeded before and which is now absent was removed by the
 * user, so it must not be re-added.
 */

export interface DocSeedDecision {
  /** Keys to add to the selection now. */
  additions: string[];
  /** Replacement seeded-set to persist. */
  nextSeeded: Set<string>;
}

/**
 * Decide which server-reported doc keys to seed.
 *
 * A key is added only when it is BOTH absent from \`\`checkedKeys\`\` and absent
 * from \`\`alreadySeeded\`\`.  Every key is recorded in \`\`nextSeeded\`\` regardless
 * of whether it was added — including one the user had already checked by
 * hand — so that a later uncheck of it is respected too.  Neither input is
 * mutated.
 */
export function resolveDocSeed(
  // Accepts React.Key (= string | number | bigint) so callers can pass
  // checkedKeys straight through without a cast.  Every key is coerced with
  // String() below, so the widened type needs no additional handling.
  serverKeys: ReadonlyArray<string | number | bigint>,
  checkedKeys: ReadonlyArray<string | number | bigint>,
  alreadySeeded: ReadonlySet<string>,
): DocSeedDecision {
  const checked = new Set<string>();
  for (const k of checkedKeys) checked.add(String(k));

  const nextSeeded = new Set<string>(alreadySeeded);
  const additions: string[] = [];
  const queued = new Set<string>();

  for (const raw of serverKeys) {
    const key = String(raw);
    // An empty key is not a path; it would corrupt the selection.
    if (!key) continue;
    nextSeeded.add(key);
    // Already in context — nothing to add, but now recorded as seeded.
    if (checked.has(key)) continue;
    // Seeded before and absent now: the user removed it.  Honour that.
    if (alreadySeeded.has(key)) continue;
    // Guard against duplicates in the server response.
    if (queued.has(key)) continue;
    queued.add(key);
    additions.push(key);
  }

  return { additions, nextSeeded };
}

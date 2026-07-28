/**
 * artifactGroups — group task-run output artifacts and select a layout
 * from each group's SHAPE.
 *
 * Design intent (see design/task-cards.md §Artifacts): the emitting
 * agent declares only structure — `group` ("these belong together"),
 * `label` (display name within the group), `seq` (ordering).  It never
 * declares presentation.  The viewer picks a layout as a pure function
 * of shape, so artifact shapes nobody anticipated still render
 * sensibly instead of falling through to nothing.
 *
 * No label string is magic.  A `broken`/`fixed` pair and a
 * `us-east`/`eu-west` pair are the same shape and get the same layout;
 * status colouring (when shown) comes from the recorded `status` field,
 * which the emit-time render sets factually, never from label text.
 *
 * `list` is the always-reachable fallback: any group can be viewed as a
 * list, so a layout misfire can never hide data.
 */

import type { ArtifactPart } from '../types/task_card';

export type ArtifactLayout = 'card' | 'sideBySide' | 'sequence' | 'grid' | 'list';

export interface ArtifactGroup {
  /** Group id from the emitter; '' means "ungrouped stragglers". */
  key: string;
  parts: ArtifactPart[];
  layout: ArtifactLayout;
}

/** Largest group still rendered as a grid; beyond this, use the list. */
const MAX_GRID_PARTS = 6;

/**
 * Image media types the viewer will render inline with an <img>.
 *
 * Deliberately excludes `image/svg+xml`: SVG can carry executable
 * script and artifact filenames come from model output, so the server's
 * blob route refuses to serve it inline.  Mirroring that here keeps the
 * two ends from disagreeing (a broken <img> would be the best case).
 */
const INLINE_IMAGE_MEDIA_TYPES = new Set([
  'image/png', 'image/jpeg', 'image/gif', 'image/webp',
]);

const INLINE_IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp'];

function hasSeq(p: ArtifactPart): boolean {
  // seq === 0 is meaningful; only null/undefined count as absent.
  const seq = (p as any).seq;
  return seq !== undefined && seq !== null;
}

function labelOf(p: ArtifactPart): string {
  const label = (p as any).label;
  return typeof label === 'string' ? label : '';
}

/**
 * Choose a layout from a group's shape.  Pure; order of checks is the
 * specification:
 *
 *   1. one part                  -> card
 *   2. exactly two, both labeled -> sideBySide  (the before/after look)
 *   3. any explicit seq          -> sequence
 *   4. small enough              -> grid
 *   5. otherwise                 -> list
 */
export function selectLayout(parts: ArtifactPart[]): ArtifactLayout {
  if (!parts || parts.length === 0) return 'list';
  if (parts.length === 1) return 'card';
  if (parts.length === 2 && parts.every(p => labelOf(p) !== '')) {
    return 'sideBySide';
  }
  if (parts.some(hasSeq)) return 'sequence';
  if (parts.length <= MAX_GRID_PARTS) return 'grid';
  return 'list';
}

/**
 * Bucket an artifact's `outputs` into groups, order each group's parts,
 * and attach the selected layout.
 *
 * Group order follows first appearance so the emitter's narrative order
 * is preserved; the ungrouped bucket always sorts last and always uses
 * the `list` layout (unrelated parts should not be implied to relate by
 * sharing a grid).
 */
export function groupArtifactParts(parts: ArtifactPart[] | null | undefined): ArtifactGroup[] {
  if (!parts || parts.length === 0) return [];

  const order: string[] = [];
  const buckets = new Map<string, ArtifactPart[]>();

  for (const p of parts) {
    const raw = (p as any).group;
    const key = typeof raw === 'string' && raw !== '' ? raw : '';
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(p);
  }

  // Named groups first (in first-appearance order), ungrouped last.
  const namedKeys = order.filter(k => k !== '');
  const orderedKeys = buckets.has('') ? [...namedKeys, ''] : namedKeys;

  return orderedKeys.map(key => {
    const bucket = buckets.get(key)!;
    const sorted = sortWithinGroup(bucket);
    return {
      key,
      parts: sorted,
      // Ungrouped parts are unrelated by definition — always list them.
      layout: key === '' ? 'list' : selectLayout(sorted),
    };
  });
}

/**
 * Order parts inside a group: sequenced parts by `seq` ascending, then
 * unsequenced parts in emit order.  Stable so equal keys keep the order
 * the agent emitted them in.
 */
function sortWithinGroup(parts: ArtifactPart[]): ArtifactPart[] {
  const indexed = parts.map((p, i) => ({ p, i }));
  indexed.sort((a, b) => {
    const aHas = hasSeq(a.p);
    const bHas = hasSeq(b.p);
    if (aHas && bHas) {
      const diff = Number((a.p as any).seq) - Number((b.p as any).seq);
      return diff !== 0 ? diff : a.i - b.i;
    }
    if (aHas) return -1;
    if (bHas) return 1;
    return a.i - b.i;
  });
  return indexed.map(e => e.p);
}

/** True if this part is an image the viewer can safely render inline. */
export function isImagePart(part: ArtifactPart): boolean {
  if (!part || part.part_type !== 'file' || !part.file_uri) return false;
  const media = (part.media_type || '').toLowerCase();
  if (media) return INLINE_IMAGE_MEDIA_TYPES.has(media);
  const uri = part.file_uri.toLowerCase();
  return INLINE_IMAGE_EXTENSIONS.some(ext => uri.endsWith(ext));
}

/**
 * Map a stored blob path to the run's artifact-serving route.
 *
 * `file_uri` is an absolute on-disk path (possibly an encrypted blob),
 * so it is never usable directly by the browser.  Only its basename is
 * used — the server resolves it against the run's own artifacts dir and
 * rejects anything that escapes, so no path segment from the stored
 * value can influence the location.
 */
export function blobUrlForPart(
  part: ArtifactPart,
  projectId: string,
  runId: string,
): string | null {
  if (!part?.file_uri || !projectId || !runId) return null;
  const basename = part.file_uri.split('/').pop() || '';
  if (!basename) return null;
  return `/api/v1/projects/${encodeURIComponent(projectId)}`
    + `/task-runs/${encodeURIComponent(runId)}`
    + `/artifacts/${encodeURIComponent(basename)}`;
}

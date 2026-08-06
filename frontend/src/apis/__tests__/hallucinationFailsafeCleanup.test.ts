/**
 * Regression tests for the HALLUCINATION FAILSAFE *cleanup* step
 * (``frontend/src/apis/chatApi.ts``, "HALLUCINATION FAILSAFE" block).
 *
 * Companion to ``hallucinationFailsafeOverlap.test.ts``, which covers the
 * DETECTION half (which chunks get flagged).  This file covers what happens
 * to ``currentContent`` AFTER a chunk is flagged.
 *
 * THE DEFECTS
 * -----------
 * Once a chunk is flagged, the contaminated chunk is dropped and never
 * appended.  That means the only contamination that can possibly be sitting
 * in ``currentContent`` is a PARTIAL marker straddling the chunk boundary
 * -- e.g. the stream committed "...prose <!-- TOOL_BL" and the completing
 * "OCK_START:..." arrived in the chunk being discarded.
 *
 * The original cleanup did ``currentContent.search(matchedPattern)`` and
 * trimmed from the result, which is wrong in both directions:
 *
 *   1. WRONG-DELETION.  For a fabrication contained wholly inside the
 *      dropped chunk, the pattern does not appear in ``currentContent`` at
 *      the offending position at all -- but it very often appears EARLIER,
 *      because the frontend's own tool_display handler emits the same
 *      markers for real tool results.  ``search`` returns the FIRST such
 *      marker in the whole message, so the cleanup deleted every real tool
 *      block and all prose after it, while the actual fabrication was
 *      already being discarded anyway.  Pure collateral damage.
 *
 *   2. NO-OP-ON-SPLIT.  For a marker split across the boundary, the
 *      complete marker exists in neither string on its own, so ``search``
 *      returns -1, the cleanup does nothing, and the dangling
 *      "<!-- TOOL_BL" fragment survives in the message.  That is exactly
 *      the orphan-comment state the code's own comment says it exists to
 *      prevent: marked.js reads an unterminated HTML comment as "everything
 *      after this is comment", so all following tool blocks and prose
 *      render as literal text or vanish.
 *
 *   3. ONCE-PER-MESSAGE CLEANUP.  The whole cleanup was nested inside
 *      ``if (!hallucinationDetected)``, a flag that is set on first
 *      detection and never reset within the request.  A second fabrication
 *      in the same response was therefore still dropped from the stream but
 *      its committed remnant was never cleaned up -- re-entering state (2).
 *
 * THE FIX
 * -------
 * For marker patterns, remove precisely the straddling prefix and nothing
 * else.  For the one non-marker pattern (``SECURITY BLOCK: ... not
 * allowed``) keep the existing paragraph-break trim: that kind of
 * fabrication can be committed across several chunks before its pattern
 * completes, so the paragraph trim is the only thing that removes the
 * earlier committed lines.  And run the cleanup on every occurrence, not
 * just the first.
 *
 * WHY MIRRORED RATHER THAN IMPORTED
 * ---------------------------------
 * The block is inline in a ~700-line SSE handler with no callable seam, so
 * these tests reimplement it.  The ``extractionFidelity`` describe reads the
 * real ``chatApi.ts`` and asserts the mirrored constants and control flow
 * still match, so the mirror cannot silently drift into testing a fiction.
 */

import * as fs from 'fs';
import * as path from 'path';

const CHAT_API_PATH = path.resolve(__dirname, '../chatApi.ts');
const CHAT_API_SOURCE = fs.readFileSync(CHAT_API_PATH, 'utf-8');

/** Mirror of HALLUCINATION_PATTERNS (chatApi.ts). Order is significant. */
const HALLUCINATION_PATTERNS: RegExp[] = [
  /`{3,4}tool:mcp_/,
  /<TOOL_SENTINEL>/,
  /<\/TOOL_SENTINEL>/,
  /<!-- TOOL_BLOCK_START:/,
  /<!-- TOOL_MARKER:/,
  /<!-- TOOL_BLOCK_END:/,
  /<name>mcp_[a-zA-Z]/,
  /<n>mcp_[a-zA-Z]/,
  /<arguments>\s*\{/,
  /SECURITY BLOCK:[\s\S]{0,200}not allowed/,
];

/** Mirror of MARKER_SOURCES (chatApi.ts). */
const MARKER_SOURCES = [
  'TOOL_BLOCK_START', 'TOOL_BLOCK_END', 'TOOL_MARKER',
  'TOOL_SENTINEL', 'tool:mcp_', 'mcp_[a-zA-Z]', 'arguments',
];

/** Mirror of the shell-output cue test in the non-marker branch. */
const SHELL_CUES = /(\$ |ERROR:|SECURITY BLOCK|Allowed commands:|📋)/;

const OVERLAP_CHARS = 60;

/** Mirror of the fenced/inline code strip applied before scanning. */
function stripCode(text: string): string {
  return text.replace(/```[\s\S]*?```/g, '').replace(/`[^`]+`/g, '');
}

interface Flagged {
  pattern: RegExp;
  match: RegExpMatchArray;
  overlapLength: number;
}

/**
 * Mirror of the (already fixed) detection step: scan overlap-then-candidate
 * and accept a match only when it extends into the new chunk.
 */
function detect(currentContent: string, chunk: string): Flagged | null {
  const candidate = stripCode(chunk);
  const overlap = stripCode(currentContent.slice(-OVERLAP_CHARS));
  const scanText = overlap + candidate;
  for (const pattern of HALLUCINATION_PATTERNS) {
    const match = scanText.match(pattern);
    if (match && match.index !== undefined
        && match.index + match[0].length > overlap.length) {
      return { pattern, match, overlapLength: overlap.length };
    }
  }
  return null;
}

/** The ORIGINAL cleanup, preserved so the defects are demonstrable. */
function cleanupOriginal(currentContent: string, flagged: Flagged): string {
  const isMarkerPattern = MARKER_SOURCES.some(
    (m) => flagged.pattern.source.indexOf(m) >= 0,
  );
  if (isMarkerPattern) {
    const markerIdx = currentContent.search(flagged.pattern);
    if (markerIdx >= 0) {
      return currentContent.substring(0, markerIdx).trimEnd();
    }
    return currentContent;
  }
  const lastBreak = currentContent.lastIndexOf('\n\n');
  if (lastBreak > 0 && SHELL_CUES.test(currentContent.substring(lastBreak))) {
    return currentContent.substring(0, lastBreak).trimEnd();
  }
  return currentContent;
}

/** The FIXED cleanup: straddle-only for markers, paragraph trim for shell. */
function cleanupFixed(currentContent: string, flagged: Flagged): string {
  const isMarkerPattern = MARKER_SOURCES.some(
    (m) => flagged.pattern.source.indexOf(m) >= 0,
  );
  if (isMarkerPattern) {
    const { match, overlapLength } = flagged;
    if (match.index !== undefined && match.index < overlapLength) {
      const straddled = match[0].slice(0, overlapLength - match.index);
      // endsWith is load-bearing: the overlap is code-stripped, so its
      // indices can be shifted relative to currentContent.
      if (straddled && currentContent.endsWith(straddled)) {
        return currentContent.substring(
          0, currentContent.length - straddled.length,
        );
      }
    }
    return currentContent;
  }
  const lastBreak = currentContent.lastIndexOf('\n\n');
  if (lastBreak > 0 && SHELL_CUES.test(currentContent.substring(lastBreak))) {
    return currentContent.substring(0, lastBreak).trimEnd();
  }
  return currentContent;
}

function htmlCommentBlock(tool: string, toolId: string, result: string): string {
  return `<!-- TOOL_BLOCK_START:${tool}|H|${toolId} -->\n${result}\n`
    + `<!-- TOOL_BLOCK_END:${tool}|${toolId} -->\n\n`;
}

const countBlocks = (s: string): number =>
  (s.match(/TOOL_BLOCK_START/g) || []).length;

// ── Defect 1: wrong-deletion on an in-chunk fabrication ─────────────

describe('cleanup must not delete legitimate content', () => {
  const committed =
    'Checking the first file.\n\n'
    + htmlCommentBlock('mcp_WorkspaceSearch', 'toolu_t1', '{"hits":3}')
    + 'That found three hits.\n\n'
    + htmlCommentBlock('mcp_WorkspaceSearch', 'toolu_t2', '{"hits":7}')
    + 'Now the caller.\n\n';

  // The model fabricates a complete marker inside a single chunk.  That
  // chunk is dropped, so nothing about currentContent should change.
  const fabrication = 'Here is <!-- TOOL_BLOCK_START:mcp_fake|H|z --> output.';

  it('flags the fabricated chunk', () => {
    expect(detect(committed, fabrication)).not.toBeNull();
  });

  it('ORIGINAL destroys real tool blocks and prose (the defect)', () => {
    const flagged = detect(committed, fabrication)!;
    const after = cleanupOriginal(committed, flagged);
    expect(countBlocks(committed)).toBe(2);
    expect(countBlocks(after)).toBe(0);
    expect(after).toBe('Checking the first file.');
    // Every later sentence is gone too.
    expect(after).not.toContain('That found three hits');
    expect(after).not.toContain('Now the caller');
  });

  it('FIXED leaves currentContent byte-identical', () => {
    const flagged = detect(committed, fabrication)!;
    expect(cleanupFixed(committed, flagged)).toBe(committed);
  });

  it('FIXED preserves the separator, so the next delta cannot weld', () => {
    const flagged = detect(committed, fabrication)!;
    const after = cleanupFixed(committed, flagged);
    expect(after.endsWith('\n\n')).toBe(true);
    expect(after + 'The caller passes null.').toContain('caller.\n\nThe caller');
  });

  it('ORIGINAL removes the separator, producing the welded seam', () => {
    const flagged = detect(committed, fabrication)!;
    const after = cleanupOriginal(committed, flagged);
    expect(/\n$/.test(after)).toBe(false);
    expect(after + 'The caller passes null.')
      .toBe('Checking the first file.The caller passes null.');
  });
});

// ── Defect 2: no-op on a split marker leaves an orphan comment ──────

describe('cleanup must remove a straddling marker fragment', () => {
  // The stream committed a partial marker; the completion arrives in the
  // chunk about to be dropped.
  const committed = 'Let me check the file. <!-- TOOL_BL';
  const chunk = 'OCK_START:mcp_fake|H|x -->\nfabricated\n';

  it('flags the split marker', () => {
    expect(detect(committed, chunk)).not.toBeNull();
  });

  it('ORIGINAL no-ops, leaving the orphan comment opener (the defect)', () => {
    const flagged = detect(committed, chunk)!;
    const after = cleanupOriginal(committed, flagged);
    expect(after).toBe(committed);
    // An unterminated <!-- makes marked.js swallow the rest of the message.
    expect(after).toContain('<!-- TOOL_BL');
  });

  it('FIXED removes exactly the fragment and nothing more', () => {
    const flagged = detect(committed, chunk)!;
    const after = cleanupFixed(committed, flagged);
    expect(after).toBe('Let me check the file. ');
    expect(after).not.toContain('<!--');
  });

  it('FIXED keeps the trailing space so prose does not weld', () => {
    const flagged = detect(committed, chunk)!;
    const after = cleanupFixed(committed, flagged);
    expect(after.endsWith(' ')).toBe(true);
  });

  it('handles a fragment of a TOOL_MARKER comment too', () => {
    const cur = 'Running the command. <!-- TOOL_MAR';
    const flagged = detect(cur, 'KER:abc -->\nfake\n')!;
    expect(cleanupFixed(cur, flagged)).toBe('Running the command. ');
  });

  it('handles a straddling tool fence', () => {
    const cur = 'Let me run it.\n\n``';
    const flagged = detect(cur, '``tool:mcp_fake|H|bash\nfake\n')!;
    const after = cleanupFixed(cur, flagged);
    expect(after).toBe('Let me run it.\n\n');
    expect(after).not.toContain('``');
  });
});

// ── The endsWith guard: never delete the wrong bytes ────────────────

describe('the endsWith guard protects against index drift', () => {
  it('still trims correctly when inline code shifts overlap indices', () => {
    // stripCode removes `retry_count`, so overlap indices no longer line up
    // with currentContent indices.
    const cur = 'Use the `retry_count` field. <!-- TOOL_MAR';
    expect(stripCode(cur.slice(-OVERLAP_CHARS)).length)
      .toBeLessThan(cur.slice(-OVERLAP_CHARS).length);
    const flagged = detect(cur, 'KER:x -->\nfake')!;
    const after = cleanupFixed(cur, flagged);
    expect(after).toBe('Use the `retry_count` field. ');
    expect(after).toContain('retry_count');   // real prose untouched
    expect(after).not.toContain('<!--');
  });

  it('refuses to trim when the computed fragment is not at the end', () => {
    // The pattern appears earlier in committed content, not straddling.
    const cur = 'Prose mentioning <!-- TOOL_MARKER: earlier, then more text.';
    const flagged = detect(cur, '<!-- TOOL_MARKER:y -->');
    if (flagged) {
      expect(cleanupFixed(cur, flagged)).toBe(cur);
    } else {
      // Not flagged at all is equally acceptable — content is preserved.
      expect(flagged).toBeNull();
    }
  });

  it('leaves content alone when the match lies wholly in the new chunk', () => {
    const cur = 'Some prose here.\n\n';
    const flagged = detect(cur, 'Fake <!-- TOOL_MARKER:q --> here')!;
    expect(flagged.match.index).toBeGreaterThanOrEqual(flagged.overlapLength);
    expect(cleanupFixed(cur, flagged)).toBe(cur);
  });
});

// ── Defect 3: cleanup gated on a never-reset flag ───────────────────

describe('cleanup runs on every occurrence, not only the first', () => {
  /** Drive a sequence of chunks; return final content and per-chunk actions. */
  function stream(
    chunks: string[],
    gateCleanupOnFlag: boolean,
  ): { content: string; cleaned: boolean[] } {
    let currentContent = '';
    let hallucinationDetected = false;
    const cleaned: boolean[] = [];
    for (const chunk of chunks) {
      const flagged = detect(currentContent, chunk);
      if (flagged) {
        const shouldClean = gateCleanupOnFlag ? !hallucinationDetected : true;
        if (shouldClean) {
          const before = currentContent;
          currentContent = cleanupFixed(currentContent, flagged);
          cleaned.push(currentContent !== before);
        } else {
          cleaned.push(false);
        }
        hallucinationDetected = true;
        continue;                            // chunk dropped
      }
      currentContent += chunk;
      cleaned.push(false);
    }
    return { content: currentContent, cleaned };
  }

  // Two separate split-marker fabrications in one response.
  const chunks = [
    'First prose. <!-- TOOL_MAR',           // commits a fragment
    'KER:a -->\nfake one\n',                // completes it -> flagged
    'Second prose. <!-- TOOL_BL',           // commits another fragment
    'OCK_START:mcp_f|H|b -->\nfake two\n',  // completes it -> flagged
  ];

  it('GATED cleanup leaves the second orphan fragment behind (the defect)', () => {
    const { content, cleaned } = stream(chunks, true);
    expect(cleaned[1]).toBe(true);    // first cleanup ran
    expect(cleaned[3]).toBe(false);   // second suppressed by the flag
    expect(content).toContain('<!-- TOOL_BL');
  });

  it('UNGATED cleanup removes both fragments', () => {
    const { content, cleaned } = stream(chunks, false);
    expect(cleaned[1]).toBe(true);
    expect(cleaned[3]).toBe(true);
    expect(content).not.toContain('<!--');
    expect(content).toBe('First prose. Second prose. ');
  });

  it('does not re-log on later occurrences (warn stays once per message)', () => {
    // The flag still gates the console.warn; only cleanup is ungated.
    let hallucinationDetected = false;
    let warns = 0;
    let currentContent = '';
    for (const chunk of chunks) {
      const flagged = detect(currentContent, chunk);
      if (flagged) {
        if (!hallucinationDetected) warns += 1;
        currentContent = cleanupFixed(currentContent, flagged);
        hallucinationDetected = true;
        continue;
      }
      currentContent += chunk;
    }
    expect(warns).toBe(1);
  });
});

// ── The shell-cue branch must be preserved, not replaced ────────────

describe('non-marker (shell fabrication) branch is unchanged', () => {
  it('has at least one reachable non-marker pattern', () => {
    const nonMarker = HALLUCINATION_PATTERNS.filter(
      (p) => !MARKER_SOURCES.some((m) => p.source.indexOf(m) >= 0),
    );
    // If this ever hits zero the shell branch is dead and can be removed;
    // while it is non-empty, deleting the branch would drop live behaviour.
    expect(nonMarker.length).toBeGreaterThan(0);
    expect(nonMarker.map((p) => p.source)).toContain(
      'SECURITY BLOCK:[\\s\\S]{0,200}not allowed',
    );
  });

  it('still trims a shell fabrication committed across chunks', () => {
    // The paragraph-break trim is the ONLY thing that removes the earlier
    // committed "$ rm -rf" line; straddle-only removal would leave it.
    const cur = 'Let me run it.\n\n$ rm -rf /tmp/x\nSECURITY BLOCK: that command is';
    const flagged = detect(cur, ' not allowed here.')!;
    expect(MARKER_SOURCES.some((m) => flagged.pattern.source.indexOf(m) >= 0))
      .toBe(false);
    expect(cleanupFixed(cur, flagged)).toBe('Let me run it.');
  });

  it('shell branch behaves identically before and after the fix', () => {
    const cur = 'Let me run it.\n\n$ ls /nope\nSECURITY BLOCK: ls is';
    const flagged = detect(cur, ' not allowed.')!;
    expect(cleanupFixed(cur, flagged)).toBe(cleanupOriginal(cur, flagged));
  });

  it('shell branch no-ops when the committed tail carries no cue', () => {
    const cur = 'Let me run the command.\n\n';
    const flagged = detect(cur, 'SECURITY BLOCK: rm is not allowed.')!;
    expect(cleanupFixed(cur, flagged)).toBe(cur);
  });
});

// ── Fidelity: the mirror must track the real source ─────────────────

describe('extractionFidelity: the mirror matches the real source', () => {
  it('mirrors every MARKER_SOURCES entry', () => {
    const block = CHAT_API_SOURCE.slice(
      CHAT_API_SOURCE.indexOf('const MARKER_SOURCES'),
      CHAT_API_SOURCE.indexOf('];', CHAT_API_SOURCE.indexOf('const MARKER_SOURCES')),
    );
    expect(block.length).toBeGreaterThan(0);
    for (const src of MARKER_SOURCES) {
      expect(block).toContain(src);
    }
  });

  it('still keys the marker/non-marker split on MARKER_SOURCES', () => {
    expect(CHAT_API_SOURCE).toContain('const isMarkerPattern = MARKER_SOURCES.some(');
  });

  it('still retains the shell-cue paragraph-break trim', () => {
    expect(CHAT_API_SOURCE).toContain("lastIndexOf('\\n\\n')");
    expect(CHAT_API_SOURCE).toContain('SECURITY BLOCK|Allowed commands:');
  });

  it('mirrors the same overlap window length', () => {
    expect(CHAT_API_SOURCE).toContain(`currentContent.slice(-${OVERLAP_CHARS})`);
  });

  it('no longer trims markers via search() over the whole message', () => {
    // The wrong-deletion mechanism.  If this reappears, defect 1 is back.
    expect(CHAT_API_SOURCE).not.toContain('currentContent.search(matchedPattern)');
  });

  it('uses an endsWith-guarded straddle removal for markers', () => {
    expect(CHAT_API_SOURCE).toContain('currentContent.endsWith(straddled)');
  });

  it('does not gate the cleanup on hallucinationDetected', () => {
    // The flag must guard only the console.warn.  Asserting on the source
    // is crude but this is the one property with no runtime seam: the
    // cleanup has to sit OUTSIDE the `if (!hallucinationDetected)` body.
    const warnIdx = CHAT_API_SOURCE.indexOf('HALLUCINATION FAILSAFE: Detected fake');
    const straddleIdx = CHAT_API_SOURCE.indexOf('const straddled = scanMatch[0].slice(');
    const flagSetIdx = CHAT_API_SOURCE.indexOf('hallucinationDetected = true;');
    expect(warnIdx).toBeGreaterThan(0);
    expect(straddleIdx).toBeGreaterThan(0);
    expect(flagSetIdx).toBeGreaterThan(0);
    // The flag is set before the cleanup runs, and the cleanup follows it.
    expect(straddleIdx).toBeGreaterThan(flagSetIdx);
  });
});

/**
 * Regression tests for the HALLUCINATION FAILSAFE overlap window
 * (``frontend/src/apis/chatApi.ts``, "HALLUCINATION FAILSAFE" block).
 *
 * THE DEFECT
 * ----------
 * The failsafe scans each incoming text chunk for markers that "should
 * NEVER appear in the content stream" -- tool fences, TOOL_BLOCK_START /
 * TOOL_BLOCK_END / TOOL_MARKER comments, XML tool-call shapes.  Those
 * markers are produced by the frontend's own tool_display handler, so
 * their presence in the *model's* text means the model is fabricating
 * tool output.
 *
 * To catch a marker split across two SSE chunks ("<!-- TOOL_BL" then
 * "OCK_START:"), the scan carries a 60-char overlap window taken from
 * ``currentContent``.  But ``currentContent`` is the accumulated stream,
 * which already holds the frontend's own legitimately-emitted markers.
 * So after any tool call whose result is rendered via HTML comments, the
 * overlap contains a real marker and the failsafe flags OUR OWN output.
 *
 * The consequence is severe and silent:
 *   1. ``currentContent.search(matchedPattern)`` finds the FIRST such
 *      marker in the entire message -- not the recent one -- and
 *      everything from there is discarded.
 *   2. ``.trimEnd()`` removes the trailing newlines that separated the
 *      block from the following prose.
 *   3. ``return`` drops the current text chunk.
 *   4. ``hallucinationDetected`` is never reset, so subsequent matching
 *      chunks keep getting dropped.
 *
 * Because HTML comments render invisibly, the user sees a tool call
 * vanish with no trace and the next sentence welded onto the previous
 * one with no space: "...retry count.3The retry count is 3."
 *
 * WHY ONLY SOME TOOL CALLS
 * ------------------------
 * ``formatMCPOutput`` returns ``hierarchicalResults`` for search-style
 * tools (see mcpFormatter.ts -- the field arrives on the backend result),
 * which chatApi renders with HTML-comment markers.  Every other tool
 * takes the plain path, which emits a ```` fence -- and the fence-strip
 * that runs before the scan removes those markers first.  So the bug is
 * path-dependent, not random.
 *
 * THE FIX
 * -------
 * Scan ``overlap + candidate`` (the correct order for reassembling a
 * split marker -- the stream tail PRECEDES the new chunk) and accept a
 * match only when it extends past the end of the overlap, i.e. into the
 * new chunk.  A complete marker already in ``currentContent`` lies wholly
 * inside the overlap and is ignored.
 *
 * These tests mirror the scan logic rather than importing it, because the
 * block is inline inside a ~700-line streaming handler with no seam to
 * call.  The mirror is kept deliberately literal -- pattern list, strip
 * regexes, and window size copied verbatim -- so it fails if the real
 * constants drift.  ``extractionFidelity`` below asserts that fidelity
 * against the real source file, so the mirror cannot silently diverge
 * into testing a fiction.
 */

import * as fs from 'fs';
import * as path from 'path';

/** Verbatim copy of HALLUCINATION_PATTERNS (chatApi.ts). */
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

const OVERLAP_CHARS = 60;

/** Verbatim copy of the fence/inline-code strip applied before scanning. */
const stripCode = (s: string): string =>
  s.replace(/```[\s\S]*?```/g, '').replace(/`[^`]+`/g, '');

/** Current (defective) scan: candidate + '\n' + overlap, any match wins. */
function scanCurrent(currentContent: string, chunk: string): RegExp | null {
  const overlap = stripCode(currentContent.slice(-OVERLAP_CHARS));
  const scanText = stripCode(chunk) + '\n' + overlap;
  return HALLUCINATION_PATTERNS.find(p => p.test(scanText)) ?? null;
}

/** Fixed scan: overlap + candidate, match must extend into the chunk. */
function scanFixed(currentContent: string, chunk: string): RegExp | null {
  const overlap = stripCode(currentContent.slice(-OVERLAP_CHARS));
  const candidate = stripCode(chunk);
  const scanText = overlap + candidate;
  return HALLUCINATION_PATTERNS.find(p => {
    const m = scanText.match(p);
    return m != null && m.index !== undefined
      && m.index + m[0].length > overlap.length;
  }) ?? null;
}

/** MARKER_SOURCES from chatApi.ts, choosing the trim strategy. */
const MARKER_SOURCES = [
  'TOOL_BLOCK_START', 'TOOL_BLOCK_END', 'TOOL_MARKER',
  'TOOL_SENTINEL', 'tool:mcp_', 'mcp_[a-zA-Z]', 'arguments',
];

/**
 * Reproduce the destructive branch: trim from the first matching marker
 * and drop the chunk.  Returns the surviving accumulated content.
 */
function applyTrim(currentContent: string, matched: RegExp): string {
  const isMarkerPattern = MARKER_SOURCES.some(m => matched.source.indexOf(m) >= 0);
  if (isMarkerPattern) {
    const markerIdx = currentContent.search(matched);
    if (markerIdx >= 0) return currentContent.substring(0, markerIdx).trimEnd();
  }
  return currentContent;
}

/** Strip HTML-comment markers the way MarkdownRenderer does, to get
 *  what the user actually sees. */
const asRendered = (s: string): string =>
  s.replace(/<!-- TOOL_BLOCK_START:[\s\S]*?-->\n?/g, '')
   .replace(/<!-- TOOL_BLOCK_END:[\s\S]*?-->\n?/g, '')
   .replace(/<!-- TOOL_MARKER:[^>]+ -->\n?/g, '');

/** Build the HTML-comment tool block chatApi emits on the
 *  hierarchicalResults path. */
function htmlCommentBlock(tool: string, toolId: string, result: string): string {
  return `<!-- TOOL_BLOCK_START:${tool}|Header|${toolId} -->\n`
    + `${result}\n`
    + `<!-- TOOL_BLOCK_END:${tool}|${toolId} -->\n\n`;
}

/** Build the 4-backtick fence block emitted on the plain path. */
function fenceBlock(tool: string, result: string): string {
  const F = '`'.repeat(4);
  return `${F}tool:${tool}|Header|json\n${result}\n${F}\n\n`;
}

describe('failsafe false-positives on the frontend’s own markers', () => {
  it('flags a legitimate HTML-comment tool block as a hallucination', () => {
    const acc = 'Let me check the retry count.\n\n'
      + htmlCommentBlock('mcp_WorkspaceSearch', 'toolu_01ab', '{"count":3}');
    // The defect: our own marker sits in the overlap window.
    expect(scanCurrent(acc, 'The retry count is 3.')).not.toBeNull();
    // The fix ignores it, because the match never reaches the new chunk.
    expect(scanFixed(acc, 'The retry count is 3.')).toBeNull();
  });

  it('false-positives regardless of result length on the HTML path', () => {
    // The tail after the END marker is short and fixed, so slice(-60)
    // always reaches back into "<!-- TOOL_BLOCK_END:".  Length is NOT
    // the discriminator -- the render path is.
    for (const len of [1, 3, 10, 40, 100, 500]) {
      const acc = 'Prose.\n\n'
        + htmlCommentBlock('mcp_x', 'toolu_01ab', 'y'.repeat(len));
      expect(scanCurrent(acc, 'Next sentence.')).not.toBeNull();
      expect(scanFixed(acc, 'Next sentence.')).toBeNull();
    }
  });

  it('does not false-positive on the fence path (explains “only some tools”)', () => {
    // The fence-strip removes the fence markers before the scan, so the
    // plain path was never affected.  This asymmetry is why the bug
    // appeared to strike only certain tool calls.
    for (const len of [1, 10, 100]) {
      const acc = 'Prose.\n\n' + fenceBlock('mcp_x', 'y'.repeat(len));
      expect(scanCurrent(acc, 'Next sentence.')).toBeNull();
      expect(scanFixed(acc, 'Next sentence.')).toBeNull();
    }
  });

  it('false-positives after several tool calls, not just one', () => {
    let acc = 'First look.\n\n' + htmlCommentBlock('mcp_a', 'toolu_1', '{"a":1}');
    acc += 'Second look.\n\n' + htmlCommentBlock('mcp_b', 'toolu_2', '{"b":2}');
    acc += 'Third look.\n\n' + htmlCommentBlock('mcp_c', 'toolu_3', '{"c":3}');
    expect(scanCurrent(acc, 'Now I understand.')).not.toBeNull();
    expect(scanFixed(acc, 'Now I understand.')).toBeNull();
  });
});

describe('the user-visible damage', () => {
  it('deletes the tool block, leaves no trace, and removes the space', () => {
    const acc = 'Let me check the retry count.\n\n'
      + htmlCommentBlock('mcp_c', 'toolu_1', '3');
    const matched = scanCurrent(acc, 'The retry count is 3.');
    expect(matched).not.toBeNull();

    // Chunk is dropped; the NEXT delta appends with no separator.
    const survived = applyTrim(acc, matched!);
    const final = survived + 'The retry count is 3.';
    const seen = asRendered(final);

    // Exactly the reported symptom: no tool trace, no space.
    expect(seen).not.toMatch(/TOOL_|tool:|Running|````/);
    expect(seen).toBe('Let me check the retry count.\n\n3The retry count is 3.');
    // The weld is between the orphaned result text and the next sentence:
    // "3The".  Asserted on that seam specifically -- the paragraph break
    // before "3" is legitimate and survives, so a \s check there would be
    // testing the wrong boundary.
    expect(seen).toMatch(/3The retry/);
    expect(seen).not.toMatch(/3\s+The retry/);
  });

  it('discards content back to the FIRST marker, losing earlier tool calls', () => {
    // search() is not anchored near the recent marker, so a late false
    // positive can wipe out most of the message.
    let acc = 'First look.\n\n' + htmlCommentBlock('mcp_a', 'toolu_1', '{"a":1}');
    acc += 'Second look.\n\n' + htmlCommentBlock('mcp_b', 'toolu_2', '{"b":2}');
    acc += 'Third look.\n\n' + htmlCommentBlock('mcp_c', 'toolu_3', '{"c":3}');
    const matched = scanCurrent(acc, 'Now I understand.');
    const survived = applyTrim(acc, matched!);

    const before = (acc.match(/TOOL_BLOCK_START/g) ?? []).length;
    const after = (survived.match(/TOOL_BLOCK_START/g) ?? []).length;
    expect(before).toBe(3);
    expect(after).toBeLessThan(before);   // earlier blocks destroyed
    expect(survived.length).toBeLessThan(acc.length / 2);
  });

  it('destroys the trailing newlines that separated block from prose', () => {
    const acc = 'Prose.\n\n' + htmlCommentBlock('mcp_x', 'toolu_1', 'result');
    expect(acc.endsWith('\n\n')).toBe(true);
    const matched = scanCurrent(acc, 'Follow-up sentence.');
    const survived = applyTrim(acc, matched!);
    // trimEnd() means the next delta cannot be separated.
    expect(survived.endsWith('\n')).toBe(false);
  });
});

describe('genuine hallucinations are still caught', () => {
  it('catches a marker split across two chunks (the window’s purpose)', () => {
    // This is the case the overlap exists for.  Note it FAILS under the
    // current candidate-first order: the halves only reassemble when the
    // stream tail precedes the new chunk.
    expect(scanCurrent('some prose <!-- TOOL_BL', 'OCK_START:mcp_x|H|t1 -->')).toBeNull();
    expect(scanFixed('some prose <!-- TOOL_BL', 'OCK_START:mcp_x|H|t1 -->')).not.toBeNull();
  });

  it('catches a fabricated marker wholly inside one chunk', () => {
    const chunk = 'Here is <!-- TOOL_BLOCK_START:mcp_fake|H|x --> output';
    expect(scanFixed('Prose. ', chunk)).not.toBeNull();
  });

  it('catches a fabricated fence opener (the real streaming shape)', () => {
    // A fence arrives opener-first; with no closer yet the strip cannot
    // remove it, so the pattern matches.
    const chunk = '`'.repeat(4) + 'tool:mcp_fake|H|bash\nfake output';
    expect(scanFixed('Prose. ', chunk)).not.toBeNull();
  });

  it('catches fabricated XML tool-call shapes', () => {
    expect(scanFixed('Prose. ', '<name>mcp_fake_tool</name>')).not.toBeNull();
    expect(scanFixed('Prose. ', '<arguments> {"a":1}')).not.toBeNull();
  });

  it('catches a fabricated TOOL_SENTINEL', () => {
    expect(scanFixed('Prose. ', 'and then <TOOL_SENTINEL> fires')).not.toBeNull();
  });

  it('leaves ordinary prose alone', () => {
    const acc = 'Some earlier discussion.\n\n';
    for (const chunk of [
      'A normal sentence.',
      'Discussing the mcp_ prefix in words.',
      'A dash -- and an arrow -->, both harmless.',
    ]) {
      expect(scanFixed(acc, chunk)).toBeNull();
    }
  });

  it('does not regress the complete-fabricated-fence hole', () => {
    // A fully-closed fabricated fence is stripped before scanning, so it
    // is missed -- BEFORE and AFTER the fix alike.  Asserted so the fix
    // is not blamed for a pre-existing gap, and so closing it later is a
    // deliberate, visible change.
    const F = '`'.repeat(4);
    const chunk = `${F}tool:mcp_fake|H|bash\nfake\n${F}`;
    expect(scanCurrent('Prose. ', chunk)).toBeNull();
    expect(scanFixed('Prose. ', chunk)).toBeNull();
  });
});

describe('extractionFidelity: the mirror matches the real source', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '../chatApi.ts'), 'utf-8',
  );

  it('still uses a 60-char overlap window', () => {
    // If the window size changes, the boundary arithmetic above is stale.
    expect(source).toContain('currentContent.slice(-60)');
  });

  it('mirrors every pattern in the real HALLUCINATION_PATTERNS list', () => {
    const block = source.slice(
      source.indexOf('const HALLUCINATION_PATTERNS'),
      source.indexOf('];', source.indexOf('const HALLUCINATION_PATTERNS')),
    );
    expect(block).not.toBe('');
    for (const marker of [
      'tool:mcp_', 'TOOL_SENTINEL', 'TOOL_BLOCK_START',
      'TOOL_MARKER', 'TOOL_BLOCK_END', 'arguments',
    ]) {
      expect(block).toContain(marker);
    }
    // Count the entries so an ADDED pattern forces this mirror to be
    // updated rather than silently under-testing.
    const entries = (block.match(/^\s+\//gm) ?? []).length;
    expect(entries).toBe(HALLUCINATION_PATTERNS.length);
  });

  it('no longer trims from the first match, and still drops the chunk', () => {
    // This assertion previously pinned ``currentContent.search(matchedPattern)``
    // as the then-current cleanup, with a note that it would need revisiting if
    // the behaviour were reworked.  It has been: that search scanned the WHOLE
    // accumulated message, so a fabrication contained entirely within the
    // (already-discarded) chunk resolved to the FIRST *legitimate* marker and
    // deleted every real tool block and all prose after it.  The cleanup is now
    // an endsWith-guarded removal of only the marker prefix straddling the chunk
    // boundary -- see hallucinationFailsafeCleanup.test.ts, which owns the
    // behavioural coverage.
    //
    // What is asserted here is the part these overlap tests actually depend on:
    // the contaminated chunk is still dropped rather than appended, which is why
    // the only contamination reaching currentContent is a straddling fragment.
    expect(source).not.toContain('currentContent.search(matchedPattern)');
    expect(source).toContain('currentContent.endsWith(straddled)');
    // Chunk drop retained: warn/cleanup, then flush and bail out of the handler.
    expect(source).toContain('// Silently drop contaminated chunks');
    expect(source).toContain('return; // Backend will handle retry');
  });

  it('never resets hallucinationDetected after the initial declaration', () => {
    // Documents a second, independent defect: once tripped, every later
    // matching chunk in the message is dropped for good.
    const assignments = source.match(/hallucinationDetected\s*=/g) ?? [];
    expect(assignments).toHaveLength(2);          // declaration + set-true
    expect(source).toContain('hallucinationDetected = true');
    const resets = source.match(/hallucinationDetected\s*=\s*false/g) ?? [];
    expect(resets).toHaveLength(1);              // only the declaration
  });
});

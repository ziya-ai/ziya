/**
 * G-CHAT-RECOVERY / D-022 (chat-message-w4-12): near-miss GFM table recovery.
 *
 * A ragged GFM table whose delimiter (separator) row is one column short of its
 * header — the exact w4-12 shape — is rejected by marked (the renderer's lexer)
 * and rendered as a paragraph; the chat renderer's CommonMark breaks:false
 * lexing then collapses every row into one run-on "pipe soup" line.
 * repairNearMissTableSeparator pads the delimiter to the header's column count.
 *
 * marked recognises a table only when the delimiter row has EXACTLY as many
 * cells as the header (verified out-of-band with marked 16.4.2:
 * `marked.lexer(W412)` -> [paragraph]; after this repair -> a `table` token
 * with header [Name,Qty,Price] and Pear's row padded to ['Pear','5','']). This
 * suite proves the same acceptance condition on the text the lexer sees:
 *
 * DIRECTION: each "fixed" case first asserts the RAW delimiter has FEWER cells
 * than the header (the condition under which marked drops the table), and then
 * that the repair makes the two counts EQUAL — so the test fails against a
 * pipeline lacking the pass. Theme-independent (pure text preprocessor), so the
 * both-theme obligation is discharged at the shared render stage.
 */

import { repairNearMissTableSeparator } from '../../../utils/tableSeparatorRepair';

// Count cells the way marked's splitCells does: strip one optional leading /
// trailing pipe, then split on unescaped pipes.
const cells = (line: string): number => {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.replace(/\|$/, '');
  return s.split(/(?<!\\)\|/).length;
};

// Exact chat-message-w4-12 body (the table portion the model emitted).
const W412 =
  'Quarterly units:\n\n' +
  'Name | Qty | Price\n' +
  '--- | ---\n' +
  'Apple | 3 | $1.20\n' +
  'Pear | 5\n' +
  'Plum | 7 | $0.80\n\n' +
  'Totals follow.\n';

describe('D-022: near-miss GFM table separator is padded to the header width', () => {
  it('w4-12: a 2-column separator under a 3-column header is recovered', () => {
    const rawLines = W412.split('\n');
    // Direction: raw header has 3 cells but the separator only 2 -> marked
    // rejects the table (renders the block as one paragraph = "pipe soup").
    expect(cells(rawLines[2])).toBe(3); // "Name | Qty | Price"
    expect(cells(rawLines[3])).toBe(2); // "--- | ---"
    expect(cells(rawLines[3])).toBeLessThan(cells(rawLines[2]));

    const fixed = repairNearMissTableSeparator(W412);
    const fixedLines = fixed.split('\n');

    // The separator now matches the header width, the condition marked needs.
    expect(fixedLines[3]).toBe('--- | --- | ---');
    expect(cells(fixedLines[3])).toBe(cells(fixedLines[2]));
    // Header text and the short data row are otherwise untouched.
    expect(fixedLines[2]).toBe('Name | Qty | Price');
    expect(fixedLines[5]).toBe('Pear | 5');
  });

  it('pads a separator that is two columns short', () => {
    // The delimiter must carry a pipe to be a table delimiter (a bare `---`
    // under a text line is a setext heading, deliberately left alone below).
    const raw = 'A | B | C | D\n--- | ---\n1 | 2 | 3 | 4\n';
    expect(cells('--- | ---')).toBeLessThan(cells('A | B | C | D'));
    const fixed = repairNearMissTableSeparator(raw);
    expect(fixed.split('\n')[1]).toBe('--- | --- | --- | ---');
    expect(cells(fixed.split('\n')[1])).toBe(4);
  });

  it('preserves a trailing outer pipe when padding', () => {
    const raw = '| A | B | C |\n| --- | --- |\n| 1 | 2 | 3 |\n';
    const fixed = repairNearMissTableSeparator(raw);
    // header strips outer pipes -> 3 cells; delimiter was 2 -> padded to 3,
    // trailing pipe retained.
    expect(fixed.split('\n')[1]).toBe('| --- | --- | --- |');
    expect(cells(fixed.split('\n')[1])).toBe(3);
  });
});

describe('D-022: the pass is inert on content it must not touch', () => {
  it('leaves an already-valid table byte-for-byte unchanged (idempotent)', () => {
    const valid = 'Name | Qty | Price\n--- | --- | ---\nApple | 3 | $1.20\n';
    expect(repairNearMissTableSeparator(valid)).toBe(valid);
    // and a second pass changes nothing further
    const once = repairNearMissTableSeparator(W412);
    expect(repairNearMissTableSeparator(once)).toBe(once);
  });

  it('does not mistake a setext heading underline for a table delimiter', () => {
    // "Heading\n---" is a setext H2; the underline has NO pipe, so it is not a
    // delimiter and must be returned unchanged.
    const setext = 'Some Heading | with a pipe\n---\nbody text\n';
    expect(repairNearMissTableSeparator(setext)).toBe(setext);
  });

  it('ignores a delimiter that already matches or exceeds the header', () => {
    const eq = 'A | B\n--- | ---\n1 | 2\n';
    expect(repairNearMissTableSeparator(eq)).toBe(eq);
    const longer = 'A | B\n--- | --- | ---\n1 | 2\n';
    expect(repairNearMissTableSeparator(longer)).toBe(longer);
  });

  it('leaves plain prose with pipes and dashes alone', () => {
    const prose = 'Use the -x flag | or the -y flag when needed.\nNo table here.\n';
    expect(repairNearMissTableSeparator(prose)).toBe(prose);
  });
});

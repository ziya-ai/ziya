/**
 * The deck must persist the CARD-level scope when it saves a card.
 *
 * Attribution (2026-10-13): adding skills under "Card Permissions" showed
 * "n skills" on the card row, then the skills vanished after switching to
 * another card and back -- even after pressing Save in the permissions
 * dialog and again on the card.  The editor put the new scope on the
 * draft (TaskCardEditor's setScope -> onChange), but every
 * taskCardApi.update the deck issued listed name/description/root/tags
 * and never scope.  TaskCardUpdate is partial, so the server kept the old
 * scope, and loadCard() on return replaced the draft with it.
 * TaskCardLaunchButton's save already sent scope; only the deck dropped it.
 *
 * Source-text guard, matching the neighbouring deck wiring tests: the
 * defect is a missing field in a payload literal, and a mount would need
 * a project, a fetch mock and a selected card to reach the same line.
 */

import * as fs from 'fs';
import * as path from 'path';

const DIR = path.resolve(__dirname, '..');
const library = fs.readFileSync(path.join(DIR, 'TaskCardsLibrary.tsx'), 'utf8');

/** Each `taskCardApi.update(...)` call's argument text, balanced on parens. */
function updateCalls(src: string): string[] {
  const out: string[] = [];
  const marker = 'taskCardApi.update(';
  let i = src.indexOf(marker);
  while (i !== -1) {
    let depth = 0;
    let j = i + marker.length - 1;
    for (; j < src.length; j++) {
      if (src[j] === '(') depth++;
      else if (src[j] === ')' && --depth === 0) break;
    }
    out.push(src.slice(i, j + 1));
    i = src.indexOf(marker, j);
  }
  return out;
}

describe('the deck saves the card-level scope', () => {
  const definitionSaves = updateCalls(library).filter(c => /\broot:/.test(c));

  it('has the definition-saving calls this guard is about', () => {
    // Save, Add to deck, and the save-before-launch in launchToChat.
    // If this drops, the guard below is passing vacuously.
    expect(definitionSaves.length).toBeGreaterThanOrEqual(3);
  });

  it('every update that sends root also sends scope', () => {
    const missing = definitionSaves.filter(c => !/\bscope:\s*draft\.scope\b/.test(c));
    expect(missing).toEqual([]);
  });
});

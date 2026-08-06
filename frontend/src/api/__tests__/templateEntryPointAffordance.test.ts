/**
 * Tests for the DISCOVERABILITY of the template-manager entry point.
 *
 * The template manager shipped fully working and completely invisible, for
 * two reasons that no existing test covered:
 *
 *   1. The entry link was the LAST element in the modal's scrolling body.
 *      antd's Modal scrolls its body but not its footer, and the body holds
 *      one card per project — with 30 projects that is roughly 2400px, so a
 *      link beneath the final card cannot be reached without scrolling past
 *      every project in the list.
 *
 *   2. It was a bare `<a>` with no `href`. Browsers apply link styling
 *      (colour, underline, pointer cursor) only to anchors that HAVE an
 *      href, and this app defines no global `a {}` rule to compensate. The
 *      element therefore rendered as plain small text — indistinguishable
 *      from the captions surrounding it.
 *
 * Neither defect is visible to a logic test: every handler was correct and
 * every unit test passed. These assertions read the component source and
 * check the two properties that determine whether a user can actually find
 * and recognise the control, which is the part that was broken.
 *
 * Source-reading rather than DOM-rendering is deliberate: mounting this
 * modal needs ProjectContext, ThemeContext, ActiveChatContext and antd, and
 * a jsdom mount could not observe "did this scroll off-screen" anyway
 * because jsdom does no layout. The properties below are structural, so
 * reading structure is the honest test.
 */

import fs from 'fs';
import path from 'path';

const MODAL_PATH = path.join(
  __dirname, '..', '..', 'components', 'ProjectManagerModal.tsx',
);

const source = (): string => fs.readFileSync(MODAL_PATH, 'utf8');

/**
 * The main list view's `return (` — everything after it is that view.
 * Anchored on the modal title so it cannot accidentally match one of the
 * three sub-views (settings / merge / template manager) that return first.
 */
function mainListView(src: string): string {
  const i = src.indexOf('title="Manage Projects"');
  expect(i).toBeGreaterThan(-1);
  return src.slice(i);
}

/** The `footer={...}` prop of the main list view, brace-matched. */
function mainListFooter(src: string): string {
  const view = mainListView(src);
  const start = view.indexOf('footer={');
  expect(start).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = start + 'footer='.length; i < view.length; i += 1) {
    if (view[i] === '{') depth += 1;
    else if (view[i] === '}') {
      depth -= 1;
      if (depth === 0) return view.slice(start, i + 1);
    }
  }
  throw new Error('unbalanced footer={...}');
}

const ENTRY_LABEL = 'Manage project templates';

describe('template-manager entry point is reachable', () => {
  it('lives in the modal footer, not the scrolling body', () => {
    // The regression: an entry point at the end of a body containing one
    // card per project is unreachable without scrolling past all of them.
    // antd renders the footer outside the scrollable region, so placing it
    // there makes it visible regardless of project count.
    expect(mainListFooter(source())).toContain(ENTRY_LABEL);
  });

  it('appears exactly once, so the body copy was removed', () => {
    // Moving it without deleting the original would leave two entry points
    // that must be kept in sync — the same duplication hazard as the
    // duplicated sub-view this file's sibling tests already guard against.
    const occurrences = source().split(ENTRY_LABEL).length - 1;
    expect(occurrences).toBe(1);
  });

  it('keeps a Close control in the footer beside it', () => {
    // The footer previously held only Close. Replacing the whole prop is
    // how the entry point got there, so Close must survive that swap or
    // the modal loses its dismiss button.
    const footer = mainListFooter(source());
    expect(footer).toMatch(/onClick=\{onClose\}/);
    expect(footer).toContain('Close');
  });

  it('opens the manager rather than merely being decorative', () => {
    const footer = mainListFooter(source());
    expect(footer).toMatch(/setShowTemplateManager\(true\)/);
  });

  it('still resolves the default template to a name, not a raw id', () => {
    // A slug in the UI ("software_development") is the failure the
    // load-on-visible change exists to prevent; the move must not drop it.
    const footer = mainListFooter(source());
    expect(footer).toMatch(/templates\.find\(/);
    expect(footer).toContain('defaultTemplateId');
  });
});

describe('href-less anchors carry their own affordance', () => {
  /**
   * Every `<a ...>` opening tag in the file, with its attributes.
   *
   * Matches up to the closing `>` of the opening tag only, so the anchor's
   * text content and any following markup are excluded.
   */
  function anchorOpeningTags(src: string): string[] {
    const out: string[] = [];
    const re = /<a\b/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(src)) !== null) {
      // Walk to the first '>' that is not inside a {...} expression, so a
      // style object containing '>' cannot terminate the tag early.
      let depth = 0;
      for (let i = m.index; i < src.length; i += 1) {
        const c = src[i];
        if (c === '{') depth += 1;
        else if (c === '}') depth -= 1;
        else if (c === '>' && depth === 0) {
          out.push(src.slice(m.index, i + 1));
          break;
        }
      }
    }
    return out;
  }

  it('finds the anchors it means to check', () => {
    // Guards the extractor: if the matcher silently found nothing, every
    // assertion below would pass vacuously and re-admit the defect.
    const tags = anchorOpeningTags(source());
    expect(tags.length).toBeGreaterThanOrEqual(3);
  });

  it('gives every href-less anchor a pointer cursor', () => {
    // Without an href the element is not a link as far as the browser is
    // concerned: no pointer cursor, so it does not read as clickable.
    const offenders = anchorOpeningTags(source())
      .filter(tag => !/\bhref=/.test(tag))
      .filter(tag => !/cursor:\s*'pointer'/.test(tag));
    expect(offenders).toEqual([]);
  });

  it('gives every href-less anchor a link colour', () => {
    // Same root cause as the cursor: default link colour is not applied,
    // so the anchor inherits body text colour and reads as a caption.
    const offenders = anchorOpeningTags(source())
      .filter(tag => !/\bhref=/.test(tag))
      .filter(tag => !/color:/.test(tag));
    expect(offenders).toEqual([]);
  });

  it('themes that colour rather than hard-coding one shade', () => {
    // A single hex would be invisible in one of the two themes; the file's
    // other accents are all conditional on isDarkMode.
    const themed = anchorOpeningTags(source())
      .filter(tag => !/\bhref=/.test(tag))
      .filter(tag => /color:/.test(tag));
    expect(themed.length).toBeGreaterThanOrEqual(3);
    for (const tag of themed) {
      expect(tag).toMatch(/isDarkMode/);
    }
  });
});

describe('the create-form picker is affected by the same defect', () => {
  /** The template line + picker block of the create form. */
  function createFormTemplateArea(src: string): string {
    const i = src.indexOf('<span>Template:</span>');
    expect(i).toBeGreaterThan(-1);
    return src.slice(i, i + 3000);
  }

  it('styles the "change" toggle as clickable', () => {
    // This anchor reveals the template picker. Unstyled, the picker is as
    // undiscoverable as the manager was — the same bug, one dialog over.
    const area = createFormTemplateArea(source());
    const changeIdx = area.indexOf("'change'");
    expect(changeIdx).toBeGreaterThan(-1);
    const tagStart = area.lastIndexOf('<a', changeIdx);
    expect(tagStart).toBeGreaterThan(-1);
    const tag = area.slice(tagStart, changeIdx);
    expect(tag).toMatch(/cursor:\s*'pointer'/);
  });

  it('styles the "use autodetection instead" reset as clickable', () => {
    const src = source();
    const label = src.indexOf('use autodetection instead');
    expect(label).toBeGreaterThan(-1);
    const tagStart = src.lastIndexOf('<a', label);
    const tag = src.slice(tagStart, label);
    expect(tag).toMatch(/cursor:\s*'pointer'/);
  });
});

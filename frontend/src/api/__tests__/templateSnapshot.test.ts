/**
 * Tests for the snapshot-a-project-as-template helpers.
 *
 * `slugifyTemplateId` derives a storage key from a human-typed name.  The
 * id is not a display string: it becomes a JSON object key in
 * ~/.ziya/templates.json AND a URL path segment in deleteTemplate, so the
 * dialog derives it rather than asking the user for a second field whose
 * constraints they would have to understand.
 *
 * `isUsableTemplateName` exists to stop a name of pure punctuation POSTing
 * an empty id, which the server rejects with a bare 400 (see
 * template_store.save_user_template: "A template needs both an id and a
 * name").  Catching it in the dialog is the difference between an
 * explanation and an opaque failure.
 */

import {
  isUsableTemplateName,
  slugifyTemplateId,
} from '../projectTemplateApi';

describe('slugifyTemplateId', () => {
  it('lowercases and joins words with underscores', () => {
    expect(slugifyTemplateId('Deno Service')).toBe('deno_service');
  });

  it('leaves an already-valid slug untouched', () => {
    expect(slugifyTemplateId('deno_service')).toBe('deno_service');
  });

  it('collapses a run of punctuation into a single separator', () => {
    expect(slugifyTemplateId('Deno + Fresh (v2)')).toBe('deno_fresh_v2');
  });

  it('collapses runs of whitespace the same way', () => {
    expect(slugifyTemplateId('Deno  Fresh  v2')).toBe('deno_fresh_v2');
  });

  it('converges names that differ only in separators', () => {
    // Load-bearing: without collapsing, "Deno + Fresh (v2)" and
    // "Deno  Fresh  v2" would produce two different ids whose NAMES render
    // identically in the template picker — two entries the user cannot
    // tell apart.
    expect(slugifyTemplateId('Deno + Fresh (v2)'))
      .toBe(slugifyTemplateId('Deno  Fresh  v2'));
  });

  it('strips leading and trailing separators', () => {
    expect(slugifyTemplateId('  ...Notes...  ')).toBe('notes');
  });

  it('keeps digits', () => {
    expect(slugifyTemplateId('Python 3 Service')).toBe('python_3_service');
  });

  it('replaces non-ASCII letters rather than dropping them silently', () => {
    // 'é' is not in [a-z0-9], so it becomes a separator.  Asserted rather
    // than left implicit because the alternative (transliteration) would be
    // a different, larger feature and this documents which we chose.
    expect(slugifyTemplateId('Café Notes')).toBe('caf_notes');
  });

  it('normalises dashes and dots, which are legal in names but not ids', () => {
    expect(slugifyTemplateId('my-cool-template')).toBe('my_cool_template');
    expect(slugifyTemplateId('deno.json thing')).toBe('deno_json_thing');
  });

  it('is idempotent', () => {
    // The dialog displays the derived id live.  If slugging a slug drifted,
    // the id shown at type-time could differ from what a later re-save
    // produced, silently creating a second template.
    const once = slugifyTemplateId('Deno + Fresh (v2)');
    expect(slugifyTemplateId(once)).toBe(once);
  });

  it('yields empty for input with nothing slug-able', () => {
    expect(slugifyTemplateId('!!!')).toBe('');
    expect(slugifyTemplateId('---')).toBe('');
    expect(slugifyTemplateId('')).toBe('');
    expect(slugifyTemplateId('   ')).toBe('');
  });

  it('tolerates null and undefined', () => {
    // The name comes from a controlled input, but this is a public helper
    // and a nullish argument must not throw inside a render path.
    expect(slugifyTemplateId(null as unknown as string)).toBe('');
    expect(slugifyTemplateId(undefined as unknown as string)).toBe('');
  });

  it('produces ids that survive being a URL path segment unchanged', () => {
    // deleteTemplate interpolates the id into a path.  If a slug needed
    // escaping, the encoded and stored forms would diverge and a delete
    // would 404 on a template that exists.
    const names = [
      'Deno Service', 'Deno + Fresh (v2)', 'my-cool-template',
      'deno.json thing', 'Café Notes', 'Python 3 Service',
    ];
    for (const n of names) {
      const slug = slugifyTemplateId(n);
      expect(encodeURIComponent(slug)).toBe(slug);
    }
  });
});

describe('isUsableTemplateName', () => {
  it('accepts a name that yields a non-empty id', () => {
    expect(isUsableTemplateName('Deno')).toBe(true);
  });

  it('accepts a name of only digits', () => {
    // Odd but valid: '7' slugs to '7', which the server accepts.  The guard
    // must not reject more than the server does, or the dialog would block
    // something that would have worked.
    expect(isUsableTemplateName('7')).toBe(true);
  });

  it('rejects punctuation-only and empty names', () => {
    expect(isUsableTemplateName('---')).toBe(false);
    expect(isUsableTemplateName('!!!')).toBe(false);
    expect(isUsableTemplateName('')).toBe(false);
    expect(isUsableTemplateName('   ')).toBe(false);
  });

  it('agrees exactly with whether slugify produced anything', () => {
    // The guard is defined in terms of the slug, so any future change to
    // slugify must keep them consistent — otherwise the dialog could enable
    // Save for a name that POSTs an empty id.
    const samples = [
      'Deno', '7', '---', '', '   ', 'Café', 'a-b', '...x...',
    ];
    for (const s of samples) {
      expect(isUsableTemplateName(s)).toBe(slugifyTemplateId(s).length > 0);
    }
  });
});

/**
 * The dialog's Save button is gated on `isUsableTemplateName`, and the
 * handler re-checks it before POSTing.  Both are asserted against the real
 * component source so the guard cannot be removed from one place while the
 * other still assumes it.
 */
describe('extraction fidelity', () => {
  const fs = require('fs') as typeof import('fs');
  const path = require('path') as typeof import('path');
  const modalSource = () => fs.readFileSync(
    path.resolve(__dirname, '../../components/ProjectManagerModal.tsx'),
    'utf8',
  );

  it('gates the Save Template button on a usable name', () => {
    expect(modalSource()).toMatch(
      /disabled=\{!templateApi\.isUsableTemplateName\(snapshotName\)\}/,
    );
  });

  it('re-checks the name in the handler rather than trusting the button', () => {
    // A disabled button is a UI affordance, not a validation boundary:
    // Enter-to-submit bypasses it (the name Input has onPressEnter).
    const src = modalSource();
    expect(src).toMatch(
      /if \(!templateApi\.isUsableTemplateName\(name\)\)/,
    );
  });

  it('derives the id rather than taking it from a user field', () => {
    expect(modalSource()).toMatch(
      /id: templateApi\.slugifyTemplateId\(name\)/,
    );
  });

  it('clears snapshot state after a successful save', () => {
    // Leaving the previous name in the form is how you get an accidental
    // second template on the next visit.
    const src = modalSource();
    const handler = src.slice(src.indexOf('handleSaveAsTemplate'));
    expect(handler).toMatch(/setSnapshotName\(''\)/);
    expect(handler).toMatch(/setSnapshotMarkers\(\[\]\)/);
  });
});

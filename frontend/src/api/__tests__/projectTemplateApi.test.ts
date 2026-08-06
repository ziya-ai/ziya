/**
 * Tests for the project-template API client and the create dialog's
 * template-precedence logic.
 *
 * Two things are worth testing here and nothing else is:
 *
 *   1. `detectTemplateSafe` must NEVER throw.  The create dialog calls it
 *      on a debounce as the user types a path, so a transient failure or a
 *      half-typed path must degrade to "general / not detected" rather than
 *      surfacing an error.
 *   2. The precedence rule (explicit choice > detection > global default >
 *      general) is implemented in BOTH places — server-side in
 *      resolve_template_id and client-side to render the preview line.  Two
 *      implementations of one rule can drift, so the client's copy is
 *      pinned here against the same cases the Python tests use.
 *
 * The precedence function is inlined rather than imported because it lives
 * as an inline expression in ProjectManagerModal.  If it is ever extracted
 * to a util, this mirror should import it instead — see the fidelity test
 * at the bottom, which asserts the mirror still matches the component.
 */

import * as fs from 'fs';
import * as path from 'path';

// ── Mock the base api client ─────────────────────────────────────────────
jest.mock('../index', () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}));

import { api } from '../index';
import {
  GENERAL_TEMPLATE_ID,
  describeTemplateChoice,
  detectTemplate,
  detectTemplateSafe,
  deleteTemplate,
  listTemplates,
  saveProjectAsTemplate,
  setDefaultTemplate,
} from '../projectTemplateApi';
import type { DetectTemplateResponse } from '../../types/projectTemplate';

const mockApi = api as jest.Mocked<typeof api>;

beforeEach(() => {
  jest.clearAllMocks();
});

// ── 1. detectTemplateSafe never throws ───────────────────────────────────

describe('detectTemplateSafe', () => {
  it('returns the general fallback for an empty path without calling the server', async () => {
    const r = await detectTemplateSafe('');
    expect(r).toEqual({
      templateId: GENERAL_TEMPLATE_ID, marker: null, detected: false,
    });
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it('treats a whitespace-only path as empty', async () => {
    const r = await detectTemplateSafe('   ');
    expect(r.detected).toBe(false);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it('swallows a rejected request', async () => {
    mockApi.get.mockRejectedValueOnce(new Error('API error: 500'));
    const r = await detectTemplateSafe('/some/path');
    expect(r).toEqual({
      templateId: GENERAL_TEMPLATE_ID, marker: null, detected: false,
    });
  });

  it('swallows a network failure', async () => {
    mockApi.get.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(detectTemplateSafe('/p')).resolves.toMatchObject({
      detected: false,
    });
  });

  it('passes a successful detection straight through', async () => {
    const detected: DetectTemplateResponse = {
      templateId: 'software_development',
      marker: 'pyproject.toml',
      detected: true,
    };
    mockApi.get.mockResolvedValueOnce(detected);
    await expect(detectTemplateSafe('/svc')).resolves.toEqual(detected);
  });

  it('trims the path before sending it', async () => {
    mockApi.get.mockResolvedValueOnce({
      templateId: 'general', marker: null, detected: false,
    });
    await detectTemplateSafe('  /padded/path  ');
    expect(mockApi.get).toHaveBeenCalledWith(
      '/projects/templates/detect?path=%2Fpadded%2Fpath',
    );
  });
});

// ── 2. URL construction ──────────────────────────────────────────────────

describe('request URLs', () => {
  it('encodes a path containing spaces', async () => {
    mockApi.get.mockResolvedValueOnce({
      templateId: 'general', marker: null, detected: false,
    });
    await detectTemplate('/my projects/thing');
    expect(mockApi.get).toHaveBeenCalledWith(
      '/projects/templates/detect?path=%2Fmy%20projects%2Fthing',
    );
  });

  it('encodes a path containing a query-hostile character', async () => {
    mockApi.get.mockResolvedValueOnce({
      templateId: 'general', marker: null, detected: false,
    });
    await detectTemplate('/a&b?c=d');
    const url = mockApi.get.mock.calls[0][0] as string;
    // The separator must remain the only bare '&' / '?' in the query.
    expect(url.split('?').length).toBe(2);
    expect(url).not.toContain('&b');
  });

  it('encodes a template id on delete', async () => {
    mockApi.delete.mockResolvedValueOnce(undefined as never);
    await deleteTemplate('my template/v2');
    expect(mockApi.delete).toHaveBeenCalledWith(
      '/projects/templates/my%20template%2Fv2',
    );
  });

  it('lists templates from the combined endpoint', async () => {
    mockApi.get.mockResolvedValueOnce({ templates: [], defaultTemplateId: null });
    await listTemplates();
    expect(mockApi.get).toHaveBeenCalledWith('/projects/templates');
  });

  it('sends an explicit null to clear the default', async () => {
    mockApi.put.mockResolvedValueOnce({ templates: [], defaultTemplateId: null });
    await setDefaultTemplate(null);
    // Explicitly null, not an absent key — the server distinguishes them.
    expect(mockApi.put).toHaveBeenCalledWith(
      '/projects/templates/default', { templateId: null },
    );
  });

  it('posts a snapshot to the project-scoped endpoint', async () => {
    mockApi.post.mockResolvedValueOnce({} as never);
    await saveProjectAsTemplate('proj-1', { id: 'x', name: 'X' });
    expect(mockApi.post).toHaveBeenCalledWith(
      '/projects/proj-1/save-as-template', { id: 'x', name: 'X' },
    );
  });
});

// ── 3. Provenance wording ────────────────────────────────────────────────

describe('describeTemplateChoice', () => {
  it('names the marker file when detection succeeded', () => {
    expect(describeTemplateChoice(
      { templateId: 'software_development', marker: 'pyproject.toml', detected: true },
      null,
    )).toBe('detected from pyproject.toml');
  });

  it('falls back to the default when nothing was detected', () => {
    expect(describeTemplateChoice(
      { templateId: 'general', marker: null, detected: false },
      'software_development',
    )).toBe('your default');
  });

  it('says "no template" when there is neither', () => {
    expect(describeTemplateChoice(null, null)).toBe('no template');
  });

  it('does not claim detection when detected is false but a marker leaked through', () => {
    // Defensive: a marker without detected=true must not be presented as a
    // detection, or the UI would explain a choice it did not make.
    expect(describeTemplateChoice(
      { templateId: 'general', marker: 'pyproject.toml', detected: false },
      null,
    )).toBe('no template');
  });
});

// ── 4. Precedence rule (mirror of the component's inline expression) ─────

/**
 * Mirror of ProjectManagerModal's `effectiveTemplateId`.
 *
 * Kept in sync by the fidelity test below.  Uses ?? deliberately so that a
 * detection of `general` with detected=false does NOT outrank the user's
 * global default — the bug this ordering exists to avoid.
 */
function effectiveTemplateId(
  override: string | null,
  detection: DetectTemplateResponse | null,
  defaultTemplateId: string | null,
): string {
  return override
    ?? (detection?.detected ? detection.templateId : null)
    ?? defaultTemplateId
    ?? GENERAL_TEMPLATE_ID;
}

describe('template precedence', () => {
  const sd: DetectTemplateResponse = {
    templateId: 'software_development', marker: 'go.mod', detected: true,
  };
  const none: DetectTemplateResponse = {
    templateId: 'general', marker: null, detected: false,
  };

  it('explicit choice beats everything', () => {
    expect(effectiveTemplateId('general', sd, 'software_development')).toBe('general');
  });

  it('detection beats the global default', () => {
    expect(effectiveTemplateId(null, sd, 'general')).toBe('software_development');
  });

  it('global default applies when nothing is detected', () => {
    expect(effectiveTemplateId(null, none, 'software_development'))
      .toBe('software_development');
  });

  it('a failed detection does not shadow the global default', () => {
    // The reason for the `detected ? … : null` guard: the server returns
    // templateId "general" alongside detected=false, and taking that value
    // literally would silently discard the user's preference.
    expect(effectiveTemplateId(null, none, 'software_development'))
      .not.toBe('general');
  });

  it('falls back to general with no signals at all', () => {
    expect(effectiveTemplateId(null, null, null)).toBe(GENERAL_TEMPLATE_ID);
  });

  it('a null detection (path not yet typed) uses the default', () => {
    expect(effectiveTemplateId(null, null, 'software_development'))
      .toBe('software_development');
  });

  it('clearing the override returns to detection', () => {
    expect(effectiveTemplateId(null, sd, null)).toBe('software_development');
  });
});

// ── 5. Fidelity: the mirror must match the component ─────────────────────

describe('extraction fidelity', () => {
  const modalSrc = fs.readFileSync(
    path.join(__dirname, '../../components/ProjectManagerModal.tsx'), 'utf8',
  );

  it('the component still derives effectiveTemplateId with the mirrored ordering', () => {
    // Guards against the mirror above drifting into testing a fiction.  If
    // the component's expression is refactored, this fails and forces the
    // mirror to be updated (or replaced by a real import).
    const compact = modalSrc.replace(/\s+/g, ' ');
    expect(compact).toContain('const effectiveTemplateId = templateOverride');
    expect(compact).toContain('?? (detection?.detected ? detection.templateId : null)');
    expect(compact).toContain('?? defaultTemplateId');
    expect(compact).toContain('?? GENERAL_TEMPLATE_ID');
  });

  it('the component omits templateId when there is no override', () => {
    // Sending `templateId: undefined` would still serialize the key out of
    // JSON.stringify, but an explicit `templateId: null` would NOT — and the
    // server treats a present-but-null field as "no template" rather than
    // "detect for me".  The spread guarantees the key is absent.
    const compact = modalSrc.replace(/\s+/g, ' ');
    expect(compact).toContain(
      '...(templateOverride ? { templateId: templateOverride } : {})',
    );
  });

  it('the create form resets its template state after a successful create', () => {
    // Otherwise an override chosen for one project silently applies to the
    // next one created in the same modal session.
    expect(modalSrc).toContain('setTemplateOverride(null)');
    expect(modalSrc).toContain('setDetection(null)');
  });

  it('detection is debounced rather than fired per keystroke', () => {
    expect(modalSrc).toMatch(/setTimeout\([\s\S]{0,200}detectTemplateSafe/);
  });
});

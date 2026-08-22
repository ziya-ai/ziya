/**
 * Tests for skill-activation polarity — `effectiveActiveSkillIds`.
 *
 * THE DEFECT
 * ----------
 * `activeSkillIds` carries TWO OPPOSITE meanings, selected by the skill's
 * `visibility` (see SkillsSection.getLevel / setLevel):
 *
 *   - user_selectable    : membership means the user turned it ON
 *   - model_discoverable : membership means the user turned it OFF
 *                          (these are on-demand by default; the model loads
 *                          them itself via get_skill_details, so the only
 *                          thing the lens can express is suppression)
 *
 * Five consumers in ProjectContext looped the RAW array, so switching a
 * discoverable skill OFF injected the very prompt being suppressed and
 * additionally applied that skill's modelOverrides (temperature / model /
 * thinkingMode), toolIds and files, and billed its tokens. Turning off
 * Packet Diagrams could silently change model settings for the whole
 * conversation.
 *
 * WHY THIS FILE READS ProjectContext.tsx SOURCE
 * ---------------------------------------------
 * The sibling convention (context/__tests__/skillHydration.test.ts) is to
 * re-implement the pure logic inline and assert on the copy. That cannot
 * work here: a mirror of the fix passes against unpatched production code,
 * so it would certify the bug rather than detect it. The genuine logic is
 * therefore imported from the production module, and the WIRING — that the
 * five consumers actually call it — is asserted against the real source.
 *
 * A provider render was the alternative and was rejected: ProjectProvider's
 * boot path needs projectApi/contextApi/skillApi/tokenApi all mocked plus
 * getStartupInfo sequencing, which is far more fixture than signal for a
 * predicate this small.
 *
 * Assertions are anchored on IDENTIFIERS (memo names, the helper name), not
 * on line numbers or file slices, so they survive reformatting and code
 * motion.
 */

import * as fs from 'fs';
import * as path from 'path';

import {
    effectiveActiveSkillIds,
    isSuppressionMarker,
    MODEL_DISCOVERABLE,
    type ActivatableSkill,
} from '../skillActivation';

// ── Fixtures ─────────────────────────────────────────────────────────
// Named after the real skills involved so a failure reads meaningfully.

const TESTS: ActivatableSkill = {
    id: 'builtin-tests-for-everything',
    visibility: 'user_selectable',
};
const DOCS: ActivatableSkill = {
    id: 'builtin-continuous-documentation',
    visibility: 'user_selectable',
};
const PACKET: ActivatableSkill = {
    id: 'builtin-packet-diagrams',
    visibility: MODEL_DISCOVERABLE,
};
const CIRCUITS: ActivatableSkill = {
    id: 'builtin-circuit-diagrams',
    visibility: MODEL_DISCOVERABLE,
};
// A custom skill created through the API has visibility null — the case
// that must NOT be mistaken for a suppression marker.
const CUSTOM: ActivatableSkill = { id: 'custom-abc', visibility: null };

const ALL = [TESTS, DOCS, PACKET, CIRCUITS, CUSTOM];

describe('isSuppressionMarker', () => {
    it('treats a model_discoverable skill as a suppression marker', () => {
        expect(isSuppressionMarker(PACKET)).toBe(true);
    });

    it('does not treat a user_selectable skill as suppression', () => {
        expect(isSuppressionMarker(TESTS)).toBe(false);
    });

    it('does not treat a null-visibility custom skill as suppression', () => {
        // Custom skills are created with visibility unset. Reading unset as
        // "discoverable" would silently disable every custom skill.
        expect(isSuppressionMarker(CUSTOM)).toBe(false);
    });

    it('is false for an unknown/unloaded skill', () => {
        expect(isSuppressionMarker(undefined)).toBe(false);
    });
});

describe('effectiveActiveSkillIds', () => {
    it('keeps user_selectable skills the user switched on', () => {
        expect(effectiveActiveSkillIds([TESTS.id, DOCS.id], ALL))
            .toEqual([TESTS.id, DOCS.id]);
    });

    it('drops a model_discoverable id, which means OFF not ON', () => {
        // The core inversion. Pre-fix this id reached activeSkillPrompts.
        expect(effectiveActiveSkillIds([PACKET.id], ALL)).toEqual([]);
    });

    it('separates the two meanings in one mixed array', () => {
        // The realistic lens: two skills on, one discoverable suppressed.
        expect(effectiveActiveSkillIds([TESTS.id, PACKET.id, DOCS.id], ALL))
            .toEqual([TESTS.id, DOCS.id]);
    });

    it('drops every suppression marker, not just the first', () => {
        expect(effectiveActiveSkillIds([PACKET.id, CIRCUITS.id], ALL))
            .toEqual([]);
    });

    it('keeps a custom skill whose visibility is unset', () => {
        expect(effectiveActiveSkillIds([CUSTOM.id], ALL)).toEqual([CUSTOM.id]);
    });

    it('excludes an id with no loaded skill record', () => {
        // Lens ids outlive skill records (a deleted skill leaves a stale id
        // in localStorage — exactly what adopting the duplicate "Tests for
        // everything" produced). Visibility is unknowable, and injecting a
        // prompt on a guess is the worse failure.
        expect(effectiveActiveSkillIds(['builtin-long-gone'], ALL)).toEqual([]);
    });

    it('preserves lens order for the ids it keeps', () => {
        expect(effectiveActiveSkillIds([DOCS.id, PACKET.id, TESTS.id], ALL))
            .toEqual([DOCS.id, TESTS.id]);
    });

    it('returns empty for an empty lens', () => {
        expect(effectiveActiveSkillIds([], ALL)).toEqual([]);
    });

    it('returns empty when skills have not loaded yet', () => {
        // Lens restore sets activeSkillIds from localStorage before the
        // skills fetch resolves.
        expect(effectiveActiveSkillIds([TESTS.id], [])).toEqual([]);
    });

    it('does not mutate its inputs', () => {
        const lens = [TESTS.id, PACKET.id];
        effectiveActiveSkillIds(lens, ALL);
        expect(lens).toEqual([TESTS.id, PACKET.id]);
    });
});

// ── Wiring / seam ────────────────────────────────────────────────────
// A correct helper that nothing calls fixes nothing. These assert the
// connection at each hop, which is the half that was actually broken.

describe('ProjectContext wiring', () => {
    const SOURCE = fs.readFileSync(
        path.resolve(__dirname, '../../context/ProjectContext.tsx'),
        'utf-8',
    );

    // Every consumer that iterates the lens to derive something the MODEL
    // receives. Each was a separate instance of the same inversion.
    const ITERATING_CONSUMERS = [
        'activeFiles',
        'activeSkillPrompts',
        'activeModelOverrides',
        'activeToolIds',
    ];

    it('imports the shared helper', () => {
        expect(SOURCE).toContain('effectiveActiveSkillIds');
        expect(SOURCE).toMatch(/from\s+['"]\.\.\/utils\/skillActivation['"]/);
    });

    it('never iterates the raw activeSkillIds array', () => {
        // The literal defect shape. Anchored on the loop, so the hydration
        // effect's own `activeSkillIds.filter(...)` — which SHOULD stay raw,
        // since hydrating a suppressed skill's body is harmless — is not
        // caught by this.
        expect(SOURCE).not.toContain('for (const skillId of activeSkillIds)');
    });

    it(`iterates the filtered ids once per consumer (${ITERATING_CONSUMERS.length})`, () => {
        const hits = SOURCE.split(
            'for (const skillId of effectiveActiveSkillIds)',
        ).length - 1;
        expect(hits).toBe(ITERATING_CONSUMERS.length);
    });

    it.each(ITERATING_CONSUMERS)(
        '%s declares the filtered ids as a dependency',
        (memo) => {
            // Without the dep, the memo keeps a stale value when the user
            // toggles — the fix would appear to work only after an
            // unrelated re-render.
            const decl = SOURCE.indexOf(`const ${memo} = useMemo(`);
            expect(decl).toBeGreaterThan(-1);
            const body = SOURCE.slice(decl, SOURCE.indexOf('\n  }, [', decl) + 200);
            expect(body).toContain('effectiveActiveSkillIds');
        },
    );

    it('gates and bills token calculation on the filtered ids', () => {
        // app/api/tokens.py sums the tokenCount of every id it is handed, so
        // a suppressed skill inflated the number shown to the user.
        //
        // SCOPED to the token effect rather than asserted file-wide.  The
        // hydration effect legitimately keeps the RAW array, so a whole-file
        // "does not contain" assertion fails against CORRECT code — which is
        // what it did: it flagged the one call site that is meant to stay raw.
        const anchor = SOURCE.indexOf('tokenApi.calculateTokens');
        expect(anchor).toBeGreaterThan(-1);
        const region = SOURCE.slice(Math.max(0, anchor - 1400), anchor + 600);
        // Guard: an all-suppressed lens must clear tokenInfo, not bill it.
        expect(region).toContain('effectiveActiveSkillIds.length === 0');
        expect(region).not.toContain('activeSkillIds.length === 0');
        // Payload: the ids actually sent to the token API.
        expect(region).toContain('effectiveActiveSkillIds,');
    });

    it('leaves the hydration effect on the raw lens ids', () => {
        // Deliberate, and asserted POSITIVELY so a later "consistency"
        // cleanup cannot quietly change it without a decision: hydrating a
        // suppressed skill's body is harmless (it is never injected), and
        // filtering here would mean a skill toggled from off -> on-demand
        // -> active had an unhydrated body at the moment of activation.
        const decl = SOURCE.indexOf('const needHydration');
        expect(decl).toBeGreaterThan(-1);
        const guard = SOURCE.slice(Math.max(0, decl - 300), decl);
        expect(guard).toContain('activeSkillIds.length === 0');
    });
});

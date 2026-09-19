/**
 * summarizeServiceDescription — one-line headline from a registry description.
 *
 * Fixtures under tests/fixtures/mcp_descriptions/ are real payloads: the
 * Slack MCP registry entry is a 20 KB README whose first line is a markdown
 * heading and whose second is a byline of links; before this helper existed
 * that whole README was shown as the service's *name*.
 */
import * as fs from 'fs';
import * as path from 'path';
import { summarizeServiceDescription } from '../mcpDescriptionSummary';

const FIXTURES = path.resolve(__dirname, '../../../../tests/fixtures/mcp_descriptions');
const fixture = (name: string) => fs.readFileSync(path.join(FIXTURES, name), 'utf8');

describe('summarizeServiceDescription', () => {
    it('reduces the Slack MCP README to its first prose sentence, capped', () => {
        const raw = fixture('slack_mcp_readme.md');
        expect(raw.length).toBeGreaterThan(15000);          // the fixture is the real blob
        const s = summarizeServiceDescription(raw);
        expect(s.startsWith('A Slack MCP server for Amazon Enterprise Slack')).toBe(true);
        expect(s).not.toMatch(/^#/);                         // heading skipped
        expect(s).not.toContain('Source Code');              // byline skipped
        expect(s).not.toContain('](');                       // no link syntax
        expect(s).not.toContain('\n');
        expect(s.length).toBeLessThanOrEqual(160);
        expect(s.endsWith('…')).toBe(true);
    });

    it('keeps only the lead sentence of a description that continues into a tool list', () => {
        const s = summarizeServiceDescription(fixture('quip_mcp_toollist.md'));
        expect(s).toBe('Comprehensive MCP server for Amazon Quip providing 37 tools covering the full Automation API surface.');
    });

    it('recovers the prose from awesome-list badge residue', () => {
        const s = summarizeServiceDescription(fixture('awesome_list_badge_residue.md'));
        expect(s).toBe('Token-budgeted web fetch for AI agents.');
    });

    it('does not mistake prose containing pipes for a byline', () => {
        const raw = 'Read-only, self-scoped ABR review session lookup over the Midway-gated endpoint. '
            + 'Tools: get_my_sessions (next | upcoming | recent | past-all), get_my_profile.';
        expect(summarizeServiceDescription(raw)).toBe(
            'Read-only, self-scoped ABR review session lookup over the Midway-gated endpoint.');
    });

    it('extends a very short lead sentence with the next one', () => {
        expect(summarizeServiceDescription('Cradle Edit. Edits Cradle jobs and profiles from your assistant. More text here.'))
            .toBe('Cradle Edit. Edits Cradle jobs and profiles from your assistant.');
    });

    it('falls back to the heading, then the first line, when there is no prose', () => {
        expect(summarizeServiceDescription('# Only Heading')).toBe('Only Heading');
        expect(summarizeServiceDescription('- first item\n- second item')).toBe('first item');
    });

    it('passes short plain descriptions through unchanged', () => {
        expect(summarizeServiceDescription('Shell command execution server')).toBe('Shell command execution server');
        expect(summarizeServiceDescription('MCP server for Pippin (https://pippin.sara.amazon.dev/) tools'))
            .toBe('MCP server for Pippin (https://pippin.sara.amazon.dev/) tools');
    });

    it('returns an empty string for empty or non-string input', () => {
        expect(summarizeServiceDescription('')).toBe('');
        expect(summarizeServiceDescription('   ')).toBe('');
        expect(summarizeServiceDescription(null)).toBe('');
        expect(summarizeServiceDescription(undefined)).toBe('');
    });

    it('caps an unpunctuated run-on at maxLen on a word boundary', () => {
        const raw = 'word '.repeat(80).trim();
        const s = summarizeServiceDescription(raw, 50);
        expect(s.length).toBeLessThanOrEqual(50);
        expect(s.endsWith('…')).toBe(true);
        expect(s).not.toMatch(/wor…$/);                      // not cut mid-word
    });
});

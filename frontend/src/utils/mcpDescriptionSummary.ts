/**
 * Reduce a registry service description to a one-line headline.
 *
 * Registry providers frequently hand back an entire README as the
 * `serviceDescription` — markdown headings, a byline of links, install
 * snippets, tables of every tool. Wherever the UI needs a *name* or a
 * one-line summary (the MCP status panel header, service cards, the tool
 * search results), that blob is unusable. This extracts the first real prose
 * sentence instead.
 *
 * Mirror of `app/mcp/description_summary.py`; keep the two in sync (both are
 * covered by fixtures built from real registry payloads under
 * tests/fixtures/mcp_descriptions/).
 */

export const DEFAULT_MAX_LEN = 160;
const MIN_PROSE_LEN = 20;
const MIN_SENTENCE_LEN = 40;
const BYLINE_SEGMENT_MAX = 60;

const FENCE_RE = /```[\s\S]*?(```|$)/g;
const HTML_RE = /<[^>]+>/g;
const IMAGE_RE = /!\[[^\]]*\]\([^)]*\)/g;
const LINK_RE = /\[([^\]]*)\]\([^)]*\)/g;
// "](url)" left behind when an upstream parser truncated a nested badge link.
const ORPHAN_LINK_TAIL_RE = /\]\([^)]*\)/g;
// Leading run of emoji / symbols / punctuation before the first word.
const LEADING_SYMBOLS_RE = /^[^\p{L}\p{N}_("']+/u;
const INLINE_MARKUP_RE = /(\*\*|__|`|~~)/g;
const EMPHASIS_RE = /(?<![A-Za-z0-9])[*_](?=\S)|(?<=\S)[*_](?![A-Za-z0-9])/g;
const WS_RE = /\s+/g;
const HEADING_RE = /^#{1,6}\s*/;
const HR_RE = /^\s*([-*_])\s*(\1\s*){2,}$/;
const LIST_RE = /^\s*([-*+]|\d+[.)])\s+/;
const SENTENCE_SPLIT_RE = /(?<=[.!?])\s+(?=[A-Z0-9"'(])/;

function cleanInline(text: string): string {
    let t = text.replace(INLINE_MARKUP_RE, '').replace(EMPHASIS_RE, '');
    t = t.trim().replace(LEADING_SYMBOLS_RE, '');
    return t.replace(WS_RE, ' ').trim();
}

/** True for lines that carry layout, not prose. */
function isStructural(line: string): boolean {
    const s = line.trim();
    if (!s) return true;
    if (s.startsWith('#')) return true;
    if (HR_RE.test(s)) return true;
    if (LIST_RE.test(s)) return true;
    if (s.startsWith('>') || s.startsWith('|')) return true;
    // A byline / nav row: "Source Code | #channel | Owner (alias@) | ...".
    // Prose that merely mentions "a | b | c" has long segments around the pipes.
    const segs = s.split('|');
    if (segs.length - 1 >= 2 && Math.max(...segs.map(x => x.trim().length)) < BYLINE_SEGMENT_MAX) {
        return true;
    }
    return false;
}

/**
 * Return a single-line headline for a possibly-markdown description.
 *
 * Picks the first paragraph that reads as prose (not a heading, byline, rule,
 * list, quote, table or code block), reduces it to its leading sentence(s),
 * and caps the length on a word boundary. Falls back to the first heading,
 * then to the first non-empty line, so something is always returned for
 * non-empty input.
 */
export function summarizeServiceDescription(
    raw: string | null | undefined,
    maxLen: number = DEFAULT_MAX_LEN,
): string {
    if (!raw || typeof raw !== 'string') return '';
    let text = raw.replace(/\r\n/g, '\n');
    text = text.replace(FENCE_RE, ' ').replace(IMAGE_RE, ' ').replace(HTML_RE, ' ').replace(LINK_RE, '$1');
    if (text.includes('](')) {
        // Badge residue such as "name MCP server](https://…svg)](https://…) 🐍 -
        // real description": drop the tails, then everything up to the " - "
        // separator the awesome-list format puts before the prose.
        text = text.replace(ORPHAN_LINK_TAIL_RE, ' ');
        const idx = text.indexOf(' - ');
        if (idx >= 0) {
            const tail = text.slice(idx + 3);
            if (tail.trim().length >= MIN_PROSE_LEN) text = tail;
        }
    }

    let chosen = '';
    let firstHeading = '';
    let firstLine = '';
    for (const para of text.split(/\n\s*\n/)) {
        const lines = para.split('\n').filter(ln => ln.trim());
        if (lines.length === 0) continue;
        for (const ln of lines) {
            const s = ln.trim();
            if (!firstHeading && s.startsWith('#')) {
                firstHeading = cleanInline(s.replace(HEADING_RE, ''));
            }
            if (!firstLine && !HR_RE.test(s)) {
                firstLine = cleanInline(s.replace(HEADING_RE, '').replace(LIST_RE, ''));
            }
        }
        const prose = lines.filter(ln => !isStructural(ln));
        if (prose.length === 0) continue;
        const candidate = cleanInline(prose.join(' '));
        if (candidate.length >= MIN_PROSE_LEN) {
            chosen = candidate;
            break;
        }
    }

    if (!chosen) chosen = firstHeading || firstLine;
    if (!chosen) return '';

    // Leading sentence(s): keep adding until the headline is substantive.
    let headline = '';
    for (const sent of chosen.split(SENTENCE_SPLIT_RE)) {
        const next = headline ? `${headline} ${sent}`.trim() : sent;
        if (headline && next.length > maxLen) break;
        headline = next;
        if (headline.length >= MIN_SENTENCE_LEN) break;
    }

    if (headline.length > maxLen) {
        let cut = headline.slice(0, maxLen - 1);
        const space = cut.lastIndexOf(' ');
        if (space >= Math.floor(maxLen / 2)) cut = cut.slice(0, space);
        headline = cut.replace(/[\s,;:\-—]+$/, '') + '…';
    }
    return headline;
}

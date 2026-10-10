/**
 * Is a tool result a shell-policy refusal, or merely text that mentions one?
 *
 * The shell server reports a refusal as a message that BEGINS with
 * `🚫 SECURITY BLOCK` / `🚫 WRITE BLOCKED`, either as the whole result or
 * as the `message` of an `{'error': True, 'message': "…"}` dict.  The
 * renderer used to test for the sentinel anywhere in the content, so any
 * result that quoted it — a file_write echoing a test fixture, a grep over
 * this source, a shadow transcript of someone else's blocked command — was
 * replaced wholesale by a "Command Blocked" alert.  Anchoring at the start
 * of the message is what distinguishes the refusal from a mention of it.
 */

const SENTINEL_RE = /^\s*🚫 (?:SECURITY BLOCK|WRITE BLOCKED)\b/;
// The message field of a Python-repr or JSON error dict: its first 60 chars
// are enough to see whether it starts with the sentinel.
const ERROR_MESSAGE_RE = /^\s*\{[^{}]{0,80}?['"]message['"]\s*:\s*['"]((?:[^'"\\]|\\.){0,60})/;

export function looksLikeBlockedCommand(content: string | null | undefined): boolean {
    if (!content) return false;
    if (SENTINEL_RE.test(content)) return true;
    const t = content.trimStart();
    if (!t.startsWith('{') || !/['"]error['"]\s*:\s*(?:True|true)\b/.test(t.slice(0, 120))) return false;
    const m = ERROR_MESSAGE_RE.exec(t);
    return !!m && SENTINEL_RE.test(m[1]);
}

/** The refusal text to show, with the dict wrapper and sentinel prefix removed. */
export function blockedCommandMessage(content: string): string {
    let msg = content;
    const dict = content.match(/['"]message['"]\s*:\s*"((?:[^"\\]|\\.)*)"/);
    if (dict) msg = dict[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
    return msg.replace(/^\s*🚫 (?:SECURITY BLOCK|WRITE BLOCKED):?\s*/, '');
}

/** MCP transport errors keep the generic block's rendering too. */
export const looksLikeMcpError = (content: string): boolean =>
    /^\s*MCP (?:Tool|Resource) Error/.test(content);

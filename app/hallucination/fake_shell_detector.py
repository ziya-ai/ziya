"""
Structural detector for fabricated shell sessions in assistant output.

The target pattern: the model writes a Markdown code fence whose language
tag is a shell variant (bash, sh, shell, zsh, console, terminal) or whose
first non-blank line starts with a $ prompt, and the fence body contains
what looks like real command output -- either grep -n numbered lines or
multiple output lines after a $ prompt.

Key insight: real tool output arrives as a complete block via the tool_result
channel.  Fabricated output arrives token-by-token via the text delta stream.
The delivery mechanism is itself a discriminator -- we do not need to wait for
a completed fence.  As soon as enough evidence accumulates in an open fence
(3+ grep-numbered lines, or prompt + 2 output lines) we fire immediately.

This is separate from the shingle-index parroting check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Shell-typed fence opening: ```bash, ```sh, ```shell, ```zsh,
# ```console, ```terminal (case-insensitive, optional whitespace).
_SHELL_FENCE_OPEN_RE = re.compile(
    r'^`{3,}[ \t]*(bash|sh|shell|zsh|console|terminal)\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Generic fence opening: 3+ backticks at start of line.  The captured
# backtick run length is used to build a matching-or-longer close regex
# per CommonMark rules -- a 4-backtick open requires 4+ to close.
_FENCE_OPEN_RE = re.compile(r'^(`{3,})', re.MULTILINE)

# grep -n / grep -rn output: one or more digits, colon, space, content.
# Three or more consecutive such lines is a strong signal.
_GREP_LINE_RE = re.compile(r'^\d+:[ \t].+', re.MULTILINE)

# Shell prompt line: optional leading whitespace then $ or # followed by
# a space and at least one non-whitespace character (an actual command).
_PROMPT_LINE_RE = re.compile(r'^[ \t]*[$#] \S', re.MULTILINE)

# Strict shell prompt: only $ is considered a prompt marker.  Used when
# deciding whether an untagged fence looks like a shell session based on
# its first line.  '#' is excluded here because comments in config files
# (ini, yaml, python) would otherwise false-positive as root prompts.
_STRICT_DOLLAR_PROMPT_RE = re.compile(r'^[ \t]*\$ \S', re.MULTILINE)

# Shell-grammar markers that have no valid interpretation in non-shell
# languages: file-descriptor redirects and /dev/null redirects.  These
# appear on bare-shell command lines the model fabricates without a
# $ prompt, which Signal 2 cannot see.  Scoped to untagged or shell-
# tagged fences at the call site so that python/js fences with
# subprocess.run("... 2>/dev/null") do not false-positive.
_SHELL_GRAMMAR_RE = re.compile(
    r'(?:\b2>&1|\b2>/dev/null|>>?[ \t]*/dev/null)',
)


@dataclass(frozen=True)
class FakeShellMatch:
    """Describes a detected fabricated shell session."""
    reason: str     # human-readable explanation for log / corrective msg
    signal: str     # 'grep_output' | 'prompt_with_output'
    fence_body: str # the body of the offending fence (for diagnostics)


def _extract_fence_bodies(text: str) -> list[str]:
    """
    Return the bodies of all completed fenced code blocks in *text*.

    'Completed' means both the opening ``` and closing ``` are present.
    Only the body text (between the fences, exclusive) is returned.
    """
    bodies: list[str] = []
    pos = 0
    while pos < len(text):
        open_m = _FENCE_OPEN_RE.search(text, pos)
        if open_m is None:
            break
        # Body starts after the newline that ends the opening fence line.
        body_start = text.find('\n', open_m.start())
        if body_start == -1:
            break
        body_start += 1  # skip the newline itself
        # Find the matching close fence -- must have >= as many backticks.
        open_ticks = open_m.group(1)
        close_re = re.compile(
            rf'^`{{{len(open_ticks)},}}\s*$',
            re.MULTILINE,
        )
        close_m = close_re.search(text, body_start)
        if close_m is None:
            break  # unclosed — skip; stream may still be arriving
        bodies.append(text[body_start:close_m.start()])
        pos = close_m.end()
    return bodies


def _is_shell_fence(opening_line: str) -> bool:
    """True if the fence opening line declares a shell language."""
    return bool(_SHELL_FENCE_OPEN_RE.match(opening_line.rstrip()))


def detect_fake_shell_session(text: str) -> FakeShellMatch | None:
    """
    Scan assistant *text* for fabricated shell sessions.

    Returns a FakeShellMatch on first detection, or None.  Fires on both
    completed and in-progress (unclosed) fences -- real tool output arrives
    as a complete block, so token-by-token accumulation of output-looking
    content inside a fence is itself the fabrication signal.
    """
    pos = 0
    while pos < len(text):
        open_m = _FENCE_OPEN_RE.search(text, pos)
        if open_m is None:
            break

        # Extract the opening line to check language tag.
        open_line_end = text.find('\n', open_m.start())
        if open_line_end == -1:
            break
        opening_line = text[open_m.start():open_line_end]

        body_start = open_line_end + 1
        # Close fence must have at least as many backticks as the open.
        open_ticks = open_m.group(1)
        close_re = re.compile(
            rf'^`{{{len(open_ticks)},}}\s*$',
            re.MULTILINE,
        )
        close_m = close_re.search(text, body_start)
        if close_m is None:
            # Fence is still open (streaming).  Scan whatever has arrived.
            # If evidence threshold is met we fire now rather than waiting --
            # token-by-token delivery of output lines IS the fabrication signal.
            body = text[body_start:]
            pos = len(text)  # nothing left to scan after this
        else:
            body = text[body_start:close_m.start()]
            pos = close_m.end()

        # Signal 1: grep-n output — 3+ consecutive numbered lines.
        grep_matches = _GREP_LINE_RE.findall(body)
        if len(grep_matches) >= 3:
            return FakeShellMatch(
                reason=(
                    f'Code fence contains {len(grep_matches)} grep-style '
                    f'numbered output lines but no shell tool was called'
                ),
                signal='grep_output',
                fence_body=body[:300],
            )

        # Signal 2: shell session = prompt + non-command output lines.
        # Activates when either:
        #   (a) the fence has an explicit shell language tag, or
        #   (b) the fence has no tag but its first non-blank line starts
        #       with '$ ' (strict -- '# ' is excluded because it matches
        #       legitimate config / code comments).
        non_blank_lines = [l for l in body.splitlines() if l.strip()]
        is_shell_tagged = _is_shell_fence(opening_line)
        untagged_dollar_first = (
            not is_shell_tagged
            and non_blank_lines
            and bool(_STRICT_DOLLAR_PROMPT_RE.match(non_blank_lines[0]))
        )
        if is_shell_tagged or untagged_dollar_first:
            prompt_lines = _PROMPT_LINE_RE.findall(body)
            output_lines = [
                l for l in body.splitlines()
                if l.strip() and not _PROMPT_LINE_RE.match(l)
            ]
            # At least one prompt line and two output lines together
            # constitute a fake session.
            if len(prompt_lines) >= 1 and len(output_lines) >= 2:
                return FakeShellMatch(
                    reason=(
                        f'Shell code fence contains {len(prompt_lines)} '
                        f'prompt line(s) and {len(output_lines)} output '
                        f'line(s) but no shell tool was called'
                    ),
                    signal='prompt_with_output',
                    fence_body=body[:300],
                )

        # Signal 3: shell-grammar markers in fence body.  Catches bare
        # shell sessions the model fabricates without any $ prompt, e.g.
        #   cmd1 2>/dev/null; echo 'header'
        #   header
        #   <fabricated output>
        # Scoped to untagged fences and shell-tagged fences so that
        # python/js fences containing subprocess calls with redirect
        # strings do not false-positive.
        is_untagged = opening_line.rstrip() == open_ticks
        if (is_untagged or is_shell_tagged) and _SHELL_GRAMMAR_RE.search(body):
            non_grammar_lines = [
                l for l in body.splitlines()
                if l.strip() and not _SHELL_GRAMMAR_RE.search(l)
            ]
            if len(non_grammar_lines) >= 2:
                return FakeShellMatch(
                    reason=(
                        'Fence contains shell-grammar markers '
                        '(file-descriptor redirect or /dev/null) plus '
                        f'{len(non_grammar_lines)} non-command line(s); '
                        'no shell tool was called'
                    ),
                    signal='shell_grammar',
                    fence_body=body[:300],
                )

    return None

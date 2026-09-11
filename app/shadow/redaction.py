"""Credential redaction for shadow journals (design doc §7 / §10).

Input-side masking (echo-off, prompt regex) protects what the human
*types*.  It cannot see what the wrapped program *prints*, and on real
hosts that is where credentials actually flow: ``env``, ``aws sts
get-session-token``, ``curl -v`` with an ``Authorization`` header, a
``cat`` of a key file, a ``mysql -pSECRET`` command line echoed back.

This module is the single choke point every cmd/output record passes
through before it reaches disk (``JournalWriter``), and every argv passes
through before it reaches the registry.  Deliberately conservative: each
pattern targets a recognisable credential *shape*, and generic key/value
matching requires the value to look like a secret (>= 8 chars, contains a
letter) so ``tokens: 1266457`` and ``passed: 3`` survive.

Replacement is ``[REDACTED:<kind>]`` so a reader knows a value was there.
This is defense in depth, not a guarantee: an arbitrary password printed
without any surrounding hint is indistinguishable from prose.
"""
import re
from typing import List, Optional, Tuple

REDACTED = "[REDACTED:{kind}]"

# Identifiers that name a secret when used as ``KEY=value`` / ``key: value``.
# Stems only — no bare "pass"/"auth"/"key", which hit test summaries and prose.
_KV_STEMS = (r"passw(?:or)?d|passphrase|pwd|secret|token|api[_-]?key|"
             r"access[_-]?key|private[_-]?key|credential")
_KV_RE = re.compile(
    rf"(?i)(?<![\w.-])([\w.-]*(?:{_KV_STEMS})[\w.-]*)(\s*[=:]\s*)"
    rf"(?!\[REDACTED)(?=\S*[A-Za-z])(\S{{8,}})")

# Order matters: specific shapes first so the generic KV rule never sees
# them half-consumed.
_SHAPES: List[Tuple[str, re.Pattern]] = [
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----.*?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----",
        re.S)),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api-token", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
]
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9\-._~+/]{8,}=*)")
_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
_PEM_END_RE = re.compile(r"-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
# ``mysql -pSECRET`` style: attached value that is not purely numeric (so
# ``ssh -p2222`` survives).  Command lines only.
_CLI_P_RE = re.compile(r"(?<!\S)-p(?![0-9]+(?!\S))(\S+)")


class Redactor:
    """Stateful redactor: remembers an unterminated PEM block across chunks."""

    def __init__(self) -> None:
        self._in_pem = False

    def redact(self, text: str, *, command: bool = False) -> str:
        if not text:
            return text
        text = self._pem_state(text)
        for kind, rx in _SHAPES:
            text = rx.sub(REDACTED.format(kind=kind), text)
        text = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED.format(kind="bearer"), text)
        text = _KV_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED.format(kind="value"), text)
        if command:
            text = _CLI_P_RE.sub("-p" + REDACTED.format(kind="cli-password"), text)
        return text

    def _pem_state(self, text: str) -> str:
        """Handle a private-key block split across output chunks."""
        if self._in_pem:
            m = _PEM_END_RE.search(text)
            if m is None:
                return REDACTED.format(kind="private-key")
            self._in_pem = False
            text = REDACTED.format(kind="private-key") + text[m.end():]
        m = _PEM_BEGIN_RE.search(text)
        if m is not None and _PEM_END_RE.search(text, m.end()) is None:
            self._in_pem = True
            return text[:m.start()] + REDACTED.format(kind="private-key")
        return text


def redact_text(text: str, *, command: bool = False) -> str:
    """Stateless convenience for one-off strings (labels, argv tokens)."""
    return Redactor().redact(text, command=command)


def redact_argv(argv: List[str]) -> List[str]:
    """Redact credential-bearing tokens for display/registry use.

    The child still receives the real argv; only what is *recorded* changes.
    """
    return [redact_text(tok, command=True) for tok in argv]


def safe_terminal_text(text: Optional[str], limit: int = 2000) -> str:
    """Make chat-originated text safe to write into the human's terminal.

    Removes every escape sequence and C0/C1 control byte (so a comment can
    never issue a terminal query whose *answer* the terminal would type into
    the wrapped shell), bidi overrides and zero-width characters, and folds
    newlines so one comment renders as one ⏺ line.
    """
    if not text:
        return ""
    s = str(text)[:limit]
    s = re.sub(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])", "", s)
    s = re.sub(r"[\r\n]+", " | ", s)
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]", "", s)
    s = re.sub(r"[\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]", "", s)
    return s

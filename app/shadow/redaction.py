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
import json
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

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


# --- user-supplied patterns (per session / shareable) --------------------------
#
# The built-in shapes are generic.  A given environment has its own leaks —
# a lab ssh banner that prints a temporary credential on every login, an
# internal token format — that only its user can name.  Those live as
# ``~/.ziya/shadow/policies/redact/<set>.json``::
#
#     {"patterns": [{"kind": "lab-cred", "regex": "l2Cwv4W[A-Za-z0-9]+"}]}
#
# and are applied with ``ziya shadow --redact <set>`` (repeatable), or
# inline with ``--redact-regex RX``.  A user regex runs on every byte the
# child prints, inside the human's terminal wrapper, so it is bounded the
# same way a model-supplied search pattern is (see journal.compile_search_
# pattern): length-capped and no quantified groups.

MAX_USER_PATTERNS = 64
_PATTERN_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


def redact_sets_dir() -> Path:
    from app.shadow.policy import policies_dir
    d = policies_dir() / "redact"
    d.mkdir(exist_ok=True)
    return d


def compile_user_pattern(kind: str, regex: str) -> Tuple[str, re.Pattern]:
    """Validate and compile one user redaction pattern.  Raises ValueError."""
    from app.shadow.journal import compile_search_pattern
    if not isinstance(kind, str) or not _PATTERN_NAME_RE.fullmatch(kind or ""):
        raise ValueError(f"redact pattern kind must be [A-Za-z0-9_.-]+, got {kind!r}")
    if not isinstance(regex, str) or not regex:
        raise ValueError(f"redact pattern {kind!r}: regex is required")
    rx = compile_search_pattern(regex)
    if rx is None:
        raise ValueError(f"redact pattern {kind!r}: regex is invalid, too long, or has a "
                         f"quantified group (catastrophic-backtracking shape)")
    if rx.search(""):
        raise ValueError(f"redact pattern {kind!r}: regex matches the empty string")
    return kind, rx


def load_redact_set(name: str) -> List[Tuple[str, re.Pattern]]:
    """Load ``~/.ziya/shadow/policies/redact/<name>.json``.

    Raises ``FileNotFoundError`` / ``ValueError``; the CLI surfaces a clear
    error rather than starting a session that silently lacks the set.
    """
    if not _PATTERN_NAME_RE.fullmatch(name or ""):
        raise ValueError(f"invalid redact set name: {name!r}")
    path = redact_sets_dir() / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pats = data.get("patterns") if isinstance(data, dict) else None
    if not isinstance(pats, list):
        raise ValueError(f"redact set {name!r}: 'patterns' must be a list")
    if len(pats) > MAX_USER_PATTERNS:
        raise ValueError(f"redact set {name!r}: more than {MAX_USER_PATTERNS} patterns")
    out = []
    for i, item in enumerate(pats):
        if not isinstance(item, dict):
            raise ValueError(f"redact set {name!r}: pattern #{i} must be an object")
        out.append(compile_user_pattern(str(item.get("kind") or f"{name}-{i}"),
                                        item.get("regex")))
    return out


def build_user_patterns(sets: Sequence[str] = (),
                        regexes: Sequence[str] = ()) -> List[Tuple[str, re.Pattern]]:
    """Resolve ``--redact`` set names and ``--redact-regex`` values."""
    out: List[Tuple[str, re.Pattern]] = []
    for name in sets:
        out.extend(load_redact_set(name))
    for i, rx in enumerate(regexes):
        out.append(compile_user_pattern(f"custom-{i + 1}", rx))
    if len(out) > MAX_USER_PATTERNS:
        raise ValueError(f"more than {MAX_USER_PATTERNS} redact patterns in total")
    return out


class Redactor:
    """Stateful redactor: remembers an unterminated PEM block across chunks.

    ``extra`` are user patterns (see ``build_user_patterns``), applied
    after the built-in shapes and before the generic key/value rule.
    """

    def __init__(self, extra: Optional[Iterable[Tuple[str, re.Pattern]]] = None) -> None:
        self._in_pem = False
        self._extra: List[Tuple[str, re.Pattern]] = list(extra or [])

    def redact(self, text: str, *, command: bool = False) -> str:
        if not text:
            return text
        text = self._pem_state(text)
        for kind, rx in _SHAPES:
            text = rx.sub(REDACTED.format(kind=kind), text)
        for kind, rx in self._extra:
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

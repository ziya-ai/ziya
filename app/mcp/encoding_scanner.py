"""
Encoding-aware payload scanning (ASR NF-003).

The SDO-183 sanitizer (``response_validator.sanitize_text``) strips hidden
Unicode but is blind to *encoded* instruction payloads: a Base64/hex/ROT13
blob embedded in a repo file, wiki page, or tool result survives sanitization
intact, can be stored as a "memory", and later decoded by the model when it
is injected into the system prompt (LPCI).

This module is a DETECTION control, not a gate: it flags spans that decode to
printable, instruction-like text and returns them for the caller to log as a
security event.  Content is never mutated or blocked here — per NF-003
remediation item 3, detected encodings are logged "even if content is
ultimately allowed".

False-positive control is the whole game: Base64 appears in legitimate data,
hex in git SHAs and hashes.  The detector therefore gates on
decode-to-instruction — a span must BOTH decode to mostly-printable text AND
contain injection/imperative signal — so a commit SHA or a PNG blob does not
trip it.  The cost is that a payload decoding to instructions in a language
the signal regex doesn't cover slips through; acceptable for a detection-only
control.
"""

import base64
import binascii
import codecs
import re
from typing import List, Tuple

# Instruction-like signal in DECODED text: canonical prompt-injection
# phrasings plus bare imperatives aimed at the agent's tools.
_INSTRUCTION_SIGNAL = re.compile(
    r"(?:"
    r"ignore\s+(?:all\s+)?previous|ignore\s+prior|disregard\s+(?:all\s+)?previous|"
    r"you\s+are\s+now|new\s+instructions?|system\s+prompt|"
    r"maintenance\s+mode|developer\s+mode|jailbreak|"
    r"\b(?:run|execute|exec|eval|delete|remove|curl|wget|bash|sh|rm\s+-rf|"
    r"git\s+push|git\s+reset|chmod|sudo)\b"
    r")",
    re.IGNORECASE,
)

# A Base64 run long enough to carry a meaningful instruction (24 chars ≈ 18
# decoded bytes).  Optional trailing padding.
_B64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
# A hex run of at least 16 bytes (32 hex chars).
_HEX_RUN = re.compile(r"(?:[0-9a-fA-F]{2}){16,}")

# Cap work per call so a pathological input can't cause quadratic blowup.
_MAX_SPANS_PER_KIND = 40


def _printable_ratio(s: str) -> float:
    if not s:
        return 0.0
    printable = sum(1 for c in s if c.isprintable() or c in "\n\t ")
    return printable / len(s)


def _decoded_is_suspicious(decoded: str) -> bool:
    """True when decoded text is mostly printable AND instruction-like."""
    return _printable_ratio(decoded) >= 0.90 and bool(_INSTRUCTION_SIGNAL.search(decoded))


def scan_for_encoded_payloads(text: str) -> List[Tuple[str, str]]:
    """Scan *text* for encoded instruction payloads.

    Returns a list of ``(encoding, decoded_snippet)`` tuples for each
    suspicious span; an empty list means nothing suspicious.  Never mutates
    or blocks — detection only.
    """
    if not text or len(text) < 24:
        return []
    hits: List[Tuple[str, str]] = []

    # --- Base64 ---
    for i, m in enumerate(_B64_RUN.finditer(text)):
        if i >= _MAX_SPANS_PER_KIND:
            break
        span = m.group(0)
        pad = span + "=" * (-len(span) % 4)
        try:
            decoded = base64.b64decode(pad, validate=True).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            continue
        if _decoded_is_suspicious(decoded):
            hits.append(("base64", decoded[:120]))

    # --- Hex ---
    for i, m in enumerate(_HEX_RUN.finditer(text)):
        if i >= _MAX_SPANS_PER_KIND:
            break
        span = m.group(0)
        if len(span) % 2:
            span = span[:-1]
        try:
            decoded = bytes.fromhex(span).decode("utf-8", "replace")
        except ValueError:
            continue
        if _decoded_is_suspicious(decoded):
            hits.append(("hex", decoded[:120]))

    # --- ROT13 --- flag only when rotation REVEALS instruction signal that
    # was not present in the original (plain instruction text is NF-002's
    # job, not this scanner's).
    try:
        rot = codecs.encode(text, "rot_13")
        if _INSTRUCTION_SIGNAL.search(rot) and not _INSTRUCTION_SIGNAL.search(text):
            hits.append(("rot13", rot[:120]))
    except Exception:
        pass

    return hits


def scan_and_log(text: str, source: str) -> List[Tuple[str, str]]:
    """Scan *text* and emit a security-audit event per encoding detected.

    Convenience wrapper for write-path callers.  Returns the hits (empty if
    clean).  Logging failures never propagate.
    """
    hits = scan_for_encoded_payloads(text)
    if hits:
        try:
            from app.utils.tool_audit_log import log_security_event
            log_security_event(
                "encoded_payload_detected",
                source_tool=source,
                details={
                    "encodings": ",".join(sorted({h[0] for h in hits})),
                    "count": len(hits),
                    "sample": hits[0][1] if hits else "",
                },
            )
        except Exception:
            pass  # Detection audit must never break the write path
    return hits


__all__ = ["scan_for_encoded_payloads", "scan_and_log"]

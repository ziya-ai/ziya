"""
MCP Tool Input and Response Validation.

Validates MCP tool responses for:
- Response schema conformance (structure, MIME types, content types)
- Size and length limits
- Hidden character stripping (Unicode smuggling, zero-width chars, control chars)
- Semantic relevance checks (configurable per-tool business logic)

Validates MCP tool inputs for:
- Schema-level constraints (enum, minLength, maxLength, minimum, maximum, pattern)
- Dangerous pattern detection in string inputs

This module centralizes validation logic so both local and remote MCP server
responses pass through the same security checks.
"""

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

# --- Size / length limits ---------------------------------------------------

MAX_TEXT_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB per text block
MAX_CONTENT_BLOCKS = 50
MAX_IMAGE_DATA_BYTES = 20 * 1024 * 1024  # 20 MB per image block (base64)

# Allowed MIME types for content blocks.
ALLOWED_TEXT_MIME_TYPES = {"text/plain", "text/markdown", "text/html", "application/json"}
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
ALLOWED_MIME_TYPES = ALLOWED_TEXT_MIME_TYPES | ALLOWED_IMAGE_MIME_TYPES

# --- Hidden / dangerous character sets --------------------------------------

# SDO-183: GenAI Hidden Character Smuggling
# Strip Unicode tag block U+E0000–U+E007F and other invisible characters.
# Zero-width and invisible Unicode characters used in smuggling attacks.
_HIDDEN_CHARS_RE = re.compile(
    "["
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u2060"  # word joiner
    "\u2061"  # function application
    "\u2062"  # invisible times
    "\u2063"  # invisible separator
    "\u2064"  # invisible plus
    "\ufeff"  # BOM (when not at position 0)
    "\ufff9"  # interlinear annotation anchor
    "\ufffa"  # interlinear annotation separator
    "\ufffb"  # interlinear annotation terminator
    "\U000e0000-\U000e007f"  # language tag range (full block per AGI-BST guideline)
    "]"
)

# SDO-183: Orphaned surrogates (U+D800–U+DFFF) must also be stripped.
# Models can reconstruct valid text from orphaned surrogate pairs, so
# even isolated surrogates are dangerous.  Python 3 str objects normally
# cannot hold surrogates, but data decoded with errors='surrogatepass'
# or received from external sources via bytes can.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")

# Control characters (except \t \n \r which are normal whitespace).
# ESC (\x1b) is intentionally preserved: in CLI mode the terminal renders
# ANSI escape sequences natively; in server/web mode the frontend converts
# them to styled HTML spans.  The range is therefore split around \x1b:
#   \x0e-\x1a (before ESC) + \x1c-\x1f (after ESC).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")

# Bidi override characters for visual spoofing.
_BIDI_OVERRIDE_RE = re.compile(
    "["
    "\u202a\u202b\u202c\u202d\u202e"  # LRE, RLE, PDF, LRO, RLO
    "\u2066\u2067\u2068\u2069"          # LRI, RLI, FSI, PDI
    "]"
)

# Patterns in response text that suggest injection attempts.
_INJECTION_PATTERNS_IN_RESPONSE: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"<\s*script\b",
        r"javascript\s*:",
        r"on(?:load|error|click|mouseover)\s*=",
        r"data\s*:\s*text/html",
    ]
]

# Dangerous patterns in tool input strings.
_DANGEROUS_INPUT_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\.\./\.\.",                                          # directory traversal
        r";\s*(?:rm|curl|wget|bash|sh|python|nc|ncat)\b",     # command chaining
        r"\$\{.*?\}",                                          # template injection
        r"`[^`]*`",                                            # backtick command sub
    ]
]


# =============================================================================
# Response validation
# =============================================================================


class ResponseValidationError(Exception):
    """Raised when an MCP response fails validation."""
    def __init__(self, message: str, error_code: int = -32602):
        super().__init__(message)
        self.error_code = error_code


def validate_response(
    result: Optional[Dict[str, Any]],
    tool_name: str = "unknown",
) -> Dict[str, Any]:
    """Validate and sanitize an MCP tool response.

    Checks: structural schema, block count, size limits, MIME types,
    hidden-char stripping, injection-pattern scanning.

    Returns the sanitized response dict (may be modified in place).
    Raises ResponseValidationError if structurally unsalvageable.
    """
    if result is None:
        raise ResponseValidationError(
            f"Tool '{tool_name}' returned None — expected a dict response"
        )

    if not isinstance(result, dict):
        raise ResponseValidationError(
            f"Tool '{tool_name}' returned {type(result).__name__} — expected dict"
        )

    # Error responses: sanitize messages but pass through.
    if result.get("error"):
        _sanitize_error_message(result)
        return result

    # --- Structural check ---
    content = result.get("content")
    if content is None:
        logger.debug(f"Response from '{tool_name}' lacks 'content' key — wrapping")
        result = {"content": [{"type": "text", "text": json.dumps(result)}]}
        content = result["content"]

    if not isinstance(content, list):
        raise ResponseValidationError(
            f"Tool '{tool_name}': 'content' must be a list, got {type(content).__name__}"
        )

    # --- Block-count limit ---
    if len(content) > MAX_CONTENT_BLOCKS:
        logger.warning(
            f"Tool '{tool_name}' returned {len(content)} content blocks "
            f"(limit {MAX_CONTENT_BLOCKS}) — truncating"
        )
        result["content"] = content[:MAX_CONTENT_BLOCKS]
        content = result["content"]

    # --- Per-block validation ---
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            raise ResponseValidationError(
                f"Tool '{tool_name}': content block {i} is not a dict"
            )

        block_type = block.get("type")
        if block_type not in ("text", "image", "resource"):
            raise ResponseValidationError(
                f"Tool '{tool_name}': content block {i} has unrecognized "
                f"type '{block_type}' — expected text|image|resource"
            )

        if block_type == "text":
            _validate_text_block(block, i, tool_name)
        elif block_type == "image":
            _validate_image_block(block, i, tool_name)
        elif block_type == "resource" and "text" in block:
            block["text"] = sanitize_text(block["text"])

    return result


def sanitize_text(text: str, preserve_ansi: bool = False) -> str:  # noqa: ARG001
    """Strip hidden characters, control chars, bidi overrides, and surrogates.

    Per SDO-183 (GenAI Hidden Character Smuggling), stripping is applied
    iteratively until the output stabilises, so nested/layered smuggling
    attempts that reveal new hidden characters after one pass are caught.
    """
    if not isinstance(text, str):
        return str(text)

    # Iterative stripping per SDO-183 reference implementation (do-while loop).
    # ESC (\x1b) is always preserved so ANSI color codes survive.  In CLI mode
    # the terminal renders them; in web mode the frontend converts them to
    # styled HTML <span> tags via ansiToHtml().

    previous = None
    cleaned = text
    while cleaned != previous:
        previous = cleaned
        cleaned = _HIDDEN_CHARS_RE.sub("", cleaned)
        cleaned = _SURROGATE_RE.sub("", cleaned)
        cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
        cleaned = _BIDI_OVERRIDE_RE.sub("", cleaned)

    if cleaned != text:
        removed_count = len(text) - len(cleaned)
        logger.warning(
            f"Stripped {removed_count} hidden/control/bidi characters from response text"
        )

    return cleaned


def scan_text_for_injection(text: str, tool_name: str = "unknown") -> List[str]:
    """Scan response text for injection patterns. Returns warning strings."""
    warnings: List[str] = []
    for pattern in _INJECTION_PATTERNS_IN_RESPONSE:
        match = pattern.search(text)
        if match:
            warnings.append(
                f"Tool '{tool_name}' response contains suspicious pattern "
                f"'{pattern.pattern}' near: ...{match.group()[:60]}..."
            )
    return warnings


# =============================================================================
# Input validation (schema-level constraints beyond type coercion)
# =============================================================================


def validate_input_constraints(
    arguments: Dict[str, Any],
    schema: Dict[str, Any],
    tool_name: str = "unknown",
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate tool inputs against JSON Schema constraints.

    Checks: enum, minLength/maxLength, minimum/maximum/exclusive*, pattern,
    and dangerous-pattern scanning on string values.

    Returns (validated_arguments, list_of_warnings).
    Raises ResponseValidationError on hard constraint violations.
    """
    warnings: List[str] = []
    properties = schema.get("properties", {})

    for key, value in arguments.items():
        if key not in properties:
            continue
        field_schema = properties[key]

        # --- enum ---
        allowed = field_schema.get("enum")
        if allowed is not None and value not in allowed:
            raise ResponseValidationError(
                f"Tool '{tool_name}': parameter '{key}' value "
                f"'{_truncate(value)}' is not in allowed values: {allowed}",
            )

        # --- string constraints ---
        if isinstance(value, str):
            min_len = field_schema.get("minLength")
            max_len = field_schema.get("maxLength")
            if min_len is not None and len(value) < min_len:
                raise ResponseValidationError(
                    f"Tool '{tool_name}': parameter '{key}' length {len(value)} "
                    f"is below minimum {min_len}",
                )
            if max_len is not None and len(value) > max_len:
                raise ResponseValidationError(
                    f"Tool '{tool_name}': parameter '{key}' length {len(value)} "
                    f"exceeds maximum {max_len}",
                )

            pattern = field_schema.get("pattern")
            if pattern:
                try:
                    if not re.match(pattern, value):
                        raise ResponseValidationError(
                            f"Tool '{tool_name}': parameter '{key}' value "
                            f"'{_truncate(value)}' does not match pattern '{pattern}'",
                        )
                except re.error:
                    logger.warning(
                        f"Invalid regex pattern in schema for '{key}': {pattern}"
                    )

            # Dangerous pattern scan
            for dp in _DANGEROUS_INPUT_PATTERNS:
                match = dp.search(value)
                if match:
                    warnings.append(
                        f"Tool '{tool_name}': parameter '{key}' contains "
                        f"suspicious pattern near: ...{match.group()[:60]}..."
                    )

        # --- numeric constraints ---
        if isinstance(value, (int, float)):
            minimum = field_schema.get("minimum")
            maximum = field_schema.get("maximum")
            exclusive_min = field_schema.get("exclusiveMinimum")
            exclusive_max = field_schema.get("exclusiveMaximum")

            if minimum is not None and value < minimum:
                raise ResponseValidationError(
                    f"Tool '{tool_name}': parameter '{key}' value {value} "
                    f"is below minimum {minimum}",
                )
            if maximum is not None and value > maximum:
                raise ResponseValidationError(
                    f"Tool '{tool_name}': parameter '{key}' value {value} "
                    f"exceeds maximum {maximum}",
                )
            if exclusive_min is not None and value <= exclusive_min:
                raise ResponseValidationError(
                    f"Tool '{tool_name}': parameter '{key}' value {value} "
                    f"must be greater than {exclusive_min}",
                )
            if exclusive_max is not None and value >= exclusive_max:
                raise ResponseValidationError(
                    f"Tool '{tool_name}': parameter '{key}' value {value} "
                    f"must be less than {exclusive_max}",
                )

    return arguments, warnings


# =============================================================================
# Semantic / business-logic validators (register per tool)
# =============================================================================

_semantic_validators: Dict[str, Any] = {}


def register_semantic_validator(tool_name: str, validator_fn) -> None:
    """Register a semantic validation function for a specific tool.

    The validator receives (tool_name: str, result: dict) and returns a
    list of message strings.  Messages starting with "ERROR:" mark the
    response as invalid.
    """
    _semantic_validators[tool_name] = validator_fn
    logger.debug(f"Registered semantic validator for tool '{tool_name}'")


def run_semantic_validators(
    tool_name: str,
    result: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Run registered semantic validators for a tool response.

    Returns (is_valid, messages).  is_valid is False if any message
    starts with "ERROR:".
    """
    validator = _semantic_validators.get(tool_name)
    if validator is None:
        return True, []

    try:
        messages = validator(tool_name, result)
        if not isinstance(messages, list):
            messages = [str(messages)]
    except Exception as exc:
        logger.error(f"Semantic validator for '{tool_name}' raised: {exc}")
        messages = [f"ERROR: Semantic validator raised an exception: {exc}"]

    has_error = any(m.startswith("ERROR:") for m in messages)
    return not has_error, messages


# =============================================================================
# Internal helpers
# =============================================================================


def _validate_text_block(
    block: Dict[str, Any], index: int, tool_name: str
) -> None:
    """Validate and sanitize a text content block."""
    text = block.get("text")
    if text is None:
        raise ResponseValidationError(
            f"Tool '{tool_name}': text block {index} is missing 'text' field"
        )
    if not isinstance(text, str):
        block["text"] = str(text)
        text = block["text"]

    byte_size = len(text.encode("utf-8", errors="replace"))
    if byte_size > MAX_TEXT_CONTENT_BYTES:
        logger.warning(
            f"Tool '{tool_name}': text block {index} is {byte_size} bytes "
            f"(limit {MAX_TEXT_CONTENT_BYTES}) — truncating"
        )
        block["text"] = text[:MAX_TEXT_CONTENT_BYTES] + "\n\n[content truncated]"
        text = block["text"]

    mime = block.get("mimeType")
    if mime and mime not in ALLOWED_TEXT_MIME_TYPES:
        logger.warning(
            f"Tool '{tool_name}': text block {index} has disallowed "
            f"MIME type '{mime}' — overriding to text/plain"
        )
        block["mimeType"] = "text/plain"

    block["text"] = sanitize_text(text)

    injection_warnings = scan_text_for_injection(block["text"], tool_name)
    for w in injection_warnings:
        logger.warning(w)


def _validate_image_block(
    block: Dict[str, Any], index: int, tool_name: str
) -> None:
    """Validate an image content block."""
    data = block.get("data")
    if data is None:
        raise ResponseValidationError(
            f"Tool '{tool_name}': image block {index} is missing 'data' field"
        )

    if isinstance(data, str) and len(data) > MAX_IMAGE_DATA_BYTES:
        raise ResponseValidationError(
            f"Tool '{tool_name}': image block {index} data is "
            f"{len(data)} bytes (limit {MAX_IMAGE_DATA_BYTES})"
        )

    mime = block.get("mimeType", "image/png")
    if mime not in ALLOWED_IMAGE_MIME_TYPES:
        raise ResponseValidationError(
            f"Tool '{tool_name}': image block {index} has disallowed "
            f"MIME type '{mime}'"
        )


def _sanitize_error_message(result: Dict[str, Any]) -> None:
    """Strip hidden characters from error messages."""
    msg = result.get("message")
    if isinstance(msg, str):
        result["message"] = sanitize_text(msg)
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and "text" in block:
                block["text"] = sanitize_text(block["text"])


def _truncate(value: Any, max_len: int = 80) -> str:
    """Truncate a value for display in error messages."""
    s = str(value)
    return s if len(s) <= max_len else s[:max_len] + "…"

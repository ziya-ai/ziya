# lets move sanitizers here to clean up code flow and make them reusable

from typing import Any

from app.utils.logging_utils import logger


# Below this length a span cannot reach the detector's evidence floor, so
# scoring it is wasted work on the hot send path.
_MIN_SCANNABLE = 750


def sanitize_message_content(content: Any, describe: str = "message") -> Any:
    """Clean provider-hostile artifacts out of one message's content.

    Two independent problems, both originating from rich-text/PDF pastes:

    1. Private Use Area glyphs and zero-width formatting characters. A PDF
       viewer emits font-specific codepoints (U+E000-U+F8FF) for things like
       list bullets. They carry no meaning, inflate token counts, and survive
       storage invisibly.

    2. Spans whose case and letters have been mangled into something that
       reads like a substitution cipher. Because ROT-style encoding is a
       known jailbreak vector, the safety classifier refuses the ENTIRE
       request -- stop_reason 'refusal', empty content array, null
       explanation. The user sees a blank response with no indication of
       which message, let alone which span, caused it, and every retry fails
       identically.

    Accepts a plain string or a list of content blocks and returns the same
    shape. Never raises: a failure here would break sending outright, which
    is strictly worse than shipping the original bytes.
    """
    try:
        from app.utils.garbled_text_detector import (
            normalize_paste_artifacts, redact_garbled,
        )
    except Exception:  # pragma: no cover - guard must never break sending
        return content

    def _clean_text(text: str, where: str) -> str:
        if not isinstance(text, str) or not text:
            return text

        text, replaced = normalize_paste_artifacts(text)
        if replaced:
            logger.info(
                "🧯 PASTE_ARTIFACTS: normalized %d private-use/zero-width "
                "character(s) in %s", replaced, where,
            )

        if len(text) < _MIN_SCANNABLE:
            return text

        cleaned, spans = redact_garbled(text)
        for span in spans:
            logger.warning(
                "🧯 GARBLE_GUARD: redacted %d chars at %d:%d in %s "
                "(upper=%.2f vowelless=%.2f). This span resembled encoded "
                "text and would cause the provider to refuse the request.",
                span.length, span.start, span.end, where,
                span.upper_frac, span.vowelless_frac,
            )
        return cleaned

    try:
        if isinstance(content, str):
            return _clean_text(content, describe)
        if isinstance(content, list):
            out = []
            for index, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    original = block.get("text", "")
                    updated = _clean_text(original, f"{describe} block {index}")
                    if updated != original:
                        block = {**block, "text": updated}
                out.append(block)
            return out
    except Exception as exc:  # pragma: no cover - never break sending
        logger.warning("🧯 GARBLE_GUARD: skipped %s (%s: %s)",
                       describe, type(exc).__name__, exc)
    return content


def sanitize_filename(filename: str) -> str:
    """
    Sanitizes a filename to ensure it's safe for filesystem operations.
    
    Args:
        filename (str): The filename to sanitize
        
    Returns:
        str: The sanitized filename
    """
    # Remove potentially dangerous characters
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    return ''.join(c for c in filename if c not in invalid_chars)

def clean_backtick_sequences(text: str) -> str:
    """
    Cleans up problematic backtick sequences while preserving content within code blocks.
    Ensures all code blocks are properly closed.
    
    Args:
        text (str): The input text containing potential backtick sequences
        
    Returns:
        str: Text with properly closed code blocks and preserved content
    """
    lines = text.split('\n')
    cleaned_lines = []
    in_code_block = False
    current_block_type = None
    
    for line in lines:
        if not in_code_block:
            if line.startswith('```'):
                # Starting a new block
                in_code_block = True
                # Capture the block type (diff, python, etc.)
                current_block_type = line[3:].strip() if len(line) > 3 else None
                cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)
        else:
            # Inside a code block - collect content until closing backticks
            if line.strip() == '```':
                # Only close block if it's a bare ``` without a type specifier
                if len(line.strip()) == 3:
                    in_code_block = False
                    current_block_type = None
                    cleaned_lines.append(line)
                else:
                    # This is a nested block marker, preserve it
                    cleaned_lines.append(line)
            else:
                # Within a code block, preserve content exactly as it appears
                cleaned_lines.append(line)
    
    # If we ended with an open code block, close it
    if in_code_block:
        cleaned_lines.append('```')
        current_block_type = None
    
    return '\n'.join(cleaned_lines)

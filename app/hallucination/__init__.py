"""
Hallucination detection subsystem.

Provides session-scoped content fingerprinting and region-aware pattern
matching to detect when the model reproduces real tool output as prose
instead of issuing a proper tool_use block.

See .ziya/hallucination-detection-design.md for the full design.
"""
from .region_extraction import extract_scannable_regions, scannable_text
from .fake_shell_detector import FakeShellMatch, detect_fake_shell_session
from .shingle_index import (
    ShingleIndex,
    ShingleMatch,
    ToolResultFingerprint,
    check_for_parroting,
    clear_session,
    get_default_index,
    register_tool_result,
)

__all__ = [
    "FakeShellMatch",
    "detect_fake_shell_session",
    "extract_scannable_regions",
    "scannable_text",
    "ShingleIndex",
    "ShingleMatch",
    "ToolResultFingerprint",
    "check_for_parroting",
    "clear_session",
    "get_default_index",
    "register_tool_result",
]

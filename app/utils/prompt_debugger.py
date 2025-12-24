"""
Prompt Debugger - Comprehensive logging for prompt assembly.

Enable with environment variable: ZIYA_DEBUG_PROMPTS=true

This module provides detailed logging of:
1. System prompt assembly (template + extensions)
2. Codebase content assembly (file changes + actual files)
3. Final message structure sent to the model
4. Token estimates for each section
5. Duplicate content detection
"""

import os
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime


def is_prompt_debug_enabled() -> bool:
    """Check if prompt debugging is enabled via environment variable."""
    return os.environ.get("ZIYA_DEBUG_PROMPTS", "false").lower() in ("true", "1", "yes")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token average)."""
    return len(text) // 4


def _hash_content(content: str) -> str:
    """Generate short hash for content identification."""
    return hashlib.md5(content.encode()).hexdigest()[:8]


def _find_duplicates(text: str, min_length: int = 100) -> List[Dict[str, Any]]:
    """Find duplicate sections in text."""
    duplicates = []
    lines = text.split('\n')
    
    # Look for repeated multi-line blocks
    seen_blocks = {}
    block_size = 5  # Look for 5-line blocks
    
    for i in range(len(lines) - block_size):
        block = '\n'.join(lines[i:i + block_size])
        if len(block) >= min_length:
            block_hash = _hash_content(block)
            if block_hash in seen_blocks:
                duplicates.append({
                    'first_occurrence': seen_blocks[block_hash],
                    'second_occurrence': i,
                    'preview': block[:200] + '...' if len(block) > 200 else block
                })
            else:
                seen_blocks[block_hash] = i
    
    return duplicates


def log_prompt_assembly(
    stage: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    check_duplicates: bool = True
) -> None:
    """
    Log a stage of prompt assembly with detailed analysis.
    
    Args:
        stage: Name of the assembly stage (e.g., "base_template", "after_extensions")
        content: The content at this stage
        metadata: Optional metadata about this stage
        check_duplicates: Whether to check for duplicate content
    """
    if not is_prompt_debug_enabled():
        return
    
    from app.utils.logging_utils import logger
    
    separator = "=" * 80
    logger.info(f"\n{separator}")
    logger.info(f"📋 PROMPT DEBUG [{stage.upper()}] - {datetime.now().isoformat()}")
    logger.info(separator)
    
    # Basic stats
    char_count = len(content)
    token_estimate = _estimate_tokens(content)
    line_count = content.count('\n') + 1
    content_hash = _hash_content(content)
    
    logger.info(f"📊 Stats: {char_count:,} chars | ~{token_estimate:,} tokens | {line_count:,} lines | hash: {content_hash}")
    
    if metadata:
        logger.info(f"📎 Metadata: {metadata}")
    
    # Check for specific markers/sections that might be duplicated
    markers = [
        "CRITICAL: FILE CONTENT AUTHORITY",
        "Files being tracked for changes:",
        "SYSTEM: Overall Code Changes",
        "RECENT: Files modified since last",
        "CRITICAL: INSTRUCTION PRESERVATION",
        "<!-- TEMPLATE EXAMPLE START -->",
        "<!-- TEMPLATE EXAMPLE END -->",
        "File: ",
        "MCP Tool Usage",
        "CLAUDE FAMILY INSTRUCTIONS",
        "TOOL USAGE PRIORITIZATION",
        "MANDATORY PRE-DIFF VALIDATION",
        "CRITICAL VERIFICATION CHECKPOINT",
    ]
    
    logger.info("🔍 Section markers found:")
    for marker in markers:
        count = content.count(marker)
        if count > 0:
            # Find positions
            positions = []
            start = 0
            while True:
                pos = content.find(marker, start)
                if pos == -1:
                    break
                positions.append(pos)
                start = pos + 1
            
            status = "⚠️ DUPLICATE!" if count > 1 else "✓"
            logger.info(f"   {status} '{marker[:50]}': {count}x at char positions {positions}")
    
    # Check for duplicates if requested
    if check_duplicates:
        duplicates = _find_duplicates(content)
        if duplicates:
            logger.warning(f"⚠️ Found {len(duplicates)} potential duplicate sections:")
            for dup in duplicates[:5]:  # Show first 5
                logger.warning(f"   Lines {dup['first_occurrence']} and {dup['second_occurrence']}: {dup['preview'][:100]}...")
    
    # Show structure breakdown
    logger.info("📑 Content structure (first 3000 chars):")
    preview = content[:3000]
    for i, line in enumerate(preview.split('\n')[:50]):
        if line.strip():
            logger.info(f"   {i+1:4d}: {line[:120]}{'...' if len(line) > 120 else ''}")
    
    if len(content) > 3000:
        logger.info(f"   ... [{len(content) - 3000:,} more chars]")
    
    logger.info(separator + "\n")


def log_final_messages(messages: List[Any]) -> None:
    """
    Log the final message structure being sent to the model.
    """
    if not is_prompt_debug_enabled():
        return
    
    from app.utils.logging_utils import logger
    
    separator = "=" * 80
    logger.info(f"\n{separator}")
    logger.info(f"🚀 FINAL MESSAGES TO MODEL - {datetime.now().isoformat()}")
    logger.info(separator)
    
    total_tokens = 0
    
    for i, msg in enumerate(messages):
        if hasattr(msg, 'content'):
            content = msg.content
            msg_type = type(msg).__name__
        elif isinstance(msg, dict):
            content = msg.get('content', '')
            msg_type = msg.get('type', msg.get('role', 'unknown'))
        else:
            content = str(msg)
            msg_type = 'unknown'
        
        tokens = _estimate_tokens(content)
        total_tokens += tokens
        
        logger.info(f"\n📨 Message {i + 1}: {msg_type} (~{tokens:,} tokens, {len(content):,} chars)")
        
        if 'system' in msg_type.lower():
            log_prompt_assembly(f"system_message_{i}", content, check_duplicates=True)
        else:
            preview = content[:500] if content else "(empty)"
            logger.info(f"   Preview: {preview}{'...' if len(content) > 500 else ''}")
    
    logger.info(f"\n📊 TOTAL: {len(messages)} messages, ~{total_tokens:,} estimated tokens")
    logger.info(separator + "\n")


def log_codebase_assembly(
    overall_changes: str,
    recent_changes: str,
    codebase: str,
    final_result: str
) -> None:
    """Log the codebase assembly process."""
    if not is_prompt_debug_enabled():
        return
    
    from app.utils.logging_utils import logger
    
    logger.info("\n" + "=" * 80)
    logger.info("📁 CODEBASE ASSEMBLY DEBUG")
    logger.info("=" * 80)
    
    if overall_changes:
        log_prompt_assembly("overall_changes", overall_changes, check_duplicates=False)
    else:
        logger.info("📎 overall_changes: (empty)")
    
    if recent_changes:
        log_prompt_assembly("recent_changes", recent_changes, check_duplicates=False)
    else:
        logger.info("📎 recent_changes: (empty)")
    
    log_prompt_assembly("raw_codebase", codebase, 
                       metadata={"file_count": codebase.count("File: ")},
                       check_duplicates=False)
    
    log_prompt_assembly("final_codebase_result", final_result,
                       metadata={"file_count": final_result.count("File: ")},
                       check_duplicates=True)

"""
Diff analyzer for debugging diff application issues.

This module provides tools for analyzing diffs and diagnosing issues with diff application.
"""

import os
import re
import logging
import difflib
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("ZIYA")

def analyze_diff_failure(original_content: str, diff_content: str, result_content: str, expected_content: str) -> Dict[str, Any]:
    """
    Analyze a diff application failure to identify the root cause.
    
    Args:
        original_content: The original file content
        diff_content: The diff content that was applied
        result_content: The actual result after applying the diff
        expected_content: The expected result
        
    Returns:
        A dictionary with analysis results
    """
    logger.info("Analyzing diff application failure")
    
    analysis = {
        "hunks": [],
        "issues": [],
        "line_differences": [],
        "recommendations": []
    }
    
    # Parse the hunks from the diff
    hunks = _parse_hunks_for_analysis(diff_content)
    analysis["hunks"] = hunks
    
    # Compare the result with the expected content
    result_lines = result_content.splitlines()
    expected_lines = expected_content.splitlines()
    
    # Find differences between result and expected
    line_diffs = list(difflib.unified_diff(
        result_lines,
        expected_lines,
        lineterm='',
        n=3  # Context lines
    ))
    
    # Extract the actual differences
    diff_blocks = _extract_diff_blocks(line_diffs)
    analysis["line_differences"] = diff_blocks
    
    # Analyze each difference block
    for block in diff_blocks:
        issue = _analyze_diff_block(block, hunks)
        if issue:
            analysis["issues"].append(issue)
    
    # Generate recommendations
    analysis["recommendations"] = _generate_recommendations(analysis["issues"])
    
    return analysis

def _parse_hunks_for_analysis(diff_content: str) -> List[Dict[str, Any]]:
    """
    Parse hunks from a diff for analysis purposes.
    
    Args:
        diff_content: The diff content
        
    Returns:
        List of parsed hunks
    """
    hunks = []
    current_hunk = None
    hunk_lines = []
    
    for line in diff_content.splitlines():
        if line.startswith('@@'):
            # Start of a new hunk
            if current_hunk:
                current_hunk["lines"] = hunk_lines
                hunks.append(current_hunk)
                hunk_lines = []
            
            # Parse the hunk header
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', line)
            if match:
                src_start = int(match.group(1))
                src_count = int(match.group(2) or 1)
                dst_start = int(match.group(3))
                dst_count = int(match.group(4) or 1)
                header_info = match.group(5).strip()
                
                current_hunk = {
                    "src_start": src_start,
                    "src_count": src_count,
                    "dst_start": dst_start,
                    "dst_count": dst_count,
                    "header_info": header_info,
                    "header": line
                }
        elif current_hunk is not None:
            hunk_lines.append(line)
    
    # Add the last hunk
    if current_hunk:
        current_hunk["lines"] = hunk_lines
        hunks.append(current_hunk)
    
    return hunks

def _extract_diff_blocks(line_diffs: List[str]) -> List[Dict[str, Any]]:
    """
    Extract blocks of differences from unified diff output.
    
    Args:
        line_diffs: Lines from unified diff
        
    Returns:
        List of difference blocks
    """
    blocks = []
    current_block = None
    
    for line in line_diffs:
        if line.startswith('@@'):
            # Start of a new block
            if current_block:
                blocks.append(current_block)
            
            # Parse the block header
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if match:
                current_block = {
                    "src_start": int(match.group(1)),
                    "src_count": int(match.group(2) or 1),
                    "dst_start": int(match.group(3)),
                    "dst_count": int(match.group(4) or 1),
                    "header": line,
                    "lines": []
                }
        elif current_block is not None:
            current_block["lines"].append(line)
    
    # Add the last block
    if current_block:
        blocks.append(current_block)
    
    return blocks

def _analyze_diff_block(block: Dict[str, Any], hunks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Analyze a difference block to identify the issue.
    
    Args:
        block: The difference block
        hunks: The hunks from the original diff
        
    Returns:
        A dictionary with issue details, or None if no issue found
    """
    issue = {
        "type": "unknown",
        "description": "",
        "block": block,
        "related_hunks": []
    }
    
    # Find hunks that overlap with this block
    for hunk in hunks:
        if _blocks_overlap(block, hunk):
            issue["related_hunks"].append(hunk)
    
    # Analyze the lines in the block
    added_lines = [line[1:] for line in block["lines"] if line.startswith('+')]
    removed_lines = [line[1:] for line in block["lines"] if line.startswith('-')]
    
    # Check for common issues
    if len(added_lines) == 0 and len(removed_lines) > 0:
        # Missing additions
        issue["type"] = "missing_additions"
        issue["description"] = "Lines were removed but not added back"
    elif len(removed_lines) == 0 and len(added_lines) > 0:
        # Extra additions
        issue["type"] = "extra_additions"
        issue["description"] = "Lines were added that shouldn't be"
    elif len(added_lines) > 0 and len(removed_lines) > 0:
        # Line mismatch
        if len(added_lines) == len(removed_lines):
            # Check for whitespace issues
            whitespace_issues = any(a.strip() == r.strip() and a != r for a, r in zip(added_lines, removed_lines))
            if whitespace_issues:
                issue["type"] = "whitespace_mismatch"
                issue["description"] = "Whitespace differences between expected and actual"
            else:
                issue["type"] = "content_mismatch"
                issue["description"] = "Content differences between expected and actual"
        else:
            issue["type"] = "line_count_mismatch"
            issue["description"] = f"Line count mismatch: {len(removed_lines)} removed, {len(added_lines)} added"
    else:
        # No clear issue
        return None
    
    return issue

def _blocks_overlap(block1: Dict[str, Any], block2: Dict[str, Any]) -> bool:
    """
    Check if two blocks overlap.
    
    Args:
        block1: First block
        block2: Second block
        
    Returns:
        True if the blocks overlap, False otherwise
    """
    # Get the line ranges for each block
    block1_start = block1["src_start"]
    block1_end = block1_start + block1["src_count"]
    
    block2_start = block2["src_start"]
    block2_end = block2_start + block2["src_count"]
    
    # Check for overlap
    return (block1_start <= block2_end) and (block2_start <= block1_end)

def _generate_recommendations(issues: List[Dict[str, Any]]) -> List[str]:
    """
    Generate recommendations based on identified issues.
    
    Args:
        issues: List of identified issues
        
    Returns:
        List of recommendations
    """
    recommendations = []
    
    # Count issue types
    issue_types = {}
    for issue in issues:
        issue_type = issue["type"]
        issue_types[issue_type] = issue_types.get(issue_type, 0) + 1
    
    # Generate recommendations based on issue types
    if "whitespace_mismatch" in issue_types:
        recommendations.append("Improve whitespace handling in the diff application")
    
    if "content_mismatch" in issue_types:
        recommendations.append("Check for content normalization issues")
    
    if "line_count_mismatch" in issue_types:
        recommendations.append("Verify line counting logic in hunk application")
    
    if "missing_additions" in issue_types:
        recommendations.append("Check if hunks are being skipped or partially applied")
    
    if "extra_additions" in issue_types:
        recommendations.append("Check for duplicate hunk application")
    
    # Add general recommendations
    if len(issues) > 1:
        recommendations.append("Consider improving hunk ordering for multi-hunk changes")
    
    if not recommendations:
        recommendations.append("Perform manual inspection of the diff application")
    
    return recommendations

def visualize_diff_application(original_lines: List[str], hunks: List[Dict[str, Any]], result_lines: List[str]) -> str:
    """
    Generate a visualization of how hunks are applied to the original content.
    
    Args:
        original_lines: The original file lines
        hunks: The hunks to apply
        result_lines: The resulting file lines
        
    Returns:
        A string with the visualization
    """
    visualization = []
    
    # Add a header
    visualization.append("=== Diff Application Visualization ===")
    visualization.append("")
    
    # Show the hunks
    visualization.append(f"Number of hunks: {len(hunks)}")
    for i, hunk in enumerate(hunks):
        visualization.append(f"Hunk #{i+1}: {hunk['header']}")
    visualization.append("")
    
    # Show the original content with hunk application points
    visualization.append("=== Original Content with Hunk Application Points ===")
    for i, line in enumerate(original_lines):
        line_num = i + 1
        hunk_markers = []
        
        for j, hunk in enumerate(hunks):
            if hunk["src_start"] <= line_num <= hunk["src_start"] + hunk["src_count"] - 1:
                hunk_markers.append(f"H{j+1}")
        
        marker_str = f"[{','.join(hunk_markers)}]" if hunk_markers else ""
        visualization.append(f"{line_num:4d} {marker_str:10s} {line.rstrip()}")
    
    visualization.append("")
    
    # Show the result content
    visualization.append("=== Result Content ===")
    for i, line in enumerate(result_lines):
        visualization.append(f"{i+1:4d} {line.rstrip()}")
    
    return "\n".join(visualization)

def debug_hunk_application(file_path: str, diff_content: str) -> None:
    """
    Debug the application of a diff to a file.
    
    Args:
        file_path: Path to the file
        diff_content: The diff content
    """
    logger.info(f"Debugging hunk application for {file_path}")
    
    # Read the original content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
            original_lines = original_content.splitlines(True)
    except FileNotFoundError:
        logger.error(f"File {file_path} not found")
        return
    
    # Parse the hunks
    from ..application.difflib_apply import parse_unified_diff_exact_plus
    hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
    
    logger.info(f"Parsed {len(hunks)} hunks from diff")
    
    # Log each hunk
    for i, hunk in enumerate(hunks):
        logger.info(f"Hunk #{i+1}:")
        logger.info(f"  Source: lines {hunk['src_start']}-{hunk['src_start'] + hunk['src_count'] - 1}")
        logger.info(f"  Destination: lines {hunk['dst_start']}-{hunk['dst_start'] + hunk['dst_count'] - 1}")
        
        # Log the lines in the hunk
        logger.info("  Lines:")
        for line in hunk['lines']:
            if line.startswith('+'):
                logger.info(f"    + {line[1:]}")
            elif line.startswith('-'):
                logger.info(f"    - {line[1:]}")
            else:
                logger.info(f"      {line}")
    
    # Try applying each hunk individually
    for i, hunk in enumerate(hunks):
        logger.info(f"Applying hunk #{i+1} individually")
        
        # Make a copy of the original lines
        test_lines = original_lines.copy()
        
        # Apply the hunk
        from ..application.hunk_applier import apply_hunk
        success, modified_lines = apply_hunk(test_lines, hunk)
        
        if success:
            logger.info(f"  Hunk #{i+1} applied successfully")
        else:
            logger.error(f"  Failed to apply hunk #{i+1}")
            
            # Try to identify the issue
            logger.info("  Analyzing hunk application failure")
            
            # Check if the hunk context matches
            context_match = _check_hunk_context(test_lines, hunk)
            if not context_match:
                logger.error("  Hunk context does not match the file content")
            
            # Check for line number issues
            if hunk['src_start'] > len(test_lines):
                logger.error(f"  Hunk source start ({hunk['src_start']}) is beyond the end of the file ({len(test_lines)} lines)")
    
    # Try applying hunks in different orders
    from ..application.hunk_ordering import optimize_hunk_order
    optimized_order = optimize_hunk_order(hunks)
    
    logger.info(f"Optimized hunk order: {optimized_order}")
    
    # Apply hunks in optimized order
    test_lines = original_lines.copy()
    all_success = True
    
    for i in optimized_order:
        hunk = hunks[i]
        logger.info(f"Applying hunk #{i+1} in optimized order")
        
        # Apply the hunk
        from ..application.hunk_applier import apply_hunk
        success, test_lines = apply_hunk(test_lines, hunk)
        
        if not success:
            logger.error(f"  Failed to apply hunk #{i+1} in optimized order")
            all_success = False
            break
    
    if all_success:
        logger.info("All hunks applied successfully in optimized order")
    else:
        logger.error("Failed to apply all hunks in optimized order")

def _check_hunk_context(file_lines: List[str], hunk: Dict[str, Any]) -> bool:
    """
    Check if a hunk's context matches the file content.
    
    Args:
        file_lines: The file lines
        hunk: The hunk to check
        
    Returns:
        True if the context matches, False otherwise
    """
    # Get the context lines from the hunk
    context_lines = []
    for line in hunk['lines']:
        if not line.startswith('+') and not line.startswith('-'):
            context_lines.append(line)
    
    # Get the corresponding lines from the file
    src_start = hunk['src_start'] - 1  # Convert to 0-based index
    src_end = src_start + hunk['src_count']
    
    if src_start < 0 or src_end > len(file_lines):
        return False
    
    file_section = file_lines[src_start:src_end]
    
    # Remove the removed lines from the file section
    removed_indices = []
    current_idx = 0
    
    for line in hunk['lines']:
        if line.startswith('-'):
            # This is a line to remove
            while current_idx < len(file_section):
                if file_section[current_idx].rstrip() == line[1:].rstrip():
                    removed_indices.append(current_idx)
                    current_idx += 1
                    break
                current_idx += 1
    
    # Create a version of file_section without the removed lines
    file_context = [line for i, line in enumerate(file_section) if i not in removed_indices]
    
    # Compare the context lines
    if len(file_context) != len(context_lines):
        return False
    
    for file_line, context_line in zip(file_context, context_lines):
        if file_line.rstrip() != context_line.rstrip():
            return False
    
    return True

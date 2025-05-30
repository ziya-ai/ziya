from typing import List, Dict, Any, Optional
from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError

def apply_sequential_hunks_with_order(original_lines: List[str], hunks: List[Dict[str, Any]], hunk_order: List[int]) -> Optional[str]:
    """
    Apply hunks sequentially in the specified order.
    
    Args:
        original_lines: The original file lines
        hunks: The hunks to apply
        hunk_order: The order in which to apply the hunks
        
    Returns:
        The modified content if successful, None otherwise
    """
    logger.info("Applying hunks sequentially with specified order")
    
    # Make a copy of the original lines to avoid modifying the input
    modified_lines = original_lines.copy()
    
    # Track line offset as we apply hunks
    line_offset = 0
    
    # Apply each hunk in the specified order
    for i in hunk_order:
        if i < 0 or i >= len(hunks):
            logger.warning(f"Invalid hunk index {i}, skipping")
            continue
            
        hunk = hunks[i]
        logger.debug(f"Applying hunk #{i+1} in sequential order with offset {line_offset}")
        
        # Adjust the hunk's position based on previous modifications
        old_start = hunk.get('old_start', 1)
        adjusted_start = max(0, old_start + line_offset - 1)  # Convert to 0-based and apply offset
        
        # Apply the hunk with the adjusted position
        from .hunk_applier import apply_hunk
        success, modified_lines = apply_hunk(modified_lines, hunk)
        
        if not success:
            logger.warning(f"Failed to apply hunk #{i+1} in sequential order")
            return None
    
        # Update line offset based on the net change in lines
        old_count = len(hunk.get('old_block', []))
        new_count = len(hunk.get('new_lines', []))
        net_change = new_count - old_count
        line_offset += net_change
    
    # Join the lines back into content
    return ''.join(modified_lines)

def apply_diff_expected_result(original_lines: List[str], diff_content: str) -> str:
    """
    Calculate the expected result of applying a diff to original content.
    This is used for debugging and validation purposes.
    
    Args:
        original_lines: The original file lines
        diff_content: The diff content
        
    Returns:
        The expected result content
    """
    # Create a temporary file with the original content
    import tempfile
    import os
    import subprocess
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
        temp_file.write(''.join(original_lines))
        temp_path = temp_file.name
    
    try:
        # Create a temporary diff file
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as diff_file:
            diff_file.write(diff_content)
            diff_path = diff_file.name
        
        # Apply the diff using the system patch command
        result = subprocess.run(
            ['patch', '-p0', temp_path, diff_path],
            capture_output=True,
            text=True
        )
        
        # Read the result
        with open(temp_path, 'r') as f:
            result_content = f.read()
        
        return result_content
    except Exception as e:
        logger.error(f"Error calculating expected diff result: {str(e)}")
        return ''.join(original_lines)
    finally:
        # Clean up temporary files
        try:
            os.unlink(temp_path)
            os.unlink(diff_path)
        except:
            pass

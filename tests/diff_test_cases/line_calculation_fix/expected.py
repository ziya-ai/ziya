def apply_changes(final_lines, stripped_original, remove_pos, old_count):
    """Test case for line calculation fixes"""
    # First block - end_remove calculation
    available_lines = len(final_lines) - remove_pos
    actual_old_count = min(old_count, available_lines)
    end_remove = min(remove_pos + actual_old_count, len(final_lines))
    total_lines = len(final_lines)
    
    # Some intermediate processing
    process_lines(final_lines[remove_pos:end_remove])
    
    # Second block - available_lines calculation
    remove_pos = clamp(remove_pos, 0, len(stripped_original))
    # Adjust old_count if we're near the end of file
    available_lines = len(stripped_original) - remove_pos
    actual_old_count = min(old_count, available_lines)
    end_remove = remove_pos + actual_old_count
    total_lines = len(final_lines)
    return end_remove, total_lines

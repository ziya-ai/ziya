def process_data(data):
    """
    Process the input data
    
    Args:
        data: The data to process
        
    Returns:
        The processed data
    """
    # Extract the remaining hunks (which might still contain multiple files)
    remaining_data = extract_remaining_data()

    # --- START NEW FILTERING LOGIC ---
    filtered_data = ""
    if remaining_data.strip():
        individual_remaining_diffs = split_combined_diff(remaining_data)
        logger.debug(f"Split remaining diff into {len(individual_remaining_diffs)} parts for difflib stage.")
        for diff_part in individual_remaining_diffs:
            target = extract_target_file_from_diff(diff_part)
            # Compare normalized paths relative to the codebase dir
            target_full_path = os.path.normpath(os.path.join(user_codebase_dir, target)) if target else None
            current_file_full_path = os.path.normpath(data.path)
            logger.debug(f"Checking diff part target: '{target}' (Full: {target_full_path}) against current file: {current_file_full_path}")
            if target_full_path == current_file_full_path:
                filtered_data = diff_part
                logger.info(f"Found relevant diff part for {data.path} for difflib stage.")
                break
        else: # No break occurred
             logger.warning(f"No relevant hunks remaining for {data.path} in difflib stage.")
             # If no relevant diff part is found, but changes were written earlier, complete.
             # Otherwise, let the pipeline continue to mark remaining pending hunks as failed.
             if data.changes_written and not any(t.status == HunkStatus.PENDING for t in data.hunks.values()):
                 complete()
                 return data.to_dict()
             # If no changes written and no relevant hunks, it might be an error or already applied.
             # Let the rest of the logic handle this based on hunk statuses.
             pass # Continue to the end of the function
    else:
        logger.warning("No valid hunks remaining to process before difflib stage.")
        # If no changes written and no relevant hunks left, it might be an error or already applied.
        # Let the rest of the logic handle this based on hunk statuses.
        if data.changes_written:
            complete()
            return data.to_dict()
        else:
            # Let the rest of the logic handle this based on hunk statuses.
            pass # Continue to the end of the function

    # --- END NEW FILTERING LOGIC ---

    # Read the current content after previous stages
    try:
        with open(data.path, 'r', encoding='utf-8') as f:
            current_lines = f.readlines()
    except Exception as e:
        logger.error(f"Error reading file: {str(e)}")
        return False

    content_changed = check_content_changes(data)
    if content_changed:
        data.changes_written = True

    # Pass only the relevant diff part to the difflib stage
    # Only run difflib if there's actually a diff part for this file
    if filtered_data.strip():
        difflib_result = run_difflib_stage(data, data.path, filtered_data, current_lines)
    else:
        # If no relevant diff, skip difflib stage for this file
        logger.info(f"Skipping difflib stage for {data.path} as no relevant hunks remain.")
        difflib_result = False # Indicate no changes were made in this stage     # Stage 4: LLM Resolver (stub for now)
    # This would be implemented in the future to handle complex cases

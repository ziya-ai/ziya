"""
Test file for false positive hunk detection.
"""
import os
import json
import subprocess

def process_failures(pipeline, failures):
    """Process failures from diff application."""
    # Map failures to hunks
    for failure in failures:
        hunk_idx = failure.get("details", {}).get("hunk")
        if hunk_idx:
            pipeline.update_hunk_status(
                hunk_id=hunk_idx,
                stage="DIFFLIB",
                status="FAILED",
                error_details=failure.get("details"),
                confidence=failure.get("details", {}).get("confidence", 0.0)
            )
            print(f"Marked hunk #{hunk_idx} as FAILED")
    
    # Mark any remaining pending hunks as failed
    for hunk_id, tracker in pipeline.result.hunks.items():
        if tracker.status == "PENDING":
            pipeline.update_hunk_status(
                hunk_id=hunk_id,
                stage="DIFFLIB",
                status="FAILED",
                error_details={"error": "Failed in batch"}
            )
            print(f"Marked remaining hunk #{hunk_id} as FAILED")
    
    return False

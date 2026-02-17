"""
Diff validation hook with automatic context enhancement.

Backend-managed: checks context, adds files, notifies frontend to sync UI.
"""

import os
import re
import logging
from typing import Optional, Dict, Any, Callable, List, Set
from app.utils.logging_utils import logger
from app.utils.diff_utils.validation.pipeline_validator import validate_diff_with_full_pipeline
from app.utils.diff_utils.parsing.diff_parser import extract_target_file_from_diff


class DiffValidationHook:
    """
    Hook that validates diffs and manages context automatically.
    """
    
    def __init__(
        self,
        file_state_manager=None,
        conversation_id: Optional[str] = None,
        enabled: bool = True,
        auto_regenerate: bool = True,
        current_context: Optional[List[str]] = None
    ):
        self.validated_diffs: Dict[str, bool] = {}
        self.file_state_manager = file_state_manager
        self.conversation_id = conversation_id
        self.validation_enabled = enabled
        self.auto_regenerate = auto_regenerate
        self.current_context: Set[str] = set(current_context or [])
        self.added_files: List[str] = []
        self.last_validated_file: Optional[str] = None
        self.successful_diffs: List[str] = []  # Track which diffs passed validation
        self.failed_diff_details: List[Dict[str, Any]] = []  # Track failures with context
        
    def detect_completed_diff(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Detect if content contains a completed diff block.
        """
        pattern = r'(`{3,})diff\n([\s\S]*?)\n\1(?:\n|$)'
        
        matches = list(re.finditer(pattern, content))
        if not matches:
            return None
        
        match = matches[-1]
        diff_content = match.group(2)
        file_path = extract_target_file_from_diff(diff_content)
        diff_key = f"{file_path}:{len(diff_content)}"
        
        if diff_key in self.validated_diffs:
            return None
        
        return {
            "diff_content": diff_content,
            "start_pos": match.start(),
            "end_pos": match.end(),
            "file_path": file_path,
            "diff_key": diff_key
        }
    
    def is_file_in_context(self, file_path: str) -> bool:
        """Check if file is in current context."""
        if not file_path:
            return False
        
        if file_path in self.current_context:
            return True
        
        for context_path in self.current_context:
            if file_path.startswith(context_path + '/'):
                return True
            if context_path.endswith('/') and file_path.startswith(context_path):
                return True
        
        return False
    
    def read_file_for_context(self, file_path: str) -> Optional[str]:
        """Read file content to add to model context."""
        codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        if not codebase_dir:
            return None
        
        full_path = os.path.join(codebase_dir, file_path)
        
        if not os.path.exists(full_path):
            logger.warning(f"Cannot read file for context: {file_path} does not exist")
            return None
        
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return None
    
    def validate_and_enhance(
        self,
        content: str,
        model_messages: List[Dict[str, Any]],
        send_event: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> Optional[str]:
        """
        Validate diffs and automatically enhance context if needed.
        
        Args:
            content: The current streamed content
            model_messages: The conversation messages (to append file content to)
            send_event: Callback to send SSE events to frontend
            
        Returns:
            Model feedback string if ANY hunks failed, None if all succeeded
        """
        if not self.validation_enabled:
            return None
        
        diff_info = self.detect_completed_diff(content)
        if not diff_info:
            return None
        
        diff_content = diff_info["diff_content"]
        file_path = diff_info["file_path"]
        diff_key = diff_info["diff_key"]
        
        if not file_path:
            logger.warning("Could not extract file path from diff, skipping validation")
            return None
        
        self.validated_diffs[diff_key] = True
        
        # Track the file for error messages
        self.last_validated_file = file_path
        
        diff_number = len(self.validated_diffs)
        logger.info(f"🔍 Validating diff #{diff_number} for {file_path}")
        
        logger.info(f"🔍 VALIDATION_HOOK: Diff content length: {len(diff_content)}")
        logger.info(f"🔍 VALIDATION_HOOK: First 200 chars: {diff_content[:200]}")
        
        # In CLI mode, suppress verbose validation warnings
        is_cli_mode = os.environ.get('ZIYA_MODE') == 'chat'
        original_levels = {}
        if is_cli_mode:
            # Suppress WARNING/ERROR logs from validation pipeline
            for logger_name in ['app.utils.diff_utils.application.git_diff',
                               'app.utils.diff_utils.pipeline.pipeline_manager',
                               'app.utils.diff_utils.application.patch_apply',
                               'app.utils.diff_utils.application.fuzzy_match']:
                target_logger = logging.getLogger(logger_name)
                original_levels[logger_name] = target_logger.level
                target_logger.setLevel(logging.CRITICAL)
        
        if send_event:
            send_event("diff_validation_status", {
            })
        
        try:
            validation_result = validate_diff_with_full_pipeline(diff_content, file_path)
            
            # Restore log levels
            if is_cli_mode:
                for logger_name, level in original_levels.items():
                    logging.getLogger(logger_name).setLevel(level)
            
            # Initialize variables at function scope
            context_was_enhanced = False
            
            # Check if ANY hunks failed
            has_failures = len(validation_result["failed_hunks"]) > 0
            
            # CRITICAL: Also check if validation returned error status (e.g., file doesn't exist)
            # This handles cases where validation fails BEFORE we even parse hunks
            if validation_result["status"] == "error" and validation_result["total_hunks"] == 0:
                has_failures = True
                logger.warning(f"❌ Validation failed for {file_path}: {validation_result['model_feedback']}")
            
            if has_failures:
                
                # Record this failure
                self.failed_diff_details.append({
                    "diff_number": diff_number,
                    "file_path": file_path,
                    "reason": validation_result["model_feedback"],
                    "failed_hunks": len(validation_result["failed_hunks"])
                })
                
                # Check if file is in current context
                file_in_context = self.is_file_in_context(file_path)
                
                if not file_in_context:
                    logger.info(f"📂 File {file_path} not in context, adding automatically")
                    
                    # Read file content
                    file_content = self.read_file_for_context(file_path)
                    
                    if file_content:
                        # Add to model context as a user message
                        context_message = {
                            "role": "user",
                            "content": (
                                f"[SYSTEM: Current content of {file_path} for context]\n\n"
                                f"```{self._detect_language(file_path)}\n"
                                f"{file_content}\n"
                                f"```"
                            )
                        }
                        model_messages.append(context_message)
                        
                        self.added_files.append(file_path)
                        self.current_context.add(file_path)
                        context_was_enhanced = True
                        logger.info(f"✅ Added {file_path} to model context ({len(file_content)} chars)")
                        
                        # Immediately notify frontend about context enhancement
                        if send_event:
                            send_event("context_sync", {
                                "added_files": [file_path],
                                "reason": "diff_validation"
                            })
                        context_was_enhanced = True
                        logger.info(f"✅ Added {file_path} to model context ({len(file_content)} chars)")
                
            # Notify frontend to sync UI
            else:
                # SUCCESS - record this diff as valid
                self.successful_diffs.append(file_path)
                
                # Record the applied diff for history
                if self.file_state_manager and self.conversation_id:
                    self.file_state_manager.record_applied_diff(
                        conversation_id=self.conversation_id,
                        file_path=file_path,
                        diff_content=diff_content
                    )
                
                logger.info(f"✅ Diff #{diff_number} validated successfully: {file_path}")
            
            if send_event:
                # Just send informational status - no rewind needed
                send_event("diff_validation_status", {
                    "file_path": file_path,
                    "diff_number": diff_number,
                    "status": "failed" if has_failures else "success",
                    "failed_hunks": validation_result["failed_hunks"],
                    "total_hunks": validation_result["total_hunks"],
                    "context_enhanced": context_was_enhanced,
                    "added_files": self.added_files if context_was_enhanced else [],
                    "message": (
                        f"Diff #{diff_number} validation failed" if has_failures
                        else f"Diff #{diff_number} validated"
                    )
                })
                
            if self.auto_regenerate:
                # Only return feedback if THIS diff failed
                if has_failures:
                    return self._build_targeted_feedback(
                        diff_number=diff_number,
                        file_path=file_path,
                        diff_content=diff_content,
                        validation_result=validation_result,
                        context_was_enhanced=context_was_enhanced
                    )
            
            # No failures - don't interrupt model
            return None

        except Exception as e:
            logger.error(f"Error during diff validation: {e}")
            if send_event:
                send_event("diff_validation_status", {
                    "status": "error",
                    "file_path": file_path,
                    "error": str(e)
                })
            return None
    
    def _build_targeted_feedback(
        self,
        diff_number: int,
        file_path: str,
        diff_content: str,
        validation_result: Dict[str, Any],
        context_was_enhanced: bool
    ) -> str:
        """
        Build feedback that clearly identifies which specific diff failed.
        
        Key principles:
        1. State which diff number failed
        2. Confirm which diffs succeeded (don't regenerate those)
        3. Provide specific error details
        4. Give clear, actionable instructions
        """
        parts = []
        
        # Lead with the constraint — what NOT to do
        total_validated = len(self.validated_diffs)
        successful_count = len(self.successful_diffs)
        
        parts.append(f"⚠️ DIFF #{diff_number} for {file_path} FAILED validation.")
        parts.append("")
        
        if successful_count > 0:
            parts.append(f"DO NOT regenerate these — they already passed and will be applied:")
            for f in self.successful_diffs:
                parts.append(f"  ✅ {f}")
            parts.append("")
            parts.append(f"ONLY provide a corrected diff for: {file_path}")
        else:
            parts.append(f"Provide a corrected diff for: {file_path}")
        parts.append("")
        
        # Error details
        parts.append("Your diff that failed:")
        parts.append("```diff")
        parts.append(diff_content)
        parts.append("```")
        parts.append("")
        
        # Specific error details
        parts.append("**Problem:**")
        parts.append(validation_result["model_feedback"])
        parts.append("")
        
        if context_was_enhanced:
            parts.append(f"The current content of {file_path} has been added to your context above.")
            parts.append("Use it to verify line numbers and context lines in your corrected diff.")
        else:
            parts.append("Use the error details above to fix the hunk context/line numbers.")
        
        
        return "\n".join(parts)
    
    def get_validation_summary(self) -> Dict[str, Any]:
        """Get summary of all validation results."""
        return {
            "total_validated": len(self.validated_diffs),
            "successful": len(self.successful_diffs),
            "failed": len(self.failed_diff_details),
            "successful_files": self.successful_diffs,
            "failed_details": self.failed_diff_details
        }
    
    def reset_validation_state(self):
        """Reset state between conversation turns."""
        self.validated_diffs.clear()
        self.successful_diffs.clear()
        self.failed_diff_details.clear()
        self.added_files.clear()
    
    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension for code fence."""
        ext = file_path.split('.')[-1].lower()
        lang_map = {
            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
            'jsx': 'javascript', 'tsx': 'typescript', 'java': 'java',
            'cpp': 'cpp', 'c': 'c', 'go': 'go', 'rs': 'rust',
            'rb': 'ruby', 'php': 'php', 'swift': 'swift'
        }
        return lang_map.get(ext, 'text')

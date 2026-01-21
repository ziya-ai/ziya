"""
Diff validation hook with automatic context enhancement.

Backend-managed: checks context, adds files, notifies frontend to sync UI.
"""

import os
import re
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
        enabled: bool = True,
        auto_regenerate: bool = True,
        current_context: Optional[List[str]] = None
    ):
        self.validated_diffs: Dict[str, bool] = {}
        self.validation_enabled = enabled
        self.auto_regenerate = auto_regenerate
        self.current_context: Set[str] = set(current_context or [])
        self.added_files: List[str] = []
        self.last_validated_file: Optional[str] = None
        
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
        
        logger.info(f"🔍 Validating completed diff for {file_path}")
        
        logger.info(f"🔍 VALIDATION_HOOK: Diff content length: {len(diff_content)}")
        logger.info(f"🔍 VALIDATION_HOOK: First 200 chars: {diff_content[:200]}")
        
        if send_event:
            send_event("diff_validation_status", {
            })
        
        try:
            validation_result = validate_diff_with_full_pipeline(diff_content, file_path)
            
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
            if send_event:
                send_event("diff_validation_failed", {
                    "file_path": file_path,
                    "status": validation_result["status"],
                    "failed_hunks": validation_result["failed_hunks"],
                    "total_hunks": validation_result["total_hunks"],
                    "context_enhanced": context_was_enhanced,
                    "added_files": self.added_files if context_was_enhanced else [],
                    "user_message": f"Regenerating diff for {file_path} - {len(validation_result['failed_hunks'])} hunk(s) failed"
                })
                
            if self.auto_regenerate:
                feedback = validation_result["model_feedback"]
                
                if context_was_enhanced:
                    feedback += (
                        f"\n\n📂 CONTEXT ENHANCED: "
                        f"Added {len(self.added_files)} file(s) to context. "
                        f"Regenerate the diff using the current file content shown above."
                    )
                
                return feedback

        except Exception as e:
            logger.error(f"Error during diff validation: {e}")
            if send_event:
                send_event("diff_validation_status", {
                    "status": "error",
                    "file_path": file_path,
                    "error": str(e)
                })
            return None
    
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

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


def _utf16_units(text: str):
    """Yield UTF-16 code units, matching JavaScript's charCodeAt() iteration."""
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            yield 0xD800 + (cp >> 10)
            yield 0xDC00 + (cp & 0x3FF)
        else:
            yield cp


def diff_body_hash(body: str) -> str:
    """Content identity of a ```diff fence body, shared with the frontend.

    The frontend marks a rejected diff as superseded by matching this value
    against each rendered diff token (diffUtils.ts diffBodyHash), so the two
    implementations must agree byte for byte: CRLF folded to LF, surrounding
    whitespace stripped (marked drops the fence's trailing newline; the
    server regex does not), then FNV-1a 32-bit over UTF-16 code units so a
    non-BMP character hashes the same as JS's surrogate pairs. Pinned by the
    shared golden vector in test_diff_validation_rejection_marker.py and
    diffRejectedMarker.test.ts. A fence ordinal was rejected as the identity
    because the renderer absorbs/merges headerless continuation fences, so
    its indices do not line up with the server's.
    """
    text = body.replace('\r\n', '\n').strip()
    h = 0x811C9DC5
    for unit in _utf16_units(text):
        h ^= unit
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, '08x')


def reader_reason(validation_result: Dict[str, Any]) -> str:
    """A one-line, human-addressed reason a diff was refused.

    Built from the validator's structured fields, never from model_feedback:
    that text opens with "Please provide a corrected diff", an instruction to
    the model that was being shown verbatim in the chat notice. The zero-hunk
    early-exit paths carry no structured code, so those are recognised by the
    validator's own fixed phrases (pipeline_validator.py) and reworded.
    """
    failed = list(validation_result.get("failed_hunks") or [])
    total = validation_result.get("total_hunks") or 0
    if failed:
        details = validation_result.get("hunk_details") or {}
        kinds: List[str] = []
        for hunk_id in failed:
            err = (details.get(str(hunk_id)) or {}).get("error_details")
            kind = err.get("error") if isinstance(err, dict) else (str(err) if err else "")
            kind = str(kind or "").strip()
            if kind and kind != "unknown error" and kind not in kinds:
                kinds.append(kind)
        noun = "hunk" if len(failed) == 1 else "hunks"
        ids = ", ".join(str(h) for h in failed)
        base = f"{noun} {ids} of {total} did not match the current file"
        return f"{base} ({'; '.join(kinds)[:120]})" if kinds else base
    feedback = str(validation_result.get("model_feedback") or "")
    if validation_result.get("pending_new_file"):
        return "the target file is created by an earlier diff in this response that is not applied yet"
    if "does not exist" in feedback:
        return "the target file does not exist"
    if "Could not extract file path" in feedback:
        return "the diff has no usable file header"
    if "codebase directory not configured" in feedback:
        return "no project directory is configured for validation"
    return "the diff could not be parsed into hunks"


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
        # Diffs rejected by the most recent validate_and_enhance() call, as
        # {file_path, body_hash, reason}. server.py attaches this to the
        # validation_retry event so the frontend can mark exactly those
        # fences unappliable and explain the pause before the correction.
        self.last_rejected: List[Dict[str, Any]] = []
        # The phrase by which the most recent response cited a diff it never
        # emitted (app/utils/phantom_diff.py), or None.  server.py reads it
        # to word the correction request: this is "nothing was produced",
        # not "a diff failed to apply".
        self.phantom_reference: Optional[str] = None
        # Files a new-file fence (``--- /dev/null``) in this response creates.
        # A later fence patching one of them is refused like any diff whose
        # target is missing, but with a reason that names the cause and the
        # correct correction (one complete new-file diff), see _validate_one.
        self.created_in_response: Set[str] = set()
        
    def detect_completed_diffs(self, content: str) -> List[Dict[str, Any]]:
        """
        Every completed, not-yet-validated ```diff fence in content, in order.

        Previously only the LAST fence was returned, so a response carrying
        several diffs had only its final one validated: an earlier broken
        hunk sailed through unchecked and failed at apply time with no
        correction requested.
        """
        pattern = r'(`{3,})diff\n([\s\S]*?)\n\1(?:\n|$)'
        found: List[Dict[str, Any]] = []
        for match in re.finditer(pattern, content):
            fence_body = match.group(2)
            # The frontend marks a rejected FENCE by this hash, so every
            # per-file part below carries the hash of the fence it came from.
            fence_hash = diff_body_hash(fence_body)
            # One entry per file in the fence.  Validating the fence as one
            # unit took the first file's path for all of it, so later files
            # were never checked, and a second file sharing the first one's
            # basename (app/config/write_policy.py and
            # app/mcp_servers/write_policy.py) was merged into its hunks by
            # the pipeline's basename fallback and rejected wholesale, while
            # the frontend, which applies per file, applied every hunk.
            for diff_content in self._split_by_file(fence_body):
                file_path = extract_target_file_from_diff(diff_content)
                diff_key = f"{fence_hash}:{file_path}:{len(diff_content)}"
                if diff_key in self.validated_diffs:
                    continue
                found.append({
                    "diff_content": diff_content,
                    "start_pos": match.start(),
                    "end_pos": match.end(),
                    "file_path": file_path,
                    "diff_key": diff_key,
                    "body_hash": fence_hash,
                })
        return found

    @staticmethod
    def _split_by_file(body: str) -> List[str]:
        """Per-file sections of a fence body; the body itself for one file."""
        starts = [m.start() for m in re.finditer(r'(?m)^diff --git ', body)]
        if len(starts) < 2:
            return [body]
        parts = [body[s:e] for s, e in zip(starts, starts[1:] + [len(body)])]
        return [p.rstrip("\n") for p in parts if p.strip()]

    def detect_completed_diff(self, content: str) -> Optional[Dict[str, Any]]:
        """Last unvalidated fence only. validate_and_enhance() checks them all."""
        found = self.detect_completed_diffs(content)
        return found[-1] if found else None
    
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
        if not file_path:
            return None
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
        Validate every completed diff and enhance context for the failures.
        
        Args:
            content: The current streamed content
            model_messages: The conversation messages (to append file content to)
            send_event: Callback to send SSE events to frontend
            
        Returns:
            Combined model feedback if ANY diff failed, None if all succeeded.
            The failures are also left on self.last_rejected for the caller.
        """
        if not self.validation_enabled:
            return None

        self.last_rejected = []
        self.phantom_reference = None
        feedback_parts: List[str] = []
        for diff_info in self.detect_completed_diffs(content):
            # Two byte-identical fences share a key; the second is a no-op.
            if diff_info["diff_key"] in self.validated_diffs:
                continue
            feedback = self._validate_one(diff_info, model_messages, send_event)
            if feedback:
                feedback_parts.append(feedback)
        if feedback_parts:
            return "\n\n".join(feedback_parts)

        # No fence failed -- but was a fence CITED and never written?  The
        # response says "the diff above" / "apply the diff" while carrying
        # no ```diff at all: the change was decided in reasoning and the
        # next output was a tool call.  Same feedback-and-restart path as a
        # failed fence, so the model is asked for it before the turn ends
        # instead of the user asking "did you apply it?" next turn.  See
        # app/utils/phantom_diff.py for why this recurs.
        from app.utils.phantom_diff import (
            find_phantom_diff_reference, phantom_diff_feedback,
        )
        phrase = find_phantom_diff_reference(content)
        if phrase:
            self.phantom_reference = phrase
            logger.warning(
                f"👻 PHANTOM_DIFF: response cites a diff ({phrase!r}) but "
                f"contains no diff fence; requesting it"
            )
            if send_event:
                send_event("diff_validation_status", {
                    "status": "phantom_reference", "phrase": phrase,
                })
            return phantom_diff_feedback(phrase)
        return None

    def _validate_one(
        self,
        diff_info: Dict[str, Any],
        model_messages: List[Dict[str, Any]],
        send_event: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> Optional[str]:
        """Validate one detected fence; feedback string if it failed."""
        diff_content = diff_info["diff_content"]
        file_path = diff_info["file_path"]
        diff_key = diff_info["diff_key"]
        
        if not file_path:
            logger.warning("Could not extract file path from diff, skipping validation")
            self.validated_diffs[diff_key] = True
            return None
        
        self.validated_diffs[diff_key] = True
        
        # Track the file for error messages
        self.last_validated_file = file_path
        
        diff_number = len(self.validated_diffs)
        logger.info(f"🔍 Validating diff #{diff_number} for {file_path}")
        
        logger.debug(f"🔍 VALIDATION_HOOK: Diff content length: {len(diff_content)}")
        logger.debug(f"🔍 VALIDATION_HOOK: First 200 chars: {diff_content[:200]}")
        
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
                # The target is missing because an EARLIER fence in this same
                # response creates it and the user has not applied that yet.
                # Refusing is right: every fence must apply on its own against
                # the live file, and a patch stacked on an unapplied new-file
                # diff forces apply order on the user.  But the generic advice
                # ("mark as new file creation") would have the model recreate
                # the file from a fragment.  Say what the correction is.
                if (file_path in self.created_in_response
                        and "does not exist" in str(validation_result.get("model_feedback") or "")):
                    validation_result["pending_new_file"] = True
                    validation_result["model_feedback"] = (
                        f"❌ {file_path} does not exist yet: it is created by an earlier diff in "
                        f"this response, which has not been applied. Do not patch a file you are "
                        f"creating in the same response. Re-emit ONE complete new-file diff for "
                        f"{file_path} (--- /dev/null, new file mode 100644) containing the full "
                        f"corrected content, replacing both the earlier new-file diff and this patch."
                    )
            elif not has_failures and (
                    "--- /dev/null" in diff_content or "new file mode" in diff_content):
                self.created_in_response.add(file_path)
            
            if has_failures:
                
                # Record this failure
                self.failed_diff_details.append({
                    "diff_number": diff_number,
                    "file_path": file_path,
                    "body_hash": diff_info["body_hash"],
                    "reason": validation_result["model_feedback"],
                    "failed_hunks": len(validation_result["failed_hunks"])
                })
                self.last_rejected.append({
                    "file_path": file_path,
                    "body_hash": diff_info["body_hash"],
                    "reason": reader_reason(validation_result),
                })
                
                # Always inject current file content on failure — even if the file
                # was already in context. The model needs the live state to generate
                # correct line numbers, not a stale copy from the initial context load.
                file_content = self.read_file_for_context(file_path)
                if file_content:
                    context_message = {
                        "role": "user",
                        "content": (
                            f"[SYSTEM: Current content of {file_path} for diff correction]\n\n"
                            f"```{self._detect_language(file_path)}\n"
                            f"{file_content}\n"
                            f"```"
                        )
                    }
                    model_messages.append(context_message)
                    self.current_context.add(file_path)
                    context_was_enhanced = True
                    logger.info(f"✅ Injected current {file_path} content for retry ({len(file_content)} chars)")
                    if file_path not in self.added_files:
                        self.added_files.append(file_path)
                    if send_event:
                        send_event("context_sync", {
                            "added_files": [file_path],
                            "reason": "diff_validation"
                        })
                
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
                    "body_hash": diff_info["body_hash"],
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
            # No failures — don't interrupt the model.
            #
            # Earlier draft surfaced parser-rescued malformed hunk
            # headers as informational feedback here, but any non-None
            # return triggers the regeneration loop in
            # ``app/server.stream_chunks``, which would force a wasted
            # "correcting failed diffs" turn for a diff that already
            # applied cleanly.  Persistent guidance about counting hunk
            # headers now lives in the system prompt instead — see
            # precision_prompt_system.py.
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
        self.created_in_response.clear()
        self.phantom_reference = None
    
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

from dataclasses import dataclass
import json
import os
import time
import shutil
import threading
from typing import Dict, List, Optional, Set, Tuple
import hashlib
import shutil
from difflib import SequenceMatcher
from app.utils.file_utils import read_file_content
from app.utils.logging_utils import logger
from app.utils.prompt_cache import get_prompt_cache

@dataclass
class FileState:
    """Represents the state of a file at a specific point in time"""
    path: str
    content_hash: str
    line_states: Dict[int, str]  # line number -> state ('+' for added, '*' for modified)
    original_content: List[str]  # Original content when first seen
    current_content: List[str]   # Current content
    last_seen_content: List[str] # Content as of last query
    last_context_submission_content: List[str] # Content as of last context submission

class FileStateManager:
    """Manages file states and changes within a conversation context"""
    
    def __init__(self):
        self.state_file = os.path.join(os.path.expanduser("~"), ".ziya", "file_states.json")
        self.conversation_states: Dict[str, Dict[str, FileState]] = {}
        self._load_state()
        
    def _load_state(self):
        """Load file states from disk."""
        try:
            if os.path.exists(self.state_file):
                # Check file size before loading
                file_size = os.path.getsize(self.state_file)
                if file_size > 50 * 1024 * 1024:  # 50MB limit
                    logger.warning(f"File state file is corrupted (size: {file_size:,} bytes). Creating backup and resetting.")
                    backup_file = self.state_file + f".backup.{int(time.time())}"
                    shutil.move(self.state_file, backup_file)
                    self.conversation_states = {}
                    return
                
                with open(self.state_file, 'r') as f:
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError as e:
                        logger.warning(f"File state JSON corrupted: {e}. Resetting.")
                        backup_file = self.state_file + f".backup.{int(time.time())}"
                        shutil.move(self.state_file, backup_file)
                        self.conversation_states = {}
                        return
                        
                    for conv_id, files in data.items():
                        self.conversation_states[conv_id] = {}
                        for file_path, state_data in files.items():
                            self.conversation_states[conv_id][file_path] = FileState(
                                path=state_data['path'],
                                content_hash=state_data['content_hash'],
                                line_states=state_data['line_states'],
                                original_content=state_data['original_content'],
                                current_content=state_data['current_content'],
                                last_seen_content=state_data['last_seen_content'],
                                last_context_submission_content=state_data.get('last_context_submission_content', state_data['current_content'])
                            )
                if self.conversation_states:
                    logger.info(f"Loaded file states for {len(self.conversation_states)} conversations")
        except Exception as e:
            logger.warning(f"Failed to load file states: {e}")
            self.conversation_states = {}
    
    def _save_state(self):
        """Save file states to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            data = {}
            for conv_id, files in self.conversation_states.items():
                # Skip temporary precision_ conversations
                if conv_id.startswith('precision_'):
                    continue
                    
                data[conv_id] = {}
                for file_path, state in files.items():
                    data[conv_id][file_path] = {
                        'path': state.path,
                        'content_hash': state.content_hash,
                        'line_states': {str(k): v for k, v in state.line_states.items()},  # JSON keys must be strings
                        'original_content': state.original_content,
                        'current_content': state.current_content,
                        'last_seen_content': state.last_seen_content,
                        'last_context_submission_content': state.last_context_submission_content
                    }
            
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved file states for {len(data)} conversations")
        except Exception as e:
            logger.warning(f"Failed to save file states: {e}")
    
    def cleanup_temporary_conversations(self):
        """Remove temporary precision_ conversations from memory."""
        temp_convs = [conv_id for conv_id in self.conversation_states.keys() if conv_id.startswith('precision_')]
        for conv_id in temp_convs:
            del self.conversation_states[conv_id]
        if temp_convs:
            logger.info(f"Cleaned up {len(temp_convs)} temporary conversations from memory")
    
    def initialize_conversation(self, conversation_id: str, files: Dict[str, str], force_reset: bool = False) -> None:
        """
        Initialize file states for a conversation.
        
        Args:
            conversation_id: The conversation ID
            files: Dict mapping file paths to their content
            force_reset: If True, completely reset the conversation state. 
                        If False (default), preserve existing state and only add new files.
        """
        # Check if conversation already exists
        if conversation_id in self.conversation_states and not force_reset:
            logger.info(f"Conversation {conversation_id} already exists with {len(self.conversation_states[conversation_id])} files, updating with {len(files)} files")
            # Don't reset - just update with new files
            self.update_files_in_state(conversation_id, files)
            return
        
        # Only reset if force_reset=True or conversation doesn't exist
        if force_reset or conversation_id not in self.conversation_states:
            self.conversation_states[conversation_id] = {}
            logger.info(f"Initializing new conversation {conversation_id} with {len(files)} files")
            
        for file_path, content in files.items():
            # Split content into lines and initialize state
            lines = content.splitlines()
            self.conversation_states[conversation_id][file_path] = FileState(
                path=file_path,
                content_hash=self._compute_hash(lines),
                line_states={},  # No changes initially
                original_content=lines.copy(),
                current_content=lines.copy(),
                last_seen_content=lines.copy(),
                last_context_submission_content=lines.copy()
                # Note: line_states is initialized as empty here
                # New files should have all lines marked as new ('+')
                # This will be handled in update_file_state
            )
            logger.debug(f"Initialized state for {file_path} with {len(lines)} lines")
        
        self._save_state()  # Persist new conversation state
    
    def get_changes_since_last_submission(self, conversation_id: str) -> Dict[str, Set[int]]:
        """Get changes that occurred since the last context submission."""
        if conversation_id not in self.conversation_states:
            return {}
            
        recent_changes = {}
        for file_path, state in self.conversation_states[conversation_id].items():
            if state.current_content != state.last_context_submission_content:
                # Calculate which lines changed since last submission
                from difflib import SequenceMatcher
                matcher = SequenceMatcher(None, state.last_context_submission_content, state.current_content)
                changed_lines = set()
                
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag in ('replace', 'insert'):
                        changed_lines.update(range(j1 + 1, j2 + 1))
                
                if changed_lines:
                    recent_changes[file_path] = changed_lines
                    logger.debug(f"Recent changes in {file_path}: {len(changed_lines)} lines")
        
        return recent_changes
    
    def refresh_file_from_disk(self, conversation_id: str, file_path: str, base_dir: str) -> bool:
        """
        Refresh a file's current_content from disk without resetting the baseline.
        
        This is critical for detecting when a user has NOT applied a suggested diff -
        the file on disk won't have changed, but we need to make sure we're tracking
        the actual file content, not what we previously saw or suggested.
        
        Args:
            conversation_id: The conversation ID
            file_path: Relative path to the file
            base_dir: Base directory for resolving full paths
            
        Returns:
            True if the file was refreshed and had changes, False otherwise
        """
        if conversation_id not in self.conversation_states:
            logger.warning(f"Cannot refresh file for unknown conversation {conversation_id}")
            return False
            
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            logger.debug(f"File {file_path} not in conversation {conversation_id}, skipping refresh")
            return False
        
        # Read current content from disk
        full_path = os.path.join(base_dir, file_path)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                disk_content = f.read()
        except Exception as e:
            logger.warning(f"Could not read {full_path} from disk: {e}")
            return False
        
        disk_lines = disk_content.splitlines()
        current_hash = self._compute_hash(disk_lines)
        
        # Check if disk content differs from what we have
        if current_hash != state.content_hash:
            logger.info(f"File {file_path} changed on disk since last check")
            # Update current content but preserve original baseline
            changed_lines = self._compute_changes(state.original_content, state.current_content, disk_lines)
            state.current_content = disk_lines
            state.content_hash = current_hash
            
            # Update line states for changed lines
            for line_num in changed_lines:
                if line_num not in state.line_states:
                    state.line_states[line_num] = '+'
                elif state.line_states[line_num] != '+':
                    state.line_states[line_num] = '*'
            
            return True
        
        return False
    
    def refresh_all_files_from_disk(self, conversation_id: str, base_dir: str) -> Dict[str, bool]:
        """
        Refresh all files in a conversation from disk.
        
        Args:
            conversation_id: The conversation ID
            base_dir: Base directory for resolving full paths
            
        Returns:
            Dict mapping file paths to whether they changed
        """
        if conversation_id not in self.conversation_states:
            return {}
        
        results = {}
        for file_path in list(self.conversation_states[conversation_id].keys()):
            results[file_path] = self.refresh_file_from_disk(conversation_id, file_path, base_dir)
        
        changed_files = [f for f, changed in results.items() if changed]
        if changed_files:
            logger.info(f"Refreshed {len(changed_files)} files from disk for conversation {conversation_id}: {changed_files}")
            self._save_state()
        
        return recent_changes
    
    def has_changes_since_last_context_submission(self, conversation_id: str, file_path: str) -> bool:
        """Check if a file has changes since the last context submission."""
        if conversation_id not in self.conversation_states:
            return False
        
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            return False
        
        return state.current_content != state.last_context_submission_content
    
    def refresh_file_from_disk(self, conversation_id: str, file_path: str, base_dir: str) -> bool:
        """
        Refresh a file's current_content from disk without resetting the baseline.
        
        This is critical for detecting when a user has NOT applied a suggested diff -
        the file on disk won't have changed, but we need to track the actual state.
        
        Returns:
            True if the file was refreshed and had changes, False otherwise
        """
        if conversation_id not in self.conversation_states:
            logger.warning(f"Cannot refresh file for unknown conversation {conversation_id}")
            return False
            
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            logger.debug(f"File {file_path} not in conversation {conversation_id}, skipping refresh")
            return False
        
        full_path = os.path.join(base_dir, file_path)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                disk_content = f.read()
        except Exception as e:
            logger.warning(f"Could not read {full_path} from disk: {e}")
            return False
        
        disk_lines = disk_content.splitlines()
        current_hash = self._compute_hash(disk_lines)
        
        if current_hash != state.content_hash:
            logger.info(f"File {file_path} changed on disk since last check")
            changed_lines = self._compute_changes(state.original_content, state.current_content, disk_lines)
            state.current_content = disk_lines
            state.content_hash = current_hash
            
            for line_num in changed_lines:
                if line_num not in state.line_states:
                    state.line_states[line_num] = '+'
                elif state.line_states[line_num] != '+':
                    state.line_states[line_num] = '*'
            
            return True
        
        return False
    
    def refresh_all_files_from_disk(self, conversation_id: str, base_dir: str) -> Dict[str, bool]:
        """Refresh all files in a conversation from disk."""
        if conversation_id not in self.conversation_states:
            return {}
        
        results = {}
        for file_path in list(self.conversation_states[conversation_id].keys()):
            results[file_path] = self.refresh_file_from_disk(conversation_id, file_path, base_dir)
        
        changed_files = [f for f, changed in results.items() if changed]
        if changed_files:
            logger.info(f"Refreshed {len(changed_files)} files from disk: {changed_files}")
            self._save_state()
        
        return results
    
    def mark_context_submission(self, conversation_id: str) -> None:
        """Mark the current state as the last context submission point."""
        if conversation_id not in self.conversation_states:
            logger.warning(f"Cannot mark context submission for unknown conversation {conversation_id}")
            return
            
        logger.info(f"Marking context submission point for conversation {conversation_id}")
        for file_path, state in self.conversation_states[conversation_id].items():
            # Update last context submission content to current content
            state.last_context_submission_content = state.current_content.copy()
            logger.debug(f"Updated context submission baseline for {file_path}")
        
        self._save_state()  # Persist the updated baselines
        logger.info(f"Context submission marked for {len(self.conversation_states[conversation_id])} files")
    
    def has_changes_since_last_context_submission(self, conversation_id: str, file_path: str) -> bool:
        """Check if file has changed since last context submission."""
        if conversation_id not in self.conversation_states:
            logger.debug(f"No conversation state for {conversation_id}, treating as changed")
            return True
        
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            logger.debug(f"No file state for {file_path} in conversation {conversation_id}, treating as changed")
            return True
            
        has_changes = state.current_content != state.last_context_submission_content
        logger.debug(f"File {file_path} change check: current={len(state.current_content)} lines, "
                    f"baseline={len(state.last_context_submission_content)} lines, changed={has_changes}")
        return has_changes
    
    def format_layered_context_message(self, conversation_id: str) -> Tuple[str, str]:
        """Format context with both cumulative and recent changes."""
        # Get overall changes (existing functionality)
        overall_message, _ = self.format_context_message(conversation_id, include_recent=False)
        
        # Get recent changes since last submission
        recent_changes = self.get_changes_since_last_submission(conversation_id)
        recent_message = ""
        
        if recent_changes:
            recent_lines = ["RECENT: Files modified since last AI response:"]
            for file_path, changed_lines in recent_changes.items():
                recent_lines.append(f"  - {file_path}: {len(changed_lines)} lines changed since last response")
            recent_message = "\n".join(recent_lines)
        
        return overall_message, recent_message
    
    def get_file_changes(self, conversation_id: str) -> Dict[str, Dict[str, int]]:
        """Get summary of changes per file"""
        changes = {}
        for file_path, state in self.conversation_states.get(conversation_id, {}).items():
            added = sum(1 for state in state.line_states.values() if state == '+')
            modified = sum(1 for state in state.line_states.values() if state == '*')
            if added or modified:
                changes[file_path] = {
                    'added': added,
                    'modified': modified
                }
        return changes

    def get_recent_changes(self, conversation_id: str) -> Dict[str, Dict[str, int]]:
        """Get summary of changes since last query"""
        changes = {}
        for file_path, state in self.conversation_states.get(conversation_id, {}).items():
            # Compare current content with last seen content
            recent_changes = self._compute_changes(
                state.last_seen_content,
                state.current_content
            )
            if recent_changes:
                changes[file_path] = {
                    'changed_lines': len(recent_changes)
                }
        return changes

    def get_annotated_content(self, conversation_id: str, file_path: str) -> Tuple[List[str], bool]:
        """Get content with line state annotations"""
        state = self.conversation_states.get(conversation_id, {}).get(file_path)
        if not state:
            return [], False
            
        annotated_lines = []
        for i, line in enumerate(state.current_content, 1):
            line_state = state.line_states.get(i, ' ')
            annotated_lines.append(f"[{i:03d}{line_state}] {line}")
            
        return annotated_lines, True

    def has_changes_since_last_context_submission(self, conversation_id: str, file_path: str) -> bool:
        """Check if file has changed since last context submission."""
        if conversation_id not in self.conversation_states:
            logger.debug(f"No conversation state for {conversation_id}, treating as changed")
            return True
        
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            logger.debug(f"No file state for {file_path} in conversation {conversation_id}, treating as changed")
            return True
            
        has_changes = state.current_content != state.last_context_submission_content
        logger.debug(f"File {file_path} change check: current={len(state.current_content)} lines, "
                    f"baseline={len(state.last_context_submission_content)} lines, changed={has_changes}")
        return has_changes
    
    def refresh_file_from_disk(self, conversation_id: str, file_path: str, base_dir: str) -> bool:
        """
        Refresh a file's current_content from disk without resetting the baseline.
        
        This is critical for detecting when a user has NOT applied a suggested diff -
        the file on disk won't have changed, but we need to track the actual state.
        
        Returns:
            True if the file was refreshed and had changes, False otherwise
        """
        if conversation_id not in self.conversation_states:
            logger.warning(f"Cannot refresh file for unknown conversation {conversation_id}")
            return False
            
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            logger.debug(f"File {file_path} not in conversation {conversation_id}, skipping refresh")
            return False
        
        full_path = os.path.join(base_dir, file_path)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                disk_content = f.read()
        except Exception as e:
            logger.warning(f"Could not read {full_path} from disk: {e}")
            return False
        
        disk_lines = disk_content.splitlines()
        current_hash = self._compute_hash(disk_lines)
        
        if current_hash != state.content_hash:
            logger.info(f"File {file_path} changed on disk since last check")
            changed_lines = self._compute_changes(state.original_content, state.current_content, disk_lines)
            state.current_content = disk_lines
            state.content_hash = current_hash
            
            for line_num in changed_lines:
                if line_num not in state.line_states:
                    state.line_states[line_num] = '+'
                elif state.line_states[line_num] != '+':
                    state.line_states[line_num] = '*'
            
            return True
        
        return False
    
    def refresh_all_files_from_disk(self, conversation_id: str, base_dir: str) -> Dict[str, bool]:
        """Refresh all files in a conversation from disk."""
        if conversation_id not in self.conversation_states:
            return {}
        
        results = {}
        for file_path in list(self.conversation_states[conversation_id].keys()):
            results[file_path] = self.refresh_file_from_disk(conversation_id, file_path, base_dir)
        
        changed_files = [f for f, changed in results.items() if changed]
        if changed_files:
            logger.info(f"Refreshed {len(changed_files)} files from disk: {changed_files}")
            self._save_state()
        
        return results

    def update_file_state(self, conversation_id: str, file_path: str, new_content: str) -> Optional[Set[int]]:
        """Update file state and return set of changed line numbers"""
        if conversation_id not in self.conversation_states:
            return None
            
        state = self.conversation_states.get(conversation_id, {}).get(file_path)
        if not state:
            return None
            
        current_lines = new_content.splitlines()
        current_hash = self._compute_hash(current_lines)
        
        if current_hash == state.content_hash:
            logger.debug(f"No changes detected for {file_path}")
            return set()  # No changes detected
            
        # Update file state
        changed_lines = self._compute_changes(
            state.original_content,
            state.current_content,
            current_lines
        )

        state.current_content = current_lines.copy()  # Update current content
        state.content_hash = current_hash  # Update hash
        state.last_seen_content = current_lines.copy()  # Update last seen content
        
        # Update line states
        for line_num in changed_lines:
            if line_num not in state.line_states:
                state.line_states[line_num] = '+'  # New line
            elif state.line_states[line_num] != '+':
                state.line_states[line_num] = '*'  # Modified line
                
        return changed_lines

    def update_files(self, conversation_id: str, files: Dict[str, str]) -> Dict[str, Set[int]]:
        """Update multiple files and get all changes"""
        changes = {}
        for file_path, content in files.items():
            changed_lines = self.update_file_state(conversation_id, file_path, content)
            if changed_lines:
                changes[file_path] = changed_lines
        return changes

    def _compute_hash(self, content: List[str]) -> str:
        """Compute hash of file content"""
        content_str = '\n'.join(content)
        return hashlib.sha256(content_str.encode()).hexdigest()

    def _compute_changes(
        self,
        original_content: List[str],
        previous_content: List[str],
        current_content: List[str]
    ) -> Set[int]:
        """Compute changed line numbers"""
        changed_lines = set()
        
        # If original_content is empty, all current lines are new
        if not original_content:
            return set(range(1, len(current_content) + 1))
            
        # Compare previous and current content to find changes
        matcher = SequenceMatcher(None, previous_content, current_content)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'replace':
                # Modified lines
                changed_lines.update(range(j1 + 1, j2 + 1))
            elif tag == 'insert':
                # New lines
                changed_lines.update(range(j1 + 1, j2 + 1))
                
        return changed_lines

    def format_file_authority_message(self, conversation_id: str) -> str:
        """
        Generate a message emphasizing that the file content shown is authoritative.
        
        This helps the model understand that if it previously suggested changes that
        were NOT applied, the files shown here reflect the ACTUAL state, not the
        model's suggestions.
        """
        if conversation_id not in self.conversation_states:
            return ""
        
        # Check if there are any files with changes since the original
        files_with_changes = []
        for file_path, state in self.conversation_states[conversation_id].items():
            if state.line_states:  # Has some changes tracked
                files_with_changes.append(file_path)
        
        if not files_with_changes:
            return ""
        
        return """CRITICAL: FILE CONTENT AUTHORITY
========================================
⚠️  WARNING: VERIFY FILE STATE BEFORE GENERATING DIFFS ⚠️
========================================

The file content shown below is read directly from the filesystem and represents 
the ACTUAL current state of the files. If you previously suggested changes that 
were not applied, the file content here will NOT include those changes. 

REQUIRED VERIFICATION (perform silently before ANY diff):
1. Look at the file content below - check [NNN+], [NNN*], [NNN ] markers
2. Count the actual lines in the file content shown
3. Verify if your previous suggestions appear in this content
4. If they don't appear, they were NOT applied by the user
5. Base your diff on THIS content, not on what you suggested before

FAIL-SAFE RULE:
If you are about to generate a diff and have not looked at the file content
below in the last 10 seconds of your reasoning, STOP. Look at the file content
first, verify the current state, THEN generate the diff.

========================================

Files being tracked for changes: """ + ", ".join(files_with_changes) + "\n"

    def format_context_message(self, conversation_id: str, include_recent: bool = True, include_authority: bool = True) -> Tuple[str, str]:
        """Format context message about file changes for the prompt"""
        changes = self.get_file_changes(conversation_id)
        if not changes:
            return "", ""

        lines = [
            "The following files have been modified during this conversation.",
            "Changes are marked in the code with these indicators:",
            "  [NNN+] : New lines added",
            "  [NNN*] : Lines modified",
            "  [NNN ] : Unchanged lines",
            "\nModified files:"
        ]
        for file_path, counts in changes.items():
            added = counts['added']
            modified = counts['modified']
            lines.append(f"  - {file_path}:")
            lines.append(f"    • {added} new lines (marked with +)")
            lines.append(f"    • {modified} modified lines (marked with *)")
        
        # Add authority message if requested
        if include_authority:
            authority_msg = self.format_file_authority_message(conversation_id)
            if authority_msg:
                lines.insert(0, authority_msg)
        
        # Format recent changes if any
        recent_message = ""
        if include_recent:
            recent_changes = self.get_recent_changes(conversation_id)
            if recent_changes:
                recent_lines = [
                    "SYSTEM: The following files have been modified since your last query:",
                    ""
                ]
                for file_path, counts in recent_changes.items():
                    recent_lines.append(f"  - {file_path}: {counts['changed_lines']} lines changed")
                recent_message = "\n".join(recent_lines)

        return "\n".join(lines), recent_message

    def update_files_in_state(self, conversation_id: str, files: Dict[str, str]) -> None:
        """Ensure all files are in the state, initializing any new ones"""
        if conversation_id not in self.conversation_states:
            self.initialize_conversation(conversation_id, files)
            return
        
        for file_path, content in files.items():
            if file_path not in self.conversation_states[conversation_id]:
                lines = content.splitlines()
                self.conversation_states[conversation_id][file_path] = FileState(
                    path=file_path,
                    content_hash=self._compute_hash(lines),
                    line_states={},  # No changes initially
                    original_content=lines.copy(),
                    current_content=lines.copy(),
                    last_seen_content=lines.copy(),
                    last_context_submission_content=lines.copy()
                )
                
                # Mark all lines as new for newly added files
                for i in range(len(lines)):
                    self.conversation_states[conversation_id][file_path].line_states[i+1] = '+'
                
                logger.info(f"Added new file to state: {file_path} with {len(lines)} lines marked as new")
            else:
                # For existing files, check if we need to update the state
                existing_state = self.conversation_states[conversation_id][file_path]
                current_lines = content.splitlines()
                current_hash = self._compute_hash(current_lines)
                
                # If content has changed, update the state
                if current_hash != existing_state.content_hash:
                    changed_lines = self._compute_changes(
                        existing_state.original_content,
                        existing_state.current_content,
                        current_lines
                    )

                    # Update the current content
                    existing_state.current_content = current_lines
                    existing_state.content_hash = current_hash

                    logger.info(f"Updated existing file in state: {file_path} with {len(changed_lines)} changed lines")

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import hashlib
from difflib import SequenceMatcher
from app.utils.logging_utils import logger

@dataclass
class FileState:
    """Represents the state of a file at a specific point in time"""
    path: str
    content_hash: str
    line_states: Dict[int, str]  # line number -> state ('+' for added, '*' for modified)
    original_content: List[str]  # Original content when first seen
    current_content: List[str]   # Current content
    last_seen_content: List[str] # Content as of last query

class FileStateManager:
    """Manages file states and changes within a conversation context"""
    
    def __init__(self):
        self.conversation_states: Dict[str, Dict[str, FileState]] = {}
        
    def initialize_conversation(self, conversation_id: str, files: Dict[str, str]) -> None:
        """Initialize or reset file states for a conversation"""
        self.conversation_states[conversation_id] = {}
        logger.info(f"Initializing conversation {conversation_id} with {len(files)} files")
        
        for file_path, content in files.items():
            lines = content.splitlines()
            self.conversation_states[conversation_id][file_path] = FileState(
                path=file_path,
                content_hash=self._compute_hash(lines),
                line_states={},  # No changes initially
                original_content=lines.copy(),
                current_content=lines.copy(),
                last_seen_content=lines.copy()
            )
            logger.debug(f"Initialized state for {file_path} with {len(lines)} lines")
    
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

    def update_file_state(self, conversation_id: str, file_path: str, new_content: str) -> Optional[Set[int]]:
        """Update file state and return set of changed line numbers"""
        if conversation_id not in self.conversation_states:
            return None
            
        state = self.conversation_states[conversation_id].get(file_path)
        if not state:
            return None
            
        current_lines = new_content.splitlines()
        current_hash = self._compute_hash(current_lines)
        
        if current_hash == state.content_hash:
            logger.debug(f"No changes detected for {file_path}")
            return set()  # No changes
            
        # Update file state
        changed_lines = self._compute_changes(
            state.original_content,
            state.current_content,
            current_lines
        )
        
        state.current_content = current_lines
        state.content_hash = current_hash
        logger.info(f"Updated state for {file_path}: {len(changed_lines)} lines changed")

        state.last_seen_content = current_lines.copy()  # Update last seen content
        
        # Update line states
        for line_num in changed_lines:
            if line_num not in state.line_states:
                state.line_states[line_num] = '+'  # New line
            else:
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
        
        matcher = SequenceMatcher(None, previous_content, current_content)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ('replace', 'insert'):
                # Add all new/modified line numbers
                changed_lines.update(range(j1 + 1, j2 + 1))
                
        return changed_lines

    def format_context_message(self, conversation_id: str, include_recent: bool = True) -> Tuple[str, str]:
        """Format context message about file changes"""
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
                    last_seen_content=lines.copy()
                )
                logger.info(f"Added new file to state: {file_path}")

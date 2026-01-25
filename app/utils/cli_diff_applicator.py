"""
CLI diff applicator - Interactive diff application for terminal.
"""

import os
import re
from typing import List, Tuple, Optional
from app.utils.logging_utils import logger


class DiffBlock:
    """Represents a single diff block from markdown."""
    
    def __init__(self, content: str, start_pos: int, end_pos: int):
        self.content = content
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.file_path = self._extract_file_path()
    
    def _extract_file_path(self) -> Optional[str]:
        """Extract target file path from diff."""
        for line in self.content.split('\n'):
            if line.startswith('+++ b/'):
                return line[6:].strip()
            elif line.startswith('+++ '):
                path = line[4:].strip()
                # Remove 'b/' prefix if present
                if path.startswith('b/'):
                    path = path[2:]
                return path
        return None
    
    def get_preview(self, lines: int = 10) -> str:
        """Get a preview of the diff."""
        diff_lines = self.content.split('\n')
        preview_lines = diff_lines[:lines]
        if len(diff_lines) > lines:
            preview_lines.append(f'... ({len(diff_lines) - lines} more lines)')
        return '\n'.join(preview_lines)


class CLIDiffApplicator:
    """
    Interactive diff applicator for CLI mode.
    
    Extracts diffs from markdown responses and prompts user
    to apply them one at a time.
    """
    
    def __init__(self):
        self.applied_count = 0
        self.skipped_count = 0
        self.failed_count = 0
    
    def extract_diffs(self, markdown: str) -> List[DiffBlock]:
        """
        Extract all diff blocks from markdown response.
        
        Args:
            markdown: The markdown content to parse
        
        Returns:
            List of DiffBlock objects
        """
        # Pattern to match ```diff ... ``` blocks (code fences with diff language)
        # Match until ``` appears at the start of a line to handle nested code blocks
        # The \n``` ensures we match the closing fence on its own line
        pattern = r'```diff\n(.*?)\n```'
        matches = re.finditer(pattern, markdown, re.DOTALL)
        
        diffs = []
        for match in matches:
            diff_content = match.group(1).strip()
            diffs.append(DiffBlock(
                content=diff_content,
                start_pos=match.start(),
                end_pos=match.end()
            ))
        
        logger.debug(f"Extracted {len(diffs)} diff blocks from response")
        return diffs
    
    def _print_diff_preview(self, diff: DiffBlock, number: int, total: int):
        """Print a preview of the diff."""
        print(f"\n\033[36m{'─' * 60}\033[0m")
        print(f"\033[1;36mDiff {number}/{total}\033[0m")
        
        if diff.file_path:
            print(f"\033[90mFile: {diff.file_path}\033[0m")
        else:
            print(f"\033[33mWarning: Could not detect file path\033[0m")
        
        print(f"\033[36m{'─' * 60}\033[0m")
        
        # Show preview
        preview = diff.get_preview(15)
        print(preview)
        
        # Show truncation indicator if needed
        total_lines = len(diff.content.split('\n'))
        if total_lines > 15:
            print(f"\033[90m... ({total_lines - 15} more lines)\033[0m")
        
        print(f"\033[36m{'─' * 60}\033[0m")
    
    def _prompt_user_action(self) -> str:
        """
        Prompt user for action on current diff.
        
        Returns:
            User's choice: 'a' (apply), 's' (skip), 'v' (view), 'q' (quit)
        """
        while True:
            try:
                response = input(
                    "\n\033[1mAction:\033[0m "
                    "\033[32m[a]\033[0mpply / "
                    "\033[33m[s]\033[0mkip / "
                    "\033[36m[v]\033[0miew full / "
                    "\033[31m[q]\033[0muit? "
                ).strip().lower()
                
                if response in ['a', 's', 'v', 'q']:
                    return response
                else:
                    print("\033[90mInvalid choice. Please enter a, s, v, or q.\033[0m")
            except (EOFError, KeyboardInterrupt):
                print()
                return 'q'
    
    def _apply_diff(self, diff: DiffBlock) -> Tuple[bool, str]:
        """
        Apply a diff using the existing diff application utilities.
        
        Args:
            diff: The diff to apply
            
        Returns:
            (success, message) tuple
        """
        if not diff.file_path:
            return False, "Could not determine file path"
        
        try:
            # Get the full file path
            codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
            full_path = os.path.join(codebase_dir, diff.file_path)
            
            # Use the atomic application function
            from app.utils.diff_utils.application.git_diff import apply_diff_atomically
            
            result = apply_diff_atomically(full_path, diff.content)
            
            if result.get("status") == "success":
                details = result.get("details", {})
                if details.get("new_file"):
                    return True, f"Created new file: {diff.file_path}"
                elif details.get("already_applied"):
                    return True, f"Changes already applied to {diff.file_path}"
                else:
                    return True, f"Successfully applied to {diff.file_path}"
            else:
                error = result.get("details", {}).get("error", "Unknown error")
                return False, f"Failed to apply: {error}"
                
        except Exception as e:
            logger.error(f"Error applying diff: {e}")
            return False, f"Error: {str(e)}"
    
    def process_response(self, response: str) -> bool:
        """
        Process a response containing diffs and prompt user for actions.
        
        Args:
            response: The full response text
            
        Returns:
            True if processing completed normally, False if user quit
        """

        # Reset counters for this response
        self.applied_count = 0
        self.skipped_count = 0
        self.failed_count = 0

        # Extract all diffs
        diffs = self.extract_diffs(response)
        
        if not diffs:
            # No diffs to process
            return True
        
        print(f"\n\033[1mFound {len(diffs)} diff(s) in response\033[0m")
        
        # Process each diff one at a time
        for i, diff in enumerate(diffs, 1):
            self._print_diff_preview(diff, i, len(diffs))
            
            # Prompt for action
            while True:
                action = self._prompt_user_action()
                
                if action == 'q':
                    print(f"\n\033[90mStopping. Processed {i-1}/{len(diffs)} diffs.\033[0m")
                    self._print_summary()
                    return False
                
                elif action == 's':
                    print(f"\033[33m⊘ Skipped\033[0m")
                    self.skipped_count += 1
                    break
                
                elif action == 'v':
                    # Show full diff
                    print(f"\n\033[36m{'─' * 60}\033[0m")
                    print(diff.content)
                    print(f"\033[36m{'─' * 60}\033[0m")
                    # Continue loop to prompt again
                    continue
                
                elif action == 'a':
                    # Apply the diff
                    print(f"\033[90mApplying...\033[0m")
                    success, message = self._apply_diff(diff)
                    
                    if success:
                        print(f"\033[32m✓ {message}\033[0m")
                        self.applied_count += 1
                    else:
                        print(f"\033[31m✗ {message}\033[0m")
                        self.failed_count += 1
                    break
        
        # Print final summary
        self._print_summary()
        return True
    
    def _print_summary(self):
        """Print summary of diff processing."""
        total = self.applied_count + self.skipped_count + self.failed_count
        if total == 0:
            return
        
        print(f"\n\033[1mDiff Summary:\033[0m")
        if self.applied_count > 0:
            print(f"  \033[32m✓ {self.applied_count} applied\033[0m")
        if self.skipped_count > 0:
            print(f"  \033[33m⊘ {self.skipped_count} skipped\033[0m")
        if self.failed_count > 0:
            print(f"  \033[31m✗ {self.failed_count} failed\033[0m")
        print()

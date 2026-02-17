# CRITICAL: Set chat mode before any imports
# CLI entry point
import os
os.environ["ZIYA_MODE"] = "chat"
os.environ.setdefault("ZIYA_LOG_LEVEL", "WARNING")
import logging

"""
Ziya CLI - Clean command-line interface.

Usage:
    ziya chat [FILES...]           Interactive chat with optional file context
    ziya ask "question" [FILES...] Single question, get answer, exit
    ziya review [FILES...]         Review code (alias for ask with review prompt)
    ziya explain [FILES...]        Explain code (alias for ask with explain prompt)
    
Examples:
    ziya chat                      Start interactive chat
    ziya chat src/                 Chat with src/ directory in context
    ziya ask "what does this do?" main.py
    ziya explain utils.py
    git diff | ziya ask "review this"
    ziya review --staged           Review staged git changes
"""

import json
from datetime import datetime
import os
import sys
from typing import Optional
import asyncio
try:
    # CRITICAL: Force reconfigure all existing loggers to respect chat mode
    # This handles case where modules were imported before ZIYA_MODE was set
    for logger_name in list(logging.Logger.manager.loggerDict.keys()):
        if logger_name.startswith('app.') or logger_name == 'app':
            try:
                existing_logger = logging.getLogger(logger_name)
                existing_logger.setLevel(logging.WARNING)
                for handler in existing_logger.handlers:
                    handler.setLevel(logging.WARNING)
            except Exception:
                pass  # Skip loggers that can't be configured
except Exception as e:
    print(f"Warning: Could not configure logging: {e}", file=sys.stderr)
import re
import hashlib
import argparse
import time
import traceback
from pathlib import Path
import sys
from app.utils.logging_utils import logger
from typing import Optional, List, Tuple 
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import PathCompleter, WordCompleter, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_selection
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.containers import WindowAlign
from prompt_toolkit.widgets import RadioList
from prompt_toolkit.widgets import Label, Button, TextArea
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText


# ============================================================================
# Session history management
# ============================================================================

def get_session_dir() -> Path:
    """Get the directory for session storage."""
    session_dir = Path.home() / '.ziya' / 'sessions'
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def save_session(cli: 'CLI') -> str:
    """Save current session and return session ID."""
    session_dir = get_session_dir()
    session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_file = session_dir / f"{session_id}.json"
    
    session_data = {
        'id': session_id,
        'timestamp': datetime.now().isoformat(),
        'files': cli.files,
        'history': cli.history
    }
    
    with open(session_file, 'w') as f:
        json.dump(session_data, f, indent=2)
    
    # Cleanup old sessions (keep last 10)
    cleanup_old_sessions()
    
    return session_id


def load_session(session_id: str) -> dict:
    """Load a session by ID."""
    session_dir = get_session_dir()
    session_file = session_dir / f"{session_id}.json"
    
    if not session_file.exists():
        raise FileNotFoundError(f"Session {session_id} not found")
    
    with open(session_file, 'r') as f:
        return json.load(f)


def cleanup_old_sessions(keep_count: int = 10):
    """Keep only the most recent sessions."""
    session_dir = get_session_dir()
    sessions = sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    
    for old_session in sessions[keep_count:]:
        old_session.unlink()


def select_session() -> Optional[str]:
    """Interactive session selector."""
    session_dir = get_session_dir()
    sessions = sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    
    if not sessions:
        print("No previous sessions found.")
        return None
    
    # Load session metadata
    session_list = []
    for session_file in sessions[:10]:  # Show last 10
        try:
            with open(session_file, 'r') as f:
                data = json.load(f)
                session_list.append({
                    'id': data['id'],
                    'timestamp': data['timestamp'],
                    'file_count': len(data.get('files', [])),
                    'message_count': len(data.get('history', []))
                })
        except Exception:
            continue
    
    if not session_list:
        print("No valid sessions found.")
        return None
    
    # Create selection prompt
    print("\n\033[1;36mAvailable Sessions:\033[0m")
    for idx, session in enumerate(session_list, 1):
        timestamp = datetime.fromisoformat(session['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        print(f"{idx}. {timestamp} - {session['message_count']} messages, {session['file_count']} files")
    
    print("\n0. Cancel (start new session)")
    
    while True:
        choice = input("\nSelect session number: ").strip()
        if choice == '0':
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(session_list):
                return session_list[idx]['id']
        except ValueError:
            pass
        print("Invalid selection. Please try again.")


def print_chat_startup_info(args):
    """Pretty print essential startup information for chat mode."""
    root = getattr(args, 'root', None) or os.getcwd()
    profile = getattr(args, 'profile', None) or os.environ.get('AWS_PROFILE', 'default')
    model = getattr(args, 'model', None) or os.environ.get('ZIYA_MODEL', 'sonnet4.5')
    
    # Only show essential info
    print(f"Ziya CLI • profile: {profile} • model: {model}")
    print(f"Root: {root}")
    
    # Show MCP server count if available
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_mgr = get_mcp_manager()
        if mcp_mgr and mcp_mgr.is_initialized:
            tool_count = len(mcp_mgr._tool_cache) if hasattr(mcp_mgr, '_tool_cache') else 0
            server_count = len(mcp_mgr.clients) if hasattr(mcp_mgr, 'clients') else 0
            print(f"MCP: {server_count} servers, {tool_count} tools")
    except Exception:
        pass  # Silently skip if MCP not available
    
    print()  # Blank line before prompt


def setup_env(args):
    """Minimal environment setup for CLI mode."""
    import sys
    
    # Helper to extract flag value from sys.argv
    def get_flag_from_argv(flag_name):
        """Extract flag value from sys.argv (handles --flag value format)."""
        try:
            if f'--{flag_name}' in sys.argv:
                idx = sys.argv.index(f'--{flag_name}')
                if idx + 1 < len(sys.argv):
                    return sys.argv[idx + 1]
        except (ValueError, IndexError):
            pass
        return None
    
    # Handle debug flag first, before any other setup
    if getattr(args, 'debug', False):
        os.environ["ZIYA_LOG_LEVEL"] = "DEBUG"
        # CRITICAL: Also disable chat mode suppression for debug
        os.environ["ZIYA_MODE"] = "debug"
        print("🐛 Debug logging enabled", file=sys.stderr)
    
    # Force reconfigure all existing loggers to respect chat mode
    # This handles case where modules were imported before ZIYA_MODE was set
    try:
        # Get the target log level from environment (respects --debug flag)
        target_level = getattr(logging, os.environ.get('ZIYA_LOG_LEVEL', 'WARNING').upper())
        for logger_name in list(logging.Logger.manager.loggerDict.keys()):
            if logger_name.startswith('app.') or logger_name == 'app':
                try:
                    existing_logger = logging.getLogger(logger_name)
                    existing_logger.setLevel(target_level)
                    for handler in existing_logger.handlers:
                        handler.setLevel(target_level)
                except Exception:
                    pass  # Skip loggers that can't be configured
    except Exception as e:
        print(f"Warning: Could not reconfigure logging in setup_env: {e}", file=sys.stderr)

    
    # Set root directory
    root = getattr(args, 'root', None) or os.getcwd()
    os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", root)
    
    # Model settings
    if getattr(args, 'model', None):
        os.environ["ZIYA_MODEL"] = args.model
    
    # AWS settings - set BOTH env vars for compatibility
    profile = getattr(args, 'profile', None) or get_flag_from_argv('profile')
    if profile:
        os.environ["ZIYA_AWS_PROFILE"] = profile
        os.environ["AWS_PROFILE"] = profile
    # AWS region setting
    if getattr(args, 'region', None):
        os.environ["AWS_REGION"] = args.region
def resolve_files(paths: List[str], root: str) -> List[str]:
    """Resolve file/directory paths to list of files."""
    import glob
    
    files = []
    for path in paths:
        full_path = path if os.path.isabs(path) else os.path.join(root, path)
        
        if os.path.isfile(full_path):
            files.append(os.path.relpath(full_path, root))
        elif os.path.isdir(full_path):
            # Add all supported files in directory
            for ext in ['py', 'js', 'ts', 'tsx', 'jsx', 'java', 'go', 'rs', 'rb', 'c', 'cpp', 'h']:
                pattern = os.path.join(full_path, '**', f'*.{ext}')
                for f in glob.glob(pattern, recursive=True):
                    rel = os.path.relpath(f, root)
                    # Skip common excludes
                    if not any(x in rel for x in ['node_modules', '__pycache__', '.git', 'venv', '.venv']):
                        files.append(rel)
        elif '*' in path or '?' in path:
            # Glob pattern
            for f in glob.glob(full_path, recursive=True):
                if os.path.isfile(f):
                    files.append(os.path.relpath(f, root))
    
    return sorted(set(files))


def read_stdin_if_available() -> Optional[str]:
    """Read from stdin if data is being piped in."""
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


def get_git_staged_diff() -> Optional[str]:
    """Get diff of staged changes."""
    import subprocess
    try:
        result = subprocess.run(['git', 'diff', '--cached'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass
    return None


def get_git_diff() -> Optional[str]:
    """Get diff of unstaged changes."""
    import subprocess
    try:
        result = subprocess.run(['git', 'diff'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass
    return None


class CLI:
    """Lightweight CLI client."""
    
    def __init__(self, files: List[str] = None):
        self.files = files or []
        self.history = []
        self._model = None
        self._init_error = None
        self._active_task = None  # Track active streaming task for cancellation
        self._cancellation_requested = False
        self._diff_applicator = None  # Lazy-load diff applicator
        self._last_ctrl_c_time = 0  # Track last Ctrl+C press for double-tap exit
        self._last_input_time = 0  # Track last input time for paste detection
        self._setup_prompt_session()
    
    @property
    def model(self):
        """Lazy-load model on first use."""
        if self._model is None:
            self._model = self._initialize_model()
        return self._model
    
    @property
    def diff_applicator(self):
        """Lazy-load diff applicator on first use."""
        if self._diff_applicator is None:
            from app.utils.cli_diff_applicator import CLIDiffApplicator
            self._diff_applicator = CLIDiffApplicator()
        return self._diff_applicator
    
    def _initialize_model(self):
        """Initialize the model with proper error handling."""
        try:
            # Check credentials first for Bedrock
            endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
            if endpoint == "bedrock":
                from app.utils.aws_utils import check_aws_credentials
                valid, message = check_aws_credentials()
                if not valid:
                    self._init_error = message
                    return None
            
            from app.agents.models import ModelManager
            model_instance = ModelManager.initialize_model()
            
            if model_instance is None:
                self._init_error = "Model initialization failed. Check your credentials."
                return None
            
            return model_instance
            
        except Exception as e:
            self._init_error = str(e)
            return None
    
    def _setup_prompt_session(self):
        """Set up prompt_toolkit session with history and completions."""
        # Custom completer for commands and file paths
        class SmartCompleter(Completer):
            def __init__(self):
                self.command_completer = WordCompleter([
                    '/add', '/a',
                    '/rm', '/remove',
                    '/files', '/ls', '/f',
                    '/clear', '/c',
                    '/model', '/m',
                    '/quit', '/q', '/exit',
                    '/help', '/h'
                ], ignore_case=True, sentence=True, match_middle=True)
                
                self.path_completer = PathCompleter(
                    only_directories=False,
                    expanduser=True
                )
                
                # Model name completer
                try:
                    from app.config.models_config import MODEL_CONFIGS
                    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                    model_names = list(MODEL_CONFIGS.get(endpoint, {}).keys())
                    self.model_completer = WordCompleter(model_names, ignore_case=True)
                except Exception:
                    self.model_completer = None
            
            def get_completions(self, document: Document, complete_event):
                text = document.text_before_cursor
                stripped = text.lstrip()
                
                # Show command completions when typing a command
                if stripped.startswith('/'):
                    if ' ' in stripped:
                        # We're past the command, show path completions for the argument part
                        # Check if this is a /model command
                        command = stripped.split()[0].lower()
                        if command in ['/model', '/m'] and self.model_completer:
                            # Show model name completions
                            space_idx = stripped.index(' ')
                            model_part = stripped[space_idx + 1:]
                            
                            model_doc = Document(
                                text=model_part,
                                cursor_position=len(model_part)
                            )
                            yield from self.model_completer.get_completions(model_doc, complete_event)
                            return
                        
                        # Find where the command ends and the path argument begins
                        space_idx = stripped.index(' ')
                        path_part = stripped[space_idx + 1:]
                        
                        # Create a new document with just the path part for completion
                        path_doc = Document(
                            text=path_part,
                            cursor_position=len(path_part)
                        )
                        yield from self.path_completer.get_completions(path_doc, complete_event)
                        return
                    else:
                        # Still typing the command - show command completions
                        command_part = stripped
                        cmd_doc = Document(text=command_part, cursor_position=len(command_part))
                        yield from self.command_completer.get_completions(cmd_doc, complete_event)
                        return
                
                # Otherwise show path completions
                yield from self.path_completer.get_completions(document, complete_event)
        
        # History file in user's home directory
        history_file = Path.home() / '.ziya' / 'history'
        history_file.parent.mkdir(parents=True, exist_ok=True)

        # Key bindings for ^C handling  
        bindings = KeyBindings()
        
        @bindings.add('c-c')
        def _(event):
            """Handle Ctrl+C - double tap to exit, or cancel/clear."""
            current_time = time.time()
            time_since_last = current_time - self._last_ctrl_c_time
            
            # Double tap within 1 second = exit
            if time_since_last < 1.0:
                event.app.exit(result='__exit__')
                return
            
            if self._active_task:
                # Cancel the active streaming task
                self._cancellation_requested = True
                print("\n\033[33m^C - Cancelling operation...\033[0m")
                event.app.exit(result='')
            elif event.app.current_buffer.text:
                # Clear current input
                event.app.current_buffer.reset()
            else:
                # Empty input - show exit hint
                print("\033[90m(Press ^D or type /quit to exit)\033[0m")
            
            # Update last Ctrl+C time
            self._last_ctrl_c_time = current_time
        
        @bindings.add('enter', filter=~has_selection)
        def _(event):
            """Handle Enter - submit input."""
            buffer = event.current_buffer
            buffer.validate_and_handle()
        
        self.session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=SmartCompleter(),
            complete_while_typing=True,
            key_bindings=bindings,
            multiline=True,
            prompt_continuation=lambda width, line_number, is_soft_wrap: '  '
        )
    
    def _build_messages(self, question: str):
        """Build messages for the model."""
        from app.server import build_messages_for_streaming
        return build_messages_for_streaming(
            question=question,
            chat_history=self.history,
            files=self.files,
            conversation_id=f"cli_{os.getpid()}",
            use_langchain_format=True
        )
    
    async def ask(self, question: str, stream: bool = True) -> str:
        """Send a question and get response."""
        if self.model is None:
            error_msg = self._init_error or "Model not available"
            print(f"\n\033[31mError: {error_msg}\033[0m", file=sys.stderr)
            self._print_auth_help()
            return ""
        
        # Reset cancellation flag
        self._cancellation_requested = False
        
        # Track partial response for cancellation scenarios
        partial_response = ""
        
        try:
            response = await self._run_with_tools_and_validate(question, stream)
            partial_response = response
        except Exception as e:
            error_str = str(e)
            
            # Extract traceback info for better error reporting
            tb = traceback.extract_tb(sys.exc_info()[2])
            if tb:
                last_frame = tb[-1]
                location = f"{last_frame.filename}:{last_frame.lineno}"
                print(f"\n\033[31mError in {location}: {error_str}\033[0m", file=sys.stderr)
                if os.environ.get('ZIYA_LOG_LEVEL') == 'DEBUG':
                    traceback.print_exc(file=sys.stderr)
            else:
                print(f"\033[31mError: {error_str}\033[0m", file=sys.stderr)
            
            # Check for specific error types
            if 'ThrottlingException' in error_str or 'Too many tokens' in error_str:
                print("\033[33mRate limit hit. Please wait a moment before trying again.\033[0m", file=sys.stderr)
            elif 'ExpiredToken' in error_str:
                print("\033[33mCredentials expired. Please refresh: aws sso login --profile <profile>\033[0m", file=sys.stderr)
            elif isinstance(e, asyncio.CancelledError):
                # Graceful cancellation
                print("\n\033[33mOperation cancelled.\033[0m")
                # Use partial response accumulated before cancellation
                response = partial_response
                # Update history with partial response
                self.history.append({'type': 'human', 'content': question})
                if response:
                    self.history.append({'type': 'ai', 'content': response})
                return response
            
            return ""
        
        # Update history
        self.history.append({'type': 'human', 'content': question})
        if response:  # Empty string is falsy, so this works correctly
            # Diff processing now happens in _run_with_tools_and_validate
            pass
            
            self.history.append({'type': 'ai', 'content': response})
        
        return response
    
    async def _run_with_tools_and_validate(self, question: str, stream: bool = True) -> str:
        """Run model with tool execution loop."""
        """Run model with tools and validation-feedback loop for diffs."""
        from app.utils.diff_validation_hook import DiffValidationHook
        from langchain_core.messages import HumanMessage
        from langchain_core.messages import AIMessage
        from app.agents.agent import file_state_manager
        
        # Use consistent conversation ID for this CLI session
        conversation_id = f"cli_{os.getpid()}"

        validation_hook = DiffValidationHook(
            file_state_manager=file_state_manager,
            conversation_id=conversation_id,
            current_context=self.files,
            auto_regenerate=True
        )
        
        # Build initial messages
        messages = self._build_messages(question)
        
        # Validation loop - give model chance to fix bad diffs
        max_attempts = 3
        for attempt in range(max_attempts):
            # Reset cancellation flag for each attempt
            self._cancellation_requested = False
            
            response = await self._run_with_tools_from_messages(messages, stream)
            
            # If no diffs, we're done
            if '```diff' not in response:
                # Clean up validation hook
                validation_hook = None
                return response
            
            # Validate diffs using FULL apply pipeline (dry-run)
            validation_feedback = validation_hook.validate_and_enhance(
                content=response,
                model_messages=messages,
            )
            
            # If validation passed, process diffs interactively
            if not validation_feedback:
                if attempt > 0:
                    print("\n\033[32m✓ Diff validation passed\033[0m")
                
                # Sync auto-added context files back to CLI's file list
                for f in validation_hook.added_files:
                    if f not in self.files:
                        self.files.append(f)

                # Process diffs in a loop to handle continuations
                full_response = response
                while True:
                    # Show diffs to user for interactive application
                    try:
                        completed_normally = self.diff_applicator.process_response(full_response)
                    except Exception as e:
                        if os.environ.get('ZIYA_LOG_LEVEL') == 'DEBUG':
                            print(f"\n\033[33mNote: Could not process diffs: {e}\033[0m", file=sys.stderr)
                        break
                    
                    # After processing diffs, check if model wants to continue
                    if completed_normally:
                        # Add the assistant's response to messages so the model
                        # retains context of what it already said
                        messages.append(AIMessage(content=response))
                        summary = self._build_diff_summary()
                        continuation_message = (
                            f"{summary}\n\n"
                            "If there are more changes needed or additional steps to complete, "
                            "please continue. Otherwise, confirm that all necessary changes have been provided."
                        )
                        # Continue conversation with the model
                        continue_response = await self._continue_conversation(continuation_message, messages)
                        
                        # If continuation contains more diffs, process those too
                        if '```diff' in continue_response:
                            full_response = continue_response
                            continue
                        
                        # Return combined response
                        return response + "\n\n" + continue_response
                    break
                
                # Clean up validation hook
                validation_hook = None
                return response
            
            # Validation failed - regenerate if we have attempts left
            if attempt < max_attempts - 1:
                # Explain what's happening and why
                print(f"\n\033[33m⚠ Diff couldn't be applied cleanly (hunks don't match current file content)\033[0m")
                failed_files = [d['file_path'] for d in validation_hook.failed_diff_details]
                passed_files = validation_hook.successful_diffs
                if passed_files:
                    print(f"\033[32m  ✓ Passed: {', '.join(passed_files)}\033[0m")
                print(f"\033[31m  ✗ Failed: {', '.join(failed_files)}\033[0m")
                print(f"\033[90mRegenerating with file context... (attempt {attempt + 2}/{max_attempts})\033[0m\n")
                
                # After second failure, suggest breaking up the diff
                if attempt == 1:  # Second attempt - strong guidance to break it down
                    validation_feedback += (
                        "\n\n⚠️ CRITICAL: Diff validation failed again.\n\n"
                        "REQUIRED STRATEGY for next attempt:\n"
                        "1. Break this change into a SERIES of smaller, independent diffs\n"
                        "2. Each diff should:\n"
                        "   - Target 10-20 lines max\n"
                        "   - Include UNIQUE context (function names, class declarations, distinctive comments)\n"
                        "   - Be independently applicable (no dependencies between diffs)\n"
                        "3. Present ONE diff at a time, wait for it to be applied, then continue\n"
                        "4. The file content is now in your context - verify line numbers and context match exactly\n"
                    )
                elif attempt == 2:  # Third attempt - last chance with tool verification
                    validation_feedback += (
                        "\n\n🛑 FINAL VALIDATION ATTEMPT\n\n"
                        "Multiple diffs have failed. Before generating another diff:\n\n"
                        "1. **VERIFY FILE STATE** - Use tools to check:\n"
                        "   - Use grep/search tools to find the exact lines you want to modify\n"
                        "   - Verify the function/class structure matches your understanding\n"
                        "   - Check line numbers and surrounding context\n"
                        "2. **IF** verification shows discrepancies, explain what you found\n"
                        "3. **ONLY THEN** generate ONE minimal diff with:\n"
                        "   - Complete function/class signature as context\n"
                        "   - Exact indentation and whitespace from verified content\n"
                        "   - Unique identifiers (function names, variable names) as anchors\n\n"
                        "DO NOT guess or rely solely on context - actively verify with tools first.\n"
                    )
                
                # Append feedback and rebuild messages for retry
                # DON'T rebuild from scratch - just append feedback to existing messages
                messages.append(HumanMessage(content=validation_feedback))
            else:
                # Final attempt failed
                print(f"\n\033[31m✗ Diff validation failed after {max_attempts} attempts\033[0m")
                print(f"\033[90mThe model couldn't generate a valid diff. Showing response anyway.\033[0m")
                print(f"\033[90mReview carefully before applying any changes.\033[0m\n")
        
        # Clean up validation hook after all attempts
        # Sync any auto-added files even on failure path
        if validation_hook and validation_hook.added_files:
            for f in validation_hook.added_files:
                if f not in self.files:
                    self.files.append(f)
        validation_hook = None
        
        return response
    
    def _build_diff_summary(self) -> str:
        """Build a summary message of diff processing results."""
        applicator = self.diff_applicator
        # CLIDiffApplicator tracks counts internally during process_response
        # Check if it has these attributes, otherwise return generic message
        if hasattr(applicator, 'applied_count'):
            total = applicator.applied_count + applicator.skipped_count + applicator.failed_count
            parts = []
            if applicator.applied_count > 0:
                parts.append(f"{applicator.applied_count} applied")
            if applicator.skipped_count > 0:
                parts.append(f"{applicator.skipped_count} skipped")
            if applicator.failed_count > 0:
                parts.append(f"{applicator.failed_count} failed")
            return f"Diff processing complete: {', '.join(parts)}."
        return "Diff processing complete."
    
    async def _continue_conversation(self, message: str, messages: list) -> str:
        """Send a continuation message and get model's response."""
        from langchain_core.messages import HumanMessage
        
        # Add continuation message to history
        messages.append(HumanMessage(content=message))
        
        # Get model response using existing method
        return await self._run_with_tools_from_messages(messages, stream=True)
    
    async def _run_with_tools(self, question: str, stream: bool = True) -> str:
        """Run model with tool execution loop."""
        from app.streaming_tool_executor import StreamingToolExecutor
        from app.mcp.manager import get_mcp_manager
        from app.agents.models import ModelManager
        
        messages = self._build_messages(question)
        
        # Get MCP manager and tools
        mcp_manager = get_mcp_manager()
        if not mcp_manager or not mcp_manager.is_initialized:
            # No tools, just invoke normally
            return await self._simple_invoke(messages, stream)
        
        # Get tools for StreamingToolExecutor
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        tools = create_secure_mcp_tools()
        
        if not tools:
            return await self._simple_invoke(messages, stream)
        
        # Get AWS config
        state = ModelManager.get_state()
        aws_profile = state.get('aws_profile')
        aws_region = state.get('aws_region', 'us-west-2')
        
        # Create StreamingToolExecutor
        executor = StreamingToolExecutor(profile_name=aws_profile, region=aws_region)
        
        # Convert LangChain messages to OpenAI format
        openai_messages = self._convert_to_openai_format(messages)
        
        # Create streaming task
        full_response = ""
        
        async def stream_task():
            """Streaming task that can be cancelled."""
            async for chunk in executor.stream_with_tools(openai_messages, tools):
                # Check for cancellation
                if self._cancellation_requested:
                    raise asyncio.CancelledError("User cancelled operation")
                
                yield chunk
        
        # Store task for cancellation
        task = asyncio.create_task(self._stream_handler(stream_task(), stream))
        self._active_task = task
        
        try:
            full_response = await task
        finally:
            self._active_task = None
        
        return full_response
    
    async def _run_with_tools_from_messages(self, messages, stream: bool = True) -> str:
        """Run model with tools from existing message list (for retries)."""
        from app.streaming_tool_executor import StreamingToolExecutor
        from app.mcp.manager import get_mcp_manager
        from app.agents.models import ModelManager
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        
        mcp_manager = get_mcp_manager()
        if not mcp_manager or not mcp_manager.is_initialized:
            return await self._simple_invoke(messages, stream)
        
        tools = create_secure_mcp_tools()
        if not tools:
            return await self._simple_invoke(messages, stream)
        
        state = ModelManager.get_state()
        executor = StreamingToolExecutor(
            profile_name=state.get('aws_profile'),
            region=state.get('aws_region', 'us-west-2')
        )
        
        openai_messages = self._convert_to_openai_format(messages)
        
        async def stream_task():
            async for chunk in executor.stream_with_tools(openai_messages, tools):
                if self._cancellation_requested:
                    raise asyncio.CancelledError("User cancelled operation")
                yield chunk
        
        task = asyncio.create_task(self._stream_handler(stream_task(), stream))
        self._active_task = task
        
        try:
            full_response = await task
            return full_response
        except asyncio.CancelledError:
            # Task was cancelled, but we may have accumulated partial response
            # The _stream_handler should have returned what it collected so far
            raise  # Re-raise to be handled by ask()
        finally:
            self._active_task = None
    
    def _parse_markdown_state(self, content: str) -> dict:
        """Parse markdown to detect unclosed code blocks."""
        lines = content.split('\n')
        code_block_stack = []
        
        for line in lines:
            trimmed = line.lstrip()
            fence_match = re.match(r'^(`{3,}|~{3,})(\w*)', trimmed)
            
            if fence_match:
                fence_chars = fence_match.group(1)
                language = fence_match.group(2) or ''
                fence_type = fence_chars[0]
                
                # Check if closing
                if (code_block_stack and 
                    code_block_stack[-1]['type'] == fence_type and
                    len(fence_chars) >= 3):
                    code_block_stack.pop()
                elif len(fence_chars) >= 3:
                    code_block_stack.append({'type': fence_type, 'language': language})
        
        return {
            'in_code_block': len(code_block_stack) > 0,
            'fence_type': code_block_stack[-1]['type'] if code_block_stack else None,
            'fence_language': code_block_stack[-1]['language'] if code_block_stack else None
        }
    
    def _handle_rewind_marker(self, content: str) -> tuple[str, str]:
        """
        Handle rewind markers in streamed content.
        Truncates content to everything before the last marker.
        Returns (rewound_content, marker_stripped_chunk).
        """
        if '<!-- REWIND_MARKER:' not in content:
            return content, content
        
        # Find the last rewind marker in the content
        lines = content.split('\n')
        marker_line_idx = None
        for i in range(len(lines) - 1, -1, -1):
            if '<!-- REWIND_MARKER:' in lines[i]:
                marker_line_idx = i
                break
        
        if marker_line_idx is not None:
            before_rewind = '\n'.join(lines[:marker_line_idx])
            
            # Check if we're in a code block and close it properly
            markdown_state = self._parse_markdown_state(before_rewind)
            if markdown_state['in_code_block']:
                fence_to_use = markdown_state['fence_type'] or '`'
                before_rewind += '\n' + fence_to_use * 3 + '\n'
            
            # Strip all markers from content for display
            stripped = re.sub(r'<!-- REWIND_MARKER: [^\s]+(?: -->|(?:\|FENCE:[`~]\w*)? -->)(?:</span>)?', '', content)
            return before_rewind, stripped
        
        return content, content
    
    async def _stream_handler(self, stream_generator, stream: bool) -> str:
        """Handle streaming with cancellation support."""
        full_response = ""
        md_renderer = None
        if stream:
            from app.utils.terminal_markdown import StreamingMarkdownRenderer
            md_renderer = StreamingMarkdownRenderer()
        
        try:
            async for chunk in stream_generator:
                chunk_type = chunk.get('type')
            
                if chunk_type == 'text':
                    content = chunk.get('content', '')
                
                    # Add original content (with markers) to full_response for proper rewind processing
                    full_response += content
                
                    # For display: filter out rewind markers and continuation messages
                    display_content = re.sub(r'<!-- REWIND_MARKER: [^\s]+(?: -->|(?:\|FENCE:[`~]\w*)? -->)', '', content)
                    display_content = display_content.replace('', '')
                
                    # Render through markdown renderer or print raw
                    if display_content.strip():  # Only display if non-empty after filtering
                        if md_renderer:
                            md_renderer.feed(display_content)
                        elif stream:
                            print(display_content, end='', flush=True)
                
                    # Handle rewind markers
                    # This processes markers in the accumulated full_response and rewinds if needed
                    rewound, _ = self._handle_rewind_marker(full_response)
                    if rewound != full_response:
                        full_response = rewound
                        # Reset markdown renderer since we truncated content
                        if md_renderer:
                            from app.utils.terminal_markdown import StreamingMarkdownRenderer
                            md_renderer = StreamingMarkdownRenderer()
            
                elif chunk_type == 'tool_execution':
                    tool_name = chunk.get('tool_name', 'unknown')
                    print(f"\n\033[90m⚡ {tool_name}\033[0m", flush=True)
            
                elif chunk_type == 'tool_start':
                    tool_name = chunk.get('tool_name', 'unknown')
                    print(f"\n\033[36m⚙ Executing {tool_name}...\033[0m", flush=True)
            
                elif chunk_type == 'tool_display':
                    # Show tool result with formatting
                    tool_name = chunk.get('tool_name', 'unknown')
                    result = chunk.get('result', '')
                    args = chunk.get('args', {})
                
                    # Build header with any available metadata
                    header_parts = [tool_name]
                    metadata = []
                
                    # Extract common metadata patterns from args
                    if 'thoughtNumber' in args and 'totalThoughts' in args:
                        # Progress indicator (e.g., thought 3/5)
                        metadata.append(f"{args['thoughtNumber']}/{args['totalThoughts']}")
                
                    if args.get('isRevision') and 'revisesThought' in args:
                        # Revision indicator
                        metadata.append(f"revises #{args['revisesThought']}")
                    elif 'branchId' in args:
                        # Branch indicator
                        metadata.append(f"branch: {args['branchId']}")
                
                    if 'branchFromThought' in args:
                        # Branch origin
                        metadata.append(f"from #{args['branchFromThought']}")
                
                    # Add command if available (for shell tools)
                    if 'command' in args and not result.startswith('$ '):
                        metadata.append(f"$ {args['command']}")
                
                    # For search tools, show the search query
                    if 'WorkspaceSearch' in tool_name or 'CodeSearch' in tool_name:
                        # Extract query from args (handle nested tool_input)
                        search_args = args.get('tool_input', args)
                        if isinstance(search_args, str):
                            import json
                            try:
                                search_args = json.loads(search_args)
                            except: pass
                        query = search_args.get('searchQuery') or search_args.get('query', '')
                        if query:
                            metadata.append(f'query: "{query}"')
                
                    # Build final header
                    if metadata:
                        header = f"{header_parts[0]} ({', '.join(metadata)})"
                    else:
                        header = header_parts[0]
                
                    # Print header
                    print(f"\n\033[36m┌─ {header}\033[0m", flush=True)
                
                    # Print the thought content if it's in args (for sequential thinking)
                    if 'thought' in args:
                        thought_text = args['thought']
                        if thought_text:
                            for line in thought_text.split('\n'):
                                print(f"\033[90m│\033[0m {line}", flush=True)
                
                    # Print result (tool output/response)
                    # For sequential thinking, skip the JSON result if we showed the thought
                    if result:
                        # Check if result is just JSON metadata (starts with '{' and contains thoughtNumber)
                        is_json_metadata = result.strip().startswith('{') and 'thoughtNumber' in result
                    
                        # Only show result if it's not metadata and not a duplicate of the thought
                        if not is_json_metadata and result != args.get('thought', ''):
                            for line in result.rstrip('\n').split('\n'):
                                print(f"\033[90m│\033[0m {line}", flush=True)
                
                    print(f"\033[36m└─\033[0m", flush=True)
            
                elif chunk_type == 'stream_end':
                    break
            
                elif chunk_type == 'rewind':
                    # Targeted rewind from streaming_tool_executor
                    to_marker = chunk.get('to_marker')
                    if to_marker:
                        marker_str = f'<!-- REWIND_MARKER: {to_marker}'
                        marker_pos = full_response.find(marker_str)
                        if marker_pos >= 0:
                            full_response = full_response[:marker_pos]
                            if md_renderer:
                                from app.utils.terminal_markdown import StreamingMarkdownRenderer
                                md_renderer = StreamingMarkdownRenderer()
            
                elif chunk_type == 'throttling_error':
                    # Handle throttling gracefully
                    wait_time = chunk.get('suggested_wait', 60)
                    print(f"\n\033[33m⚠ Rate limit hit. Waiting {wait_time}s...\033[0m\n", file=sys.stderr)
                    # Don't break - let the executor handle the retry
            
                elif chunk_type == 'error':
                    error_msg = chunk.get('content', 'Unknown error')
                    print(f"\n\033[31mError: {error_msg}\033[0m", file=sys.stderr)
                    break
        except asyncio.CancelledError:
            # Streaming was cancelled - return whatever we accumulated so far
            print(f"\n\033[90m(Partial response collected: {len(full_response)} chars)\033[0m")
            if md_renderer:
                md_renderer.flush()
            # Return partial response instead of raising
            return full_response
        
        if stream:
            if md_renderer:
                md_renderer.flush()
            else:
                print()  # Final newline after streaming
        
        return full_response
    
    def _convert_to_openai_format(self, messages):
        """Convert LangChain messages to OpenAI format."""
        openai_msgs = []
        for msg in messages:
            if isinstance(msg, dict):
                openai_msgs.append(msg)
            elif hasattr(msg, 'type'):
                if msg.type == 'system':
                    openai_msgs.append({"role": "system", "content": msg.content})
                elif msg.type == 'human':
                    openai_msgs.append({"role": "user", "content": msg.content})
                elif msg.type == 'ai':
                    openai_msgs.append({"role": "assistant", "content": msg.content})
        return openai_msgs
    
    async def _simple_invoke(self, messages, stream: bool) -> str:
        """Simple invocation without tools."""
        if stream:
            response = ""
            async for chunk in self.model.astream(messages):
                content = getattr(chunk, 'content', '')
                if isinstance(content, str):
                    print(content, end='', flush=True)
                    response += content
            print()
            return response
        else:
            result = await self.model.ainvoke(messages)
            content = getattr(result, 'content', str(result))
            print(content)
            return content
    
    def _remove_tool_blocks(self, text: str, open_tag: str, close_tag: str) -> str:
        """Remove tool call blocks from text."""
        pattern = re.escape(open_tag) + r'.*?' + re.escape(close_tag)
        clean = re.sub(pattern, '', text, flags=re.DOTALL)
        # Clean up extra whitespace
        clean = re.sub(r'\n{3,}', '\n\n', clean)
        return clean.strip()
    
    async def _execute_tool_calls(self, response: str, processed: set, 
                                   open_tag: str, close_tag: str) -> Optional[Tuple[str, str, str]]:
        """Execute first unprocessed tool call. Returns (block, name, output) or None."""
        from app.mcp.tools import parse_tool_call
        from app.mcp.manager import get_mcp_manager
        
        # Find tool call blocks
        pattern = re.escape(open_tag) + r'.*?' + re.escape(close_tag)
        matches = re.findall(pattern, response, re.DOTALL)
        
        if not matches:
            return None
        
        for tool_block in matches:
            sig = hashlib.md5(tool_block.encode()).hexdigest()
            if sig in processed:
                continue
            
            processed.add(sig)
            
            # Parse the tool call
            parsed = parse_tool_call(tool_block)
            if not parsed:
                continue
            
            tool_name, arguments = parsed
            
            # Execute via MCP manager
            try:
                manager = get_mcp_manager()
                if manager:
                    result = await manager.call_tool(tool_name, arguments)
                    return (tool_block, tool_name, str(result))
                else:
                    return (tool_block, tool_name, "Error: MCP manager not available")
            except Exception as e:
                return (tool_block, tool_name, f"Error: {e}")
        
        return None

    def _print_auth_help(self):
        """Print authentication help based on endpoint."""
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
        print(file=sys.stderr)
        if endpoint == "bedrock":
            print("\033[33mTo fix AWS credentials:\033[0m", file=sys.stderr)
            print("  • Run: aws configure", file=sys.stderr)
            print("  • Or set AWS_PROFILE: export AWS_PROFILE=your-profile", file=sys.stderr)
            print("  • Or refresh SSO: aws sso login --profile your-profile", file=sys.stderr)
        elif endpoint == "google":
            print("\033[33mTo fix Google credentials:\033[0m", file=sys.stderr)
            print("  • Set GOOGLE_API_KEY environment variable", file=sys.stderr)
        print(file=sys.stderr)
    
    async def chat(self):
        """Interactive chat loop."""
        if self.model is None:
            print(f"\n\033[31mError: {self._init_error or 'Model not available'}\033[0m", file=sys.stderr)
            self._print_auth_help()
            return
        
        while True:
            try:
                # Use prompt_toolkit for rich input
                try:
                    user_input = await asyncio.to_thread(
                        self.session.prompt,
                        FormattedText([('bold magenta', 'ℤ'), ('cyan', 'iya'), ('', ' '), ('bold cyan', '› ')]),
                        # Add context-aware completion
                        refresh_interval=0.5
                    )
                    user_input = user_input.strip()
                except KeyboardInterrupt:
                    # Ctrl+C on empty prompt - continue
                    print()
                    continue
                
                # Check for double Ctrl+C exit signal
                if user_input == '__exit__':
                    print("\033[90mGoodbye\033[0m")
                    break
                
                if not user_input:
                    continue
                
                # Commands
                if user_input.startswith('/'):
                    if not await self._handle_command(user_input):
                        break
                    continue
                
                # Regular message
                print()
                try:
                    await self.ask(user_input)
                except asyncio.CancelledError:
                    # Operation was cancelled, continue the loop
                    pass
                print()
                
            except KeyboardInterrupt:
                # Ctrl+C during input - just continue
                print()
                continue
            except EOFError:
                break
        
        print("\033[90mGoodbye\033[0m")
    
    async def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands. Returns False to exit."""
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if command in ['/q', '/quit', '/exit']:
            return False
        
        elif command in ['/h', '/help']:
            print("""
\033[1mCommands:\033[0m
  /add <path>    Add file or directory to context
  /rm <path>     Remove from context  
  /files         List context files
  /clear         Clear conversation history
  /model <name>  Switch model
  /quit          Exit

\033[1mDiff Application:\033[0m
  When the AI provides code diffs, you'll be prompted to:
  [a]pply - Apply the diff to your files
  [s]kip - Skip this diff and continue
  [v]iew - View the full diff content
  [q]uit - Stop processing remaining diffs
""")
        
        elif command in ['/add', '/a']:
            if arg:
                root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
                new_files = resolve_files([arg], root)
                added = [f for f in new_files if f not in self.files]
                self.files.extend(added)
                print(f"\033[90mAdded {len(added)} files\033[0m")
            else:
                print("Usage: /add <path>")
        
        elif command in ['/files', '/ls', '/f']:
            if self.files:
                print(f"\033[1mContext files ({len(self.files)}):\033[0m")
                for f in self.files:
                    print(f"  {f}")
            else:
                print("\033[90mNo files in context\033[0m")
                print("\033[90mUse /add <path> to add files\033[0m")
        
        elif command in ['/rm', '/remove']:
            if arg:
                before = len(self.files)
                self.files = [f for f in self.files if arg not in f]
                print(f"\033[90mRemoved {before - len(self.files)} files\033[0m")
        elif command in ['/model', '/m']:
            await self._handle_model_selection_async(arg)
        
        else:
            print(f"\033[90mUnknown command: {command}\033[0m")
        
        return True

    async def _show_model_settings_dialog(self, model_name: str, model_config: dict) -> Optional[dict]:
        """Show simple text-based settings configuration."""
        from app.agents.models import ModelManager
        
        current_settings = ModelManager.get_model_settings()
        settings = {}
        
        print(f"\n\033[1;36m{'─' * 60}\033[0m")
        print(f"\033[1;36mConfigure {model_name}\033[0m")
        print(f"\033[1;36m{'─' * 60}\033[0m")
        print("\033[90mPress Enter to keep current value, or type new value\033[0m\n")
        
        # Temperature
        temp_range = model_config.get('parameter_ranges', {}).get('temperature', {'min': 0, 'max': 1, 'default': 0.3})
        current_temp = current_settings.get('temperature', temp_range.get('default', 0.3))
        temp_input = input(f"Temperature [{temp_range['min']}-{temp_range['max']}] (current: {current_temp}): ").strip()
        if temp_input:
            try:
                val = float(temp_input)
                if temp_range['min'] <= val <= temp_range['max']:
                    settings['temperature'] = val
                else:
                    print(f"\033[33mOut of range, using {current_temp}\033[0m")
                    settings['temperature'] = current_temp
            except ValueError:
                print(f"\033[33mInvalid, using {current_temp}\033[0m")
                settings['temperature'] = current_temp
        else:
            settings['temperature'] = current_temp
        
        # Max output tokens
        max_output = model_config.get('max_output_tokens', 4096)
        current_max = current_settings.get('max_output_tokens', max_output)
        max_input = input(f"Max Output Tokens [1-{max_output}] (current: {current_max}): ").strip()
        if max_input:
            try:
                val = int(max_input)
                if 1 <= val <= max_output:
                    settings['max_output_tokens'] = val
                else:
                    print(f"\033[33mOut of range, using {current_max}\033[0m")
                    settings['max_output_tokens'] = current_max
            except ValueError:
                print(f"\033[33mInvalid, using {current_max}\033[0m")
                settings['max_output_tokens'] = current_max
        else:
            settings['max_output_tokens'] = current_max
        
        # Top-k if supported
        family = model_config.get('family')
        if family and 'claude' in family:
            current_top_k = current_settings.get('top_k', 15)
            top_k_input = input(f"Top-K [0-500] (current: {current_top_k}): ").strip()
            if top_k_input:
                try:
                    val = int(top_k_input)
                    if 0 <= val <= 500:
                        settings['top_k'] = val
                    else:
                        print(f"\033[33mOut of range, using {current_top_k}\033[0m")
                        settings['top_k'] = current_top_k
                except ValueError:
                    print(f"\033[33mInvalid, using {current_top_k}\033[0m")
                    settings['top_k'] = current_top_k
            else:
                settings['top_k'] = current_top_k
        
        return settings
    
    async def _handle_model_selection_async(self, arg: str):
        """Handle /model command with async interactive selection."""
        from app.config.models_config import MODEL_CONFIGS, DEFAULT_MODELS
        
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        available_models = MODEL_CONFIGS.get(endpoint, {})
        current_model = os.environ.get("ZIYA_MODEL", DEFAULT_MODELS.get(endpoint, ""))
        
        # If a model name is provided directly, use it
        if arg:
            if arg in available_models:
                os.environ["ZIYA_MODEL"] = arg
                self._model = None  # Force reload
                print(f"\033[32m✓ Switched to {arg}\033[0m")
            else:
                print(f"\033[31mUnknown model: {arg}\033[0m")
                print(f"\033[90mAvailable: {', '.join(sorted(available_models.keys()))}\033[0m")
            return
        
        # No argument - show interactive selection
        if not available_models:
            print(f"\033[31mNo models available for endpoint: {endpoint}\033[0m")
            return
        
        # Sort models: group by family (claude, nova, etc.) then by version descending
        def sort_key(model_name):
            # Extract family prefix
            for prefix in ['sonnet', 'opus', 'haiku', 'nova', 'gemini', 'deepseek', 'openai', 'qwen']:
                if model_name.lower().startswith(prefix):
                    rest = model_name[len(prefix):].lstrip('-')
                    try:
                        version = float(rest.split('-')[0]) if rest and rest[0].isdigit() else 0
                    except ValueError:
                        version = 0
                    return (prefix, -version, model_name)
            return ('zzz', 0, model_name)
        
        sorted_models = sorted(available_models.keys(), key=sort_key)
        
        # Build radio list values with formatted labels
        radio_values = []
        for model_name in sorted_models:
            config = available_models[model_name]
            indicators = []
            if model_name == current_model:
                indicators.append("✓ current")
            if model_name == DEFAULT_MODELS.get(endpoint):
                indicators.append("default")
            
            # Show context window with auto-scale info
            token_limit = config.get('token_limit')
            extended_limit = config.get('extended_context_limit')
            supports_extended = config.get('supports_extended_context', False)
            
            if supports_extended and extended_limit:
                # Show base→extended format
                base_display = f"{token_limit // 1000000}M" if token_limit >= 1000000 else f"{token_limit // 1000}K"
                extended_display = f"{extended_limit // 1000000}M" if extended_limit >= 1000000 else f"{extended_limit // 1000}K"
                indicators.append(f"{base_display}→{extended_display} ctx")
            elif token_limit:
                # Show just the token limit
                if token_limit >= 1000000:
                    indicators.append(f"{token_limit // 1000000}M ctx")
                else:
                    indicators.append(f"{token_limit // 1000}K ctx")
            
            label_text = model_name
            if indicators:
                label_text += f"  ({', '.join(indicators)})"
            
            radio_values.append((model_name, label_text))
        
        # Create radio list
        radio_list = RadioList(values=radio_values, default=current_model if current_model in available_models else sorted_models[0])
        
        # Create key bindings
        kb = KeyBindings()
        
        configure_requested = {'value': False}
        
        @kb.add('enter')
        def _(event):
            event.app.exit(result=radio_list.current_value)
        
        @kb.add('right')
        def _(event):
            # Mark that user wants to configure settings
            configure_requested['value'] = True
            event.app.exit(result=radio_list.current_value)
        
        @kb.add('escape')
        def _(event):
            event.app.exit(result=None)
        
        # Override Enter to make it directly select the highlighted item
        # RadioList normally requires: space to mark, then enter to confirm
        # We want: enter to immediately select highlighted item
        custom_kb = KeyBindings()
        
        # Override navigation keys to auto-select as we move
        @custom_kb.add('up')
        def _(event):
            # Move up and auto-select
            radio_list._selected_index = max(0, radio_list._selected_index - 1)
            radio_list.current_value = radio_list.values[radio_list._selected_index][0]
        
        @custom_kb.add('down')
        def _(event):
            # Move down and auto-select
            radio_list._selected_index = min(len(radio_list.values) - 1, radio_list._selected_index + 1)
            radio_list.current_value = radio_list.values[radio_list._selected_index][0]
        
        @custom_kb.add('enter')
        def _(event):
            # Get the currently highlighted value (not the space-marked one)
            # RadioList stores this in _selected_index
            highlighted_value = radio_list.values[radio_list._selected_index][0]
            radio_list.current_value = highlighted_value
            event.app.exit(result=highlighted_value)
        
        from prompt_toolkit.key_binding import merge_key_bindings
        # Put custom_kb LAST so our Enter handler overrides RadioList's default
        radio_list.control.key_bindings = merge_key_bindings([radio_list.control.key_bindings, custom_kb])
        
        # Create application layout
        layout = Layout(HSplit([
            Window(
                content=FormattedTextControl(text=f'Select Model ({endpoint}) - ↑/↓ to navigate, Enter to select, → to configure, Esc to cancel\n'),
                height=2
            ),
            radio_list,
        ]))
        
        # Create and run application
        app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=False,
            mouse_support=True
        )
        
        try:
            result = await app.run_async()
            
            if result:
                selected_model = result
                selected_config = available_models[selected_model]
                
                # If user pressed right arrow, show settings dialog
                if configure_requested['value']:
                    settings = await self._show_model_settings_dialog(selected_model, selected_config)
                    if settings is None:
                        # User cancelled settings, go back to model selection
                        print(f"\n\033[90mSettings cancelled, keeping model: {current_model}\033[0m")
                        return
                    
                    # Apply settings
                    for key, value in settings.items():
                        env_key = f"ZIYA_{key.upper()}"
                        os.environ[env_key] = str(value)
                    
                    print(f"\n\033[32m✓ Switched to {selected_model} with custom settings\033[0m")
                    for key, value in settings.items():
                        print(f"  {key}: {value}")
                else:
                    if selected_model == current_model:
                        print(f"\n\033[90mModel unchanged: {selected_model}\033[0m")
                        return
                    print(f"\n\033[32m✓ Switched to {selected_model}\033[0m")
                
                os.environ["ZIYA_MODEL"] = selected_model
                self._model = None  # Force reload
            else:
                print(f"\n\033[90mCancelled\033[0m")
        except Exception as e:
            print(f"\n\033[33mInteractive selection failed: {e}\033[0m")
            print(f"\033[90mCurrent: {current_model}\033[0m")
            print(f"\033[90mUse: /model <name>\033[0m")


async def _initialize_mcp():
    """Initialize MCP servers for CLI mode."""
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.initialize()
        
        if mcp_manager.is_initialized:
            status = mcp_manager.get_server_status()
            connected = sum(1 for s in status.values() if s.get("connected"))
            tools = sum(s.get("tools", 0) for s in status.values())
            print(f"\033[90mMCP: {connected} servers, {tools} tools\033[0m", file=sys.stderr)
    except Exception as e:
        print(f"\033[90mMCP initialization skipped: {e}\033[0m", file=sys.stderr)


async def _run_async_cli(cli):
    """Run CLI in async context with MCP initialized."""
    # Initialize MCP in this event loop
    await _initialize_mcp()
    
    # Now run the chat in the same loop
    await cli.chat()


# ============================================================================
# Command handlers
# ============================================================================

def cmd_chat(args):
    """Handle: ziya chat [FILES...]"""
    # CRITICAL FIX: Set up environment BEFORE auth check
    # This ensures AWS_PROFILE is set before check_aws_credentials is called
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Handle session resume
    if getattr(args, 'resume', False):
        session_id = select_session()
        if session_id:
            try:
                session_data = load_session(session_id)
                files = session_data.get('files', [])
                history = session_data.get('history', [])
                
                cli = CLI(files=files)
                cli.history = history
                
                print(f"\033[32m✓ Resumed session from {session_data.get('timestamp', 'unknown')}\033[0m")
                print(f"  Files: {len(files)}, Messages: {len(history)}\n")
                
                asyncio.run(_run_async_cli(cli))
                
                # Save session on exit (unless ephemeral)
                if not getattr(args, 'ephemeral', False):
                    save_session(cli)
                    print(f"\n\033[90mSession saved\033[0m")
                
                return
            except FileNotFoundError as e:
                print(f"\033[33m{e}\033[0m")
                print("\033[90mStarting new session instead\033[0m\n")
    
    # Now check auth with the profile from setup_env
    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    files = resolve_files(args.files, root) if args.files else []
    
    cli = CLI(files=files)
    asyncio.run(_run_async_cli(cli))
    
    # Save session on exit (unless ephemeral)
    if not getattr(args, 'ephemeral', False):
        save_session(cli)
        print(f"\n\033[90mSession saved\033[0m")


def cmd_ask(args):
    """Handle: ziya ask "question" [FILES...]"""
    # CRITICAL FIX: Set up environment BEFORE auth check
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Now check auth with the profile from setup_env
    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    files = resolve_files(args.files, root) if args.files else []
    
    # Initialize MCP servers
    asyncio.run(_initialize_mcp())
    
    # Build question from args and stdin
    question = args.question
    
    # Check for piped input
    stdin_content = read_stdin_if_available()
    if stdin_content:
        question = f"{question}\n\n```\n{stdin_content}\n```" if question else stdin_content
    
    if not question:
        print("Error: No question provided", file=sys.stderr)
        sys.exit(1)
    
    cli = CLI(files=files)
    asyncio.run(cli.ask(question, stream=not args.no_stream))


def cmd_review(args):
    """Handle: ziya review [FILES...] [--staged]"""
    # CRITICAL FIX: Set up environment BEFORE auth check
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    # Show clean startup info
    print_chat_startup_info(args)
    
    
    # Now check auth with the profile from setup_env
    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    
    # Get content to review
    content = None
    files = []
    
    if args.staged:
        content = get_git_staged_diff()
        if not content:
            print("No staged changes to review", file=sys.stderr)
            sys.exit(1)
    elif args.diff:
        content = get_git_diff()
        if not content:
            print("No changes to review", file=sys.stderr)
            sys.exit(1)
    else:
        # Check stdin first
        content = read_stdin_if_available()
        if not content and args.files:
            files = resolve_files(args.files, root)
    
    # Initialize MCP servers
    asyncio.run(_initialize_mcp())
    
    prompt = args.prompt or "Review this code. Focus on bugs, security issues, and improvements."
    
    if content:
        question = f"{prompt}\n\n```\n{content}\n```"
    else:
        question = prompt
    
    cli = CLI(files=files)
    asyncio.run(cli.ask(question, stream=not args.no_stream))


def cmd_explain(args):
    """Handle: ziya explain [FILES...]"""
    # CRITICAL FIX: Set up environment BEFORE auth check
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Now check auth with the profile from setup_env
    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    files = resolve_files(args.files, root) if args.files else []
    
    # Initialize MCP servers
    asyncio.run(_initialize_mcp())
    
    content = read_stdin_if_available()
    prompt = args.prompt or "Explain this code clearly and concisely."
    
    if content:
        question = f"{prompt}\n\n```\n{content}\n```"
    else:
        question = prompt
    
    cli = CLI(files=files)
    asyncio.run(cli.ask(question, stream=not args.no_stream))


# ============================================================================
# Auth helpers
# ============================================================================

def _check_auth_quick(profile: str = None) -> bool:
    """Quick check if authentication is likely to work."""
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    
    if endpoint == "bedrock":
        try:
            from app.utils.aws_utils import check_aws_credentials
            # Pass profile explicitly to ensure it's used
            valid, _ = check_aws_credentials(profile_name=profile)
            return valid
        except Exception:
            return False
    elif endpoint == "google":
        return bool(os.environ.get("GOOGLE_API_KEY"))
    
    return True


def _print_auth_error():
    """Print authentication error with helpful instructions."""
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    
    print("\n\033[31m✗ Authentication failed\033[0m\n", file=sys.stderr)
    
    if endpoint == "bedrock":
        print("Your AWS credentials are missing or expired.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  aws sso login --profile <your-profile>", file=sys.stderr)
        print("  # or", file=sys.stderr)
        print("  export AWS_PROFILE=<your-profile>", file=sys.stderr)
        print("  # or", file=sys.stderr)
        print("  aws configure", file=sys.stderr)
    elif endpoint == "google":
        print("GOOGLE_API_KEY environment variable is not set.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  export GOOGLE_API_KEY=<your-api-key>", file=sys.stderr)
    
    print(file=sys.stderr)


# ============================================================================
# Argument parsing
# ============================================================================

def create_parser():
    """Create the CLI argument parser."""
    from app.config.common_args import add_common_arguments
    
    parser = argparse.ArgumentParser(
        prog='ziya',
        description='Ziya AI coding assistant',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ziya chat                        Interactive chat
  ziya chat src/                   Chat with src/ in context  
  ziya ask "what does this do?" main.py
  ziya explain utils.py
  ziya review --staged             Review staged git changes
  git diff | ziya review           Review piped diff
  cat error.log | ziya ask "what's wrong?"
        """
    )
    
    # Create a parent parser with common arguments (add_help=False prevents conflict)
    common_parent = argparse.ArgumentParser(add_help=False)
    add_common_arguments(common_parent)
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # chat
    chat_parser = subparsers.add_parser('chat', parents=[common_parent], help='Interactive chat')
    chat_parser.add_argument('files', nargs='*', help='Files/directories for context')
    chat_parser.add_argument('--resume', action='store_true', help='Resume from a previous session')
    chat_parser.add_argument('--ephemeral', action='store_true', help='Do not save session history')
    chat_parser.set_defaults(func=cmd_chat)
    
    # ask
    ask_parser = subparsers.add_parser('ask', parents=[common_parent], help='Ask a question')
    ask_parser.add_argument('question', nargs='?', help='Question to ask')
    ask_parser.add_argument('files', nargs='*', help='Files for context')
    ask_parser.set_defaults(func=cmd_ask)
    review_parser = subparsers.add_parser('review', parents=[common_parent], help='Review code')
    review_parser.add_argument('files', nargs='*', help='Files to review')
    review_parser.add_argument('--staged', '-s', action='store_true', help='Review staged git changes')
    review_parser.add_argument('--diff', '-d', action='store_true', help='Review unstaged git changes')
    review_parser.add_argument('--prompt', '-p', help='Custom review prompt')
    review_parser.set_defaults(func=cmd_review)
    
    # explain
    explain_parser = subparsers.add_parser('explain', parents=[common_parent], help='Explain code')
    explain_parser.add_argument('files', nargs='*', help='Files to explain')
    explain_parser.add_argument('--prompt', '-p', help='Custom prompt')
    explain_parser.set_defaults(func=cmd_explain)
    
    return parser
    
    


def main():
    """CLI entry point."""
    parser = create_parser()
    
    # Pre-process argv to support flags both before and after subcommand
    # e.g., "ziya --profile x chat" -> "ziya chat --profile x"
    argv = sys.argv[1:]
    commands = {'chat', 'ask', 'review', 'explain'}
    global_flags = {'--model', '-m', '--profile', '--region', '--root', '--no-stream', '--debug'}
    
    # Find command position
    cmd_idx = None
    for i, arg in enumerate(argv):
        if arg in commands:
            cmd_idx = i
            break
    
    # If command found and there are flags before it, move them after
    if cmd_idx is not None and cmd_idx > 0:
        pre_cmd = argv[:cmd_idx]
        cmd = argv[cmd_idx]
        post_cmd = argv[cmd_idx + 1:]
        
        # Separate flags from non-flags in pre_cmd section
        flags_to_move = []
        i = 0
        while i < len(pre_cmd):
            arg = pre_cmd[i]
            if arg in global_flags or arg.startswith('--'):
                flags_to_move.append(arg)
                # Check if next arg is a value (not a flag)
                if i + 1 < len(pre_cmd) and not pre_cmd[i + 1].startswith('-'):
                    flags_to_move.append(pre_cmd[i + 1])
                    i += 1
            i += 1
        
        # Reconstruct argv with flags after command
        argv = [cmd] + post_cmd + flags_to_move
    
    args = parser.parse_args(argv)
    
    if args.command is None:
        # No command - show help
        parser.print_help()
        sys.exit(0)
    
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
        sys.exit(0)
    except Exception as e:
        # Extract traceback info for better error reporting
        tb = traceback.extract_tb(sys.exc_info()[2])
        if tb:
            last_frame = tb[-1]
            location = f"{last_frame.filename}:{last_frame.lineno}"
            print(f"\033[31mError in {location}: {e}\033[0m", file=sys.stderr)
            if os.environ.get('ZIYA_LOG_LEVEL') == 'DEBUG':
                print("\nFull traceback:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
        else:
            print(f"\033[31mError: {e}\033[0m", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

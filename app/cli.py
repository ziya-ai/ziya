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

import argparse
import hashlib
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
            except (AttributeError, TypeError, ValueError):
                pass  # Skip loggers that can't be configured
except (AttributeError, TypeError, ValueError, RuntimeError) as e:
    print(f"Warning: Could not configure logging: {e}", file=sys.stderr)
import re
import signal
import time
import traceback
from pathlib import Path
import sys
from app.utils.logging_utils import logger
from typing import List, Tuple 
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import PathCompleter, WordCompleter, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_selection
from prompt_toolkit.keys import Keys
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


def save_session(cli: 'CLI', name: Optional[str] = None) -> str:
    """Save current session and return session ID.

    If the CLI already has a _session_id (from resume or a prior save),
    that same file is updated in place (checkpoint semantics). Otherwise
    a new timestamp-based id is generated, optionally suffixed with a
    user-supplied name.
    """
    session_dir = get_session_dir()
    
    # Extract opening statement from first human message
    opening_statement = ''
    for msg in cli.history:
        if msg.get('type') == 'human':
            opening_statement = msg.get('content', '')[:120]
            break

    # Preserve start_time from a previously loaded session, otherwise use now
    start_time = getattr(cli, '_session_start_time', None) or datetime.now().isoformat()

    # Determine session id / filename
    existing_id = getattr(cli, '_session_id', None)
    # Resolve the friendly name: explicit arg wins, else keep prior name
    resolved_name = name if name is not None else getattr(cli, '_session_name', None)

    if existing_id:
        session_id = existing_id
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        if resolved_name:
            safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in resolved_name)[:40]
            session_id = f"{ts}_{safe}" if safe else ts
        else:
            session_id = ts

    session_file = session_dir / f"{session_id}.json"

    session_data = {
        'id': session_id,
        'name': resolved_name,
        'start_time': start_time,
        'last_update_time': datetime.now().isoformat(),
        'timestamp': datetime.now().isoformat(),  # kept for backward compat
        'opening_statement': opening_statement,
        'files': cli.files,
        'history': cli.history
    }
    
    with open(session_file, 'w') as f:
        json.dump(session_data, f, indent=2)

    # Remember id/name on the CLI for subsequent checkpoints
    cli._session_id = session_id
    cli._session_name = resolved_name

    # Cleanup old sessions (keep last 10) — but preserve named sessions
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


def find_session_by_name(name: str) -> Optional[str]:
    """Find a session id by friendly name or id match.

    Preference order: exact name match, exact id match, name prefix,
    id prefix, name substring. Returns the most recently updated match.
    """
    session_dir = get_session_dir()
    candidates = []  # (priority, mtime, id)
    for p in sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        sid = data.get('id') or p.stem
        sname = data.get('name') or ''
        mt = p.stat().st_mtime
        if sname == name:
            candidates.append((0, mt, sid))
        elif sid == name:
            candidates.append((1, mt, sid))
        elif sname and sname.startswith(name):
            candidates.append((2, mt, sid))
        elif sid.startswith(name):
            candidates.append((3, mt, sid))
        elif sname and name.lower() in sname.lower():
            candidates.append((4, mt, sid))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], -t[1]))
    return candidates[0][2]


def cleanup_old_sessions(keep_count: int = 10):
    """Keep only the most recent sessions. Named sessions are preserved."""
    session_dir = get_session_dir()
    sessions = sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)

    # Protect named sessions (have a non-null "name" field) from auto-cleanup
    unnamed = []
    for p in sessions:
        try:
            with open(p) as f:
                if json.load(f).get('name'):
                    continue
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        unnamed.append(p)
    for old_session in unnamed[keep_count:]:
        old_session.unlink()


async def select_session() -> Optional[str]:
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
                    'name': data.get('name'),
                    'start_time': data.get('start_time', data.get('timestamp', '')),
                    'last_update_time': data.get('last_update_time', data.get('timestamp', '')),
                    'opening_statement': data.get('opening_statement', ''),
                    'file_count': len(data.get('files', [])),
                    'message_count': len(data.get('history', []))
                })
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue
    
    if not session_list:
        print("No valid sessions found.")
        return None
    
    # Build radio list values with formatted labels
    radio_values = []
    for session in session_list:
        try:
            started = datetime.fromisoformat(session['start_time']).strftime('%b %d %H:%M')
        except (ValueError, TypeError):
            started = '?'
        try:
            updated = datetime.fromisoformat(session['last_update_time']).strftime('%b %d %H:%M')
        except (ValueError, TypeError):
            updated = started

        opener = session.get('opening_statement', '') or ''
        # Truncate and clean for display
        opener = opener.replace('\n', ' ').strip()
        if len(opener) > 80:
            opener = opener[:77] + '...'

        from prompt_toolkit.formatted_text import HTML

        from html import escape as html_escape
        opener = html_escape(opener)
        name = session.get('name')
        meta = f"{session['message_count']} msgs, {session['file_count']} files"
        if started == updated:
            time_info = f"  <style fg='ansibrightblack'>{started}</style>"
        else:
            time_info = f"  <style fg='ansibrightblack'>started {started} · updated {updated}</style>"

        name_tag = f"<style fg='ansicyan'>[{html_escape(name)}]</style> " if name else ""
        if opener:
            label = HTML(f"{name_tag}<b>{opener}</b>\n    {meta}{time_info}")
        else:
            label = HTML(f"{name_tag}<b>(no opening message)</b>  {meta}{time_info}")

        radio_values.append((session['id'], label))

    radio_list = RadioList(values=radio_values, default=session_list[0]['id'])

    # Key bindings
    kb = KeyBindings()

    @kb.add('enter')
    def _(event):
        event.app.exit(result=radio_list.current_value)

    @kb.add('escape')
    def _(event):
        event.app.exit(result=None)

    custom_kb = KeyBindings()

    @custom_kb.add('up')
    def _(event):
        radio_list._selected_index = max(0, radio_list._selected_index - 1)
        radio_list.current_value = radio_list.values[radio_list._selected_index][0]

    @custom_kb.add('down')
    def _(event):
        radio_list._selected_index = min(len(radio_list.values) - 1, radio_list._selected_index + 1)
        radio_list.current_value = radio_list.values[radio_list._selected_index][0]

    @custom_kb.add('enter')
    def _(event):
        highlighted = radio_list.values[radio_list._selected_index][0]
        radio_list.current_value = highlighted
        event.app.exit(result=highlighted)

    from prompt_toolkit.key_binding import merge_key_bindings
    radio_list.control.key_bindings = merge_key_bindings([radio_list.control.key_bindings, custom_kb])

    layout = Layout(HSplit([
        Window(
            content=FormattedTextControl(text='Resume Session — ↑/↓ navigate, Enter select, Esc cancel\n'),
            height=2
        ),
        radio_list,
    ]))

    app = Application(layout=layout, key_bindings=kb, full_screen=False, mouse_support=True)

    try:
        return await app.run_async()
    except (EOFError, KeyboardInterrupt):
        return None


def print_chat_startup_info(args):
    """Pretty print essential startup information for chat mode."""
    root = getattr(args, 'root', None) or os.getcwd()
    profile = getattr(args, 'profile', None) or os.environ.get('AWS_PROFILE', 'default')
    model = getattr(args, 'model', None) or os.environ.get('ZIYA_MODEL', '')
    
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
    except (ImportError, AttributeError, OSError, RuntimeError):
        pass  # Silently skip if MCP not available
    
    print()  # Blank line before prompt


def setup_env(args):
    """CLI entry-point environment setup.

    Handles CLI-only concerns (debug logging, logger reconfiguration),
    then delegates common settings to the shared setup_environment().
    """
    # Handle debug flag first, before any other setup (must precede shared setup
    # so that logger calls inside it respect the level).
    if getattr(args, 'debug', False):
        os.environ["ZIYA_LOG_LEVEL"] = "DEBUG"
        os.environ["ZIYA_MODE"] = "debug"
        print("🐛 Debug logging enabled", file=sys.stderr)

    # Reconfigure existing loggers so modules imported before ZIYA_MODE was set
    # pick up the correct level (WARNING for chat, DEBUG when --debug).
    try:
        target_level = getattr(logging, os.environ.get('ZIYA_LOG_LEVEL', 'WARNING').upper())
        for logger_name in list(logging.Logger.manager.loggerDict.keys()):
            if logger_name.startswith('app.') or logger_name == 'app':
                try:
                    existing_logger = logging.getLogger(logger_name)
                    existing_logger.setLevel(target_level)
                    for handler in existing_logger.handlers:
                        handler.setLevel(target_level)
                except (AttributeError, TypeError, ValueError):
                    pass
    except (AttributeError, TypeError, ValueError, RuntimeError) as e:
        print(f"Warning: Could not reconfigure logging in setup_env: {e}", file=sys.stderr)

    # Shared setup (root dir, AWS, endpoint/model validation, model params, …)
    from app.config.environment import setup_environment as _shared_setup_environment
    _shared_setup_environment(args)

    # -- CLI-only: enable MCP by default for CLI sessions -------------------
    os.environ.setdefault("ZIYA_ENABLE_MCP", "true")

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
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def get_git_diff() -> Optional[str]:
    """Get diff of unstaged changes."""
    import subprocess
    try:
        result = subprocess.run(['git', 'diff'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


class CLI:
    """Lightweight CLI client."""
    
    def __init__(self, files: List[str] = None):
        self.files = files or []
        self.history = []
        self.conversation_id = f"cli_{os.getpid()}"
        self._model = None
        self._init_error = None
        self._active_task = None  # Track active streaming task for cancellation
        self._cancellation_requested = False
        self._diff_applicator = None  # Lazy-load diff applicator
        self._last_ctrl_c_time = 0   # Track last Ctrl+C press for double-tap exit
        self._partial_response = ""  # Accumulates streaming content for crash recovery
        self._last_keypress_time = 0  # Track last keypress for paste detection
        self._last_input_time = 0  # Track last input time for paste detection
        self._session_shell_commands = None  # Session-local shell command overrides
        self._session_yolo = False  # Session-local yolo mode (never persisted)
        self._session_timeout = None  # Session-local command timeout override
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
            
        except Exception as e:  # Intentionally broad: model init can raise credential/import/config/API errors
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
                    '/shell',
                    '/files', '/ls', '/f',
                    '/clear',
                    '/model', '/m',
                    '/quit', '/q', '/exit',
                    '/reset',
                    '/suspend', '/resume',
                    '/save',
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
                except (ImportError, KeyError, AttributeError):
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

        # Paste detection threshold: keypresses arriving faster than this are likely pasted
        PASTE_THRESHOLD_SEC = 0.05  # 50ms

        # Key bindings for ^C handling  
        bindings = KeyBindings()
        
        @bindings.add('c-c')
        def _(event):
            """Handle Ctrl+C - double tap to exit, or cancel/clear."""
            current_time = time.time()
            time_since_last = current_time - self._last_ctrl_c_time
            
            if self._active_task:
                # Cancel the active streaming task
                self._cancellation_requested = True
                if not self._active_task.done():
                    self._active_task.cancel()
                print("\n\033[33m^C - Cancelling operation...\033[0m")
                # Reset double-tap timer so this doesn't count toward exit
                self._last_ctrl_c_time = 0
                event.app.exit(result='')
            elif event.app.current_buffer.text:
                # Clear current input
                event.app.current_buffer.reset()
                # Reset double-tap timer so clearing text doesn't count toward exit
                self._last_ctrl_c_time = 0
            else:
                # Empty input - double tap within 1 second = exit
                if time_since_last < 1.0:
                    event.app.exit(result='__exit__')
                    return
                # First tap on empty prompt - show exit hint and start timer
                print("\033[90m(Press ^C again to exit, or ^D / /quit)\033[0m")
                self._last_ctrl_c_time = current_time
        
        @bindings.add(Keys.Any)
        def _track_keypress(event):
            """Track keypress timing for paste detection."""
            self._last_keypress_time = time.time()
            event.current_buffer.insert_text(event.data)

        @bindings.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            """Handle bracketed paste: insert full pasted text without submitting."""
            data = event.data
            data = data.replace("\r\n", "\n")
            data = data.replace("\r", "\n")
            event.current_buffer.insert_text(data)

        @bindings.add('enter', filter=~has_selection)
        def _(event):
            """Handle Enter - submit input, or insert newline if mid-paste."""
            buffer = event.current_buffer
            now = time.time()
            time_since_last_key = now - self._last_keypress_time
            if time_since_last_key < PASTE_THRESHOLD_SEC:
                # Rapid input detected (paste) — insert newline instead of submitting
                buffer.insert_text("\n")
                return
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
        self._partial_response = ""  # Reset per-request accumulator

        # Track partial response for cancellation scenarios
        partial_response = ""
        
        try:
            response = await self._run_with_tools_and_validate(question, stream)
            partial_response = response
        except asyncio.CancelledError:
            # In Python 3.9+, CancelledError inherits from BaseException,
            # not Exception — it must be caught explicitly before the
            # generic Exception handler.
            print("\n\033[33mOperation cancelled.\033[0m")
            response = self._partial_response or partial_response
            self.history.append({'type': 'human', 'content': question})
            if response:
                self.history.append({'type': 'ai', 'content': response})
            return response
        except Exception as e:  # Intentionally broad: must preserve partial response on any failure
            error_str = str(e)
            
            # Preserve partial response that was already streamed to the user
            partial = self._partial_response or partial_response
            if partial:
                print(f"\n\033[33m⚠ Response was truncated due to an error ({len(partial)} chars preserved).\033[0m", file=sys.stderr)
                self.history.append({'type': 'human', 'content': question})
                self.history.append({'type': 'ai', 'content': partial})
            
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
            
            # Return whatever partial content was accumulated
            return partial if partial else ""
        
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
            print(f"\033[90m[trace] model call attempt={attempt}\033[0m", file=sys.stderr)
            
            response = await self._run_with_tools_from_messages(messages, stream)
            print(f"\033[90m[trace] model returned, len={len(response)}, has_diff={'```diff' in response}\033[0m", file=sys.stderr)
            
            # If no diffs, we're done
            if '```diff' not in response:
                # Check if response looks incomplete (truncated mid-thought)
                stripped = response.rstrip()
                looks_incomplete = (
                    stripped.endswith(':') or
                    stripped.endswith('...') or
                    (len(stripped) > 100 and not stripped[-1] in '.!?)')
                )
                if looks_incomplete:
                    print("\033[90m[trace] response looks incomplete, auto-continuing\033[0m", file=sys.stderr)
                    messages.append(AIMessage(content=response))
                    messages.append(HumanMessage(content="[System: Your response appears incomplete. Please continue where you left off.]"))
                    continuation = await self._run_with_tools_from_messages(messages, stream)
                    validation_hook = None
                    return response + continuation
                else:
                    # Clean up validation hook
                    validation_hook = None
                    return response
            
            # Validate diffs using FULL apply pipeline (dry-run)
            print(f"\033[90m[trace] starting validate_and_enhance\033[0m", file=sys.stderr)
            try:
                validation_feedback = await asyncio.wait_for(
                    asyncio.to_thread(
                        validation_hook.validate_and_enhance,
                        content=response,
                        model_messages=messages,
                    ),
                    timeout=30,
                )
            except (asyncio.TimeoutError, Exception) as e:
                print(f"\033[33m⚠ Diff validation timed out or failed ({e}), skipping validation\033[0m", file=sys.stderr)
                validation_feedback = None
            print(f"\033[90m[trace] validate_and_enhance done, has_feedback={bool(validation_feedback)}\033[0m", file=sys.stderr)
            
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
                        print("\033[90m[trace] entering process_response\033[0m", file=sys.stderr)
                        completed_normally = self.diff_applicator.process_response(full_response)
                        print(f"\033[90m[trace] process_response done, completed_normally={completed_normally}\033[0m", file=sys.stderr)
                    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as e:
                        if os.environ.get('ZIYA_LOG_LEVEL') == 'DEBUG':
                            print(f"\n\033[33mNote: Could not process diffs: {e}\033[0m", file=sys.stderr)
                        break
                    
                    if completed_normally:
                        # If any diffs failed at apply time, tell the model what
                        # failed so it can correct — don't silently continue.
                        if self.diff_applicator.failed_count > 0:
                            failed_results = [
                                f"  ✗ {fp}: {msg}"
                                for fp, status, msg in self.diff_applicator.diff_results
                                if status == "failed"
                            ]
                            failure_feedback = (
                                f"The following diffs failed to apply:\n"
                                + "\n".join(failed_results)
                                + "\n\nPlease re-read the current file content and regenerate "
                                "only the failed diffs with corrected line numbers and context."
                            )
                            messages.append(AIMessage(content=full_response))
                            messages.append(HumanMessage(content=failure_feedback))
                            # Inject current file content for each failed file
                            for fp, status, _ in self.diff_applicator.diff_results:
                                if status == "failed":
                                    if not fp:
                                        continue
                                    file_content = validation_hook.read_file_for_context(fp)
                                    if file_content:
                                        lang = validation_hook._detect_language(fp)
                                        messages.append(HumanMessage(content=(
                                            f"[SYSTEM: Current content of {fp}]\n\n"
                                            f"```{lang}\n{file_content}\n```"
                                        )))
                            continue_response = await self._run_with_tools_from_messages(messages, stream)
                            if '```diff' in continue_response:
                                full_response = continue_response
                                continue
                            return response + "\n\n" + continue_response

                        # After processing diffs, check if model wants to continue
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
                        print("\033[90m[trace] sending continuation to model\033[0m", file=sys.stderr)
                        continue_response = await self._continue_conversation(continuation_message, messages)
                        print(f"\033[90m[trace] continuation returned, len={len(continue_response)}\033[0m", file=sys.stderr)
                        
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
        if hasattr(applicator, 'diff_results') and applicator.diff_results:
            lines = ["Diff application results:"]
            for file_path, status, message in applicator.diff_results:
                if status == "applied":
                    lines.append(f"  ✓ {file_path}: {message}")
                elif status == "failed":
                    lines.append(f"  ✗ {file_path}: FAILED - {message}")
                elif status == "skipped":
                    lines.append(f"  ⊘ {file_path}: skipped by user")
            
            # Add actionable context for failures
            failed = [(fp, msg) for fp, st, msg in applicator.diff_results if st == "failed"]
            if failed:
                lines.append("")
                lines.append("Failed diffs need to be regenerated. For each failure:")
                for fp, msg in failed:
                    lines.append(f"  - {fp}: {msg}")
                lines.append("Please re-read the current file content and regenerate only the failed diffs.")
            
            return "\n".join(lines)
        elif hasattr(applicator, 'applied_count'):
            total = getattr(applicator, 'applied_count', 0) + getattr(applicator, 'skipped_count', 0) + getattr(applicator, 'failed_count', 0)
            if total > 0:
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
        
        # Check for native function calling endpoints - use model loop instead of AWS executor
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        if endpoint in ("google", "openai", "anthropic"):
            async def stream_task():
                async for chunk in self.model.astream(messages, tools=tools):
                    if self._cancellation_requested:
                        raise asyncio.CancelledError("User cancelled operation")
                    yield chunk
            
            task = asyncio.create_task(self._stream_handler(stream_task(), stream))
            self._active_task = task
            try:
                return await task
            except asyncio.CancelledError:
                task.cancel()
                raise  # Re-raise to be handled by ask()
            finally:
                if not task.done():
                    task.cancel()
                self._active_task = None

        state = ModelManager.get_state()
        executor = StreamingToolExecutor(
            profile_name=state.get('aws_profile'),
            region=state.get('aws_region', 'us-west-2')
        )
        
        openai_messages = self._convert_to_openai_format(messages)
        
        async def stream_task():
            async for chunk in executor.stream_with_tools(openai_messages, tools, conversation_id=self.conversation_id):
                if self._cancellation_requested:
                    raise asyncio.CancelledError("User cancelled operation")
                yield chunk
        
        task = asyncio.create_task(self._stream_handler(stream_task(), stream))
        self._active_task = task
        
        try:
            full_response = await task
            return full_response
        except asyncio.CancelledError:
            task.cancel()
            raise  # Re-raise to be handled by ask()
        finally:
            if not task.done():
                task.cancel()
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
                
                    # Update instance-level accumulator so ask() can recover
                    # partial content if the stream is cancelled mid-flight.
                    self._partial_response = full_response

                    # For display: filter out rewind markers and continuation messages
                    display_content = re.sub(r'<!-- REWIND_MARKER: [^\s]+(?: -->|(?:\|FENCE:[`~]\w*)? -->)', '', content)
                    display_content = display_content.replace('', '')
                
                    # Render through markdown renderer or print raw
                    if display_content:  # Only skip completely empty strings
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
                    if md_renderer:
                        md_renderer.flush()
                    print(f"\n\033[90m⚡ {tool_name}\033[0m", flush=True)
            
                elif chunk_type == 'tool_start':
                    tool_name = chunk.get('tool_name', 'unknown')
                    display_header = chunk.get('display_header', tool_name)
                    if md_renderer:
                        md_renderer.flush()
                    print(f"\n\033[36m⚙ Executing {display_header}...\033[0m", flush=True)
            
                elif chunk_type == 'tool_display':
                    try:
                        if md_renderer:
                            md_renderer.flush()
                        # Show tool result with formatting
                        tool_name = chunk.get('tool_name', 'unknown')
                        result = chunk.get('result', '') or ''
                        args = chunk.get('args') or {}
                        if not isinstance(args, dict):
                            args = {}
                    
                        # Build header with any available metadata
                        display_header = chunk.get('display_header')
                        if not display_header:
                            # Derive header from args for file tools
                            normalized = tool_name.split('_', 1)[-1] if 'mcp_' in tool_name else tool_name
                            if normalized in ('file_read', 'file_write', 'file_list'):
                                path = args.get('path', '')
                                label = normalized.replace('_', ' ')
                                display_header = f"{label}: {path}" if path else label
                            else:
                                display_header = tool_name
                        header_parts = [display_header]
                        metadata = []
                    
                        # Extract common metadata patterns from args
                        if 'thoughtNumber' in args and 'totalThoughts' in args:
                            metadata.append(f"{args['thoughtNumber']}/{args['totalThoughts']}")
                    
                        if args.get('isRevision') and 'revisesThought' in args:
                            metadata.append(f"revises #{args['revisesThought']}")
                        elif 'branchId' in args:
                            metadata.append(f"branch: {args['branchId']}")
                    
                        if 'branchFromThought' in args:
                            metadata.append(f"from #{args['branchFromThought']}")
                    
                        # Add command if available (for shell tools)
                        if 'command' in args and isinstance(result, str) and not result.startswith('$ '):
                            metadata.append(f"$ {args['command']}")
                    
                        # For search tools, show the search query
                        if 'WorkspaceSearch' in tool_name or 'CodeSearch' in tool_name:
                            search_args = args.get('tool_input', args)
                            if isinstance(search_args, str):
                                try:
                                    search_args = json.loads(search_args)
                                except (json.JSONDecodeError, TypeError, ValueError):
                                    search_args = {}
                            if isinstance(search_args, dict):
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
                            if thought_text and isinstance(thought_text, str):
                                from app.utils.terminal_markdown import render_prefixed_markdown
                                render_prefixed_markdown(thought_text)
                    
                        # Print result (tool output/response)
                        if result and isinstance(result, str):
                            is_json_metadata = result.strip().startswith('{') and 'thoughtNumber' in result
                    
                            if not is_json_metadata and result != args.get('thought', ''):
                                from app.utils.terminal_markdown import render_prefixed_markdown
                                render_prefixed_markdown(result.rstrip('\n'))
                    
                        print(f"\033[36m└─\033[0m", flush=True)
                    except Exception as e:  # Intentionally broad: display errors must not crash the stream
                        # Log and continue — don't crash the stream for a display issue
                        logger.warning(f"Error rendering tool_display chunk: {e}")
                        tool_name = chunk.get('tool_name', 'unknown') if isinstance(chunk, dict) else 'unknown'
                        print(f"\n\033[33m⚠ Tool result from {tool_name} (display error)\033[0m", flush=True)
            
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
            # Preserve partial content and re-raise so ask() handles it uniformly
            self._partial_response = full_response
            raise
        
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
        """Simple invocation without tools, with cancellation support."""
        if stream:
            response = ""
            try:
                async for chunk in self.model.astream(messages):
                    if self._cancellation_requested:
                        print("\n\033[33m^C - Cancelled.\033[0m")
                        break
                    if isinstance(chunk, dict):
                        content = chunk.get('content', '')
                    else:
                        content = getattr(chunk, 'content', '')
                    
                    if isinstance(content, str):
                        print(content, end='', flush=True)
                        response += content
            except asyncio.CancelledError:
                # Preserve partial content and re-raise for consistent handling
                self._partial_response = response
                raise
            print()
            return response
        else:
            # Wrap the blocking ainvoke in a task so Ctrl+C can cancel it
            task = asyncio.create_task(self.model.ainvoke(messages))
            try:
                while not task.done():
                    if self._cancellation_requested:
                        task.cancel()
                        print("\n\033[33m^C - Cancelled.\033[0m")
                        raise asyncio.CancelledError("User cancelled operation")
                    # Poll every 200ms to stay responsive to cancellation
                    await asyncio.sleep(0.2)
                result = task.result()
                if isinstance(result, dict):
                    content = result.get('content', '')
                else:
                    content = getattr(result, 'content', str(result))
                print(content)
                return content
            except asyncio.CancelledError:
                task.cancel()
                # Preserve empty partial and re-raise for consistent handling
                self._partial_response = ""
                raise
    
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
            except (OSError, RuntimeError, asyncio.TimeoutError, json.JSONDecodeError, ValueError) as e:
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
        elif endpoint == "openai":
            print("\033[33mTo fix OpenAI credentials:\033[0m", file=sys.stderr)
            print("  • Set OPENAI_API_KEY: export OPENAI_API_KEY=sk-...", file=sys.stderr)
            print("  • Or set OPENAI_BASE_URL for a compatible local server", file=sys.stderr)
        elif endpoint == "anthropic":
            print("\033[33mTo fix Anthropic credentials:\033[0m", file=sys.stderr)
            print("  • Set ANTHROPIC_API_KEY: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
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
                # OSC 133;D then 133;A tells iTerm2 that any prior command has
                # finished and a new prompt is starting.  Without this, iTerm
                # keeps showing the tab activity spinner while we're idle.
                sys.stdout.write("\033]133;D\007\033]133;A\007"); sys.stdout.flush()
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
                if user_input.startswith('/') and not user_input.startswith('//'):
                    if not await self._handle_command(user_input):
                        break
                    continue
                
                # Regular message
                print()
                print("\033[90m⏳ Sending to model...\033[0m", file=sys.stderr)
                sys.stdout.write("\033]133;C\007"); sys.stdout.flush()
                try:
                    await self.ask(user_input)
                except asyncio.CancelledError:
                    # Operation was cancelled, continue the loop
                    pass
                sys.stdout.write("\033]133;D\007"); sys.stdout.flush()
                print("\033[90m[trace] ask() returned, looping to prompt\033[0m", file=sys.stderr)
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
  /suspend [name]  Save session and exit (resume later with /resume or --resume)
  /save [name]     Checkpoint the current session without exiting
  /resume          Restore a previous session's files and history
  /shell         Manage shell commands (session-local by default)
                 /shell add <cmd>   Add to allowlist
                 /shell rm <cmd>    Remove from allowlist
                 /shell yolo        Enable YOLO mode (session only)
                 /shell yolo off    Disable YOLO mode
                 /shell reset       Reset to defaults
                 /shell timeout <s> Set command timeout (0=none)
                 /shell git <op>    Allow a git operation (e.g. add, commit, push, all)
                 Append 'save' to persist: /shell add git save
  /clear         Clear conversation history
  /reset         Clear history, files, and all session state
  /tune <key> <val> Adjust session settings:
                 /tune iterations <n>  Max tool iterations (default: 200)
  /model <name>  Switch model
  /quit          Exit

\033[1mDiff Application:\033[0m
  When the AI provides code diffs, you'll be prompted to:
  [a]pply - Apply the diff to your files
  [A]pply all - Apply this and all remaining diffs
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

        elif command == '/shell':
            await self._handle_shell_command(arg)

        elif command == '/suspend':
            name = arg.strip() or None
            session_id = save_session(self, name=name)
            print(f"\033[32m✓ Session suspended: {session_id}\033[0m")
            print(f"\033[90m  Resume with: ziya chat --resume\033[0m")
            print(f"\033[90m  Or use /resume in any session\033[0m")
            return False

        elif command == '/save':
            name = arg.strip() or None
            try:
                session_id = save_session(self, name=name)
                label = f" ({name})" if name else ""
                print(f"\033[32m✓ Session checkpointed: {session_id}{label}\033[0m")
            except (OSError, ValueError) as e:
                print(f"\033[31mFailed to save: {e}\033[0m")
            return True

        elif command == '/resume':
            if arg:
                session_id = find_session_by_name(arg)
                if not session_id:
                    print(f"\033[33mNo session matching '{arg}'\033[0m")
                    return True
            else:
                session_id = await select_session()
            if session_id:
                try:
                    session_data = load_session(session_id)
                    self.files = session_data.get('files', [])
                    self.history = session_data.get('history', [])
                    self._session_start_time = session_data.get('start_time', session_data.get('timestamp'))
                    self._session_id = session_data.get('id', session_id)
                    self._session_name = session_data.get('name')
                    opener = session_data.get('opening_statement', '')
                    print(f"\033[32m✓ Session resumed\033[0m")
                    if opener:
                        print(f"  \033[90m\"{opener[:80]}\"\033[0m")
                    print(f"  Files: {len(self.files)}, Messages: {len(self.history)}")
                except FileNotFoundError as e:
                    print(f"\033[33m{e}\033[0m")
                except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
                    print(f"\033[31mFailed to resume: {e}\033[0m")
            else:
                print("\033[90mResume cancelled\033[0m")

        elif command in ['/model', '/m']:
            await self._handle_model_selection_async(arg)
        
        elif command == '/tune':
            self._handle_tune(arg)

        elif command == '/clear':
            count = len(self.history)
            self.history.clear()
            print(f"\033[32m✓ Cleared {count} messages from history\033[0m")

        elif command == '/reset':
            hist_count = len(self.history)
            file_count = len(self.files)
            self.history.clear()
            self.files.clear()
            self.conversation_id = f"cli_{os.getpid()}_{id(self)}"
            self._session_start_time = None
            self._session_id = None
            self._session_name = None
            self._session_shell_commands = None
            self._session_yolo = False
            self._session_timeout = None
            self._partial_response = ""
            print(f"\033[32m✓ Session reset\033[0m")
            print(f"\033[90m  Cleared {hist_count} messages, {file_count} files\033[0m")

        else:
            print(f"\033[90mUnknown command: {command}\033[0m")
        
        return True

    _TUNABLES = {
        'iterations': ('ZIYA_MAX_TOOL_ITERATIONS', '200', 'Max tool iterations per response'),
    }

    def _handle_tune(self, arg: str):
        """Handle /tune subcommands for session settings."""
        parts = arg.split() if arg else []
        if not parts:
            print("\033[1mTunable settings:\033[0m")
            for key, (env, default, desc) in self._TUNABLES.items():
                current = os.environ.get(env, default)
                print(f"  {key} = {current}  \033[90m({desc})\033[0m")
            print(f"\n\033[90mUsage: /tune <key> <value>\033[0m")
            return

        key = parts[0]
        if key not in self._TUNABLES:
            print(f"\033[31mUnknown tunable: {key}\033[0m")
            print(f"\033[90mAvailable: {', '.join(self._TUNABLES)}\033[0m")
            return

        env_var, default, desc = self._TUNABLES[key]
        if len(parts) < 2:
            current = os.environ.get(env_var, default)
            print(f"\033[90m{key} = {current}  ({desc})\033[0m")
            return

        try:
            n = int(parts[1])
            if n < 1:
                raise ValueError
            os.environ[env_var] = str(n)
            print(f"\033[32m✓ {key} = {n}\033[0m")
        except ValueError:
            print(f"\033[31m{key} requires a positive integer\033[0m")

    async def _handle_shell_command(self, arg: str):
        """Handle /shell subcommands for managing allowed shell commands."""
        from app.config.shell_config import (
            DEFAULT_SHELL_CONFIG,
            set_persisted_allowed_commands,
            reset_shell_config,
            get_default_shell_config,
        )

        parts = arg.split() if arg else []
        sub = parts[0] if parts else ''
        rest = arg[len(sub):].strip() if sub else ''

        # Split rest by commas if present, otherwise treat as space-separated tokens
        # But for add/rm: if no commas, treat entire rest as ONE command (e.g. "git add")
        has_commas = ',' in rest

        # Check for trailing 'save' keyword
        persist = rest.endswith(' save') or rest == 'save'
        if persist:
            rest = rest.rsplit(' save', 1)[0].strip() if rest.endswith(' save') else ''

        # Parse command list: commas = multiple commands, no commas = single command
        if has_commas:
            sub_args = [c.strip() for c in rest.split(',') if c.strip()]
        else:
            sub_args = [rest] if rest else []

        # /shell  or  /shell list
        if sub in ('', 'list', 'ls'):
            commands = self._get_session_commands()
            commands.sort()
            yolo = self._session_yolo

            if yolo:
                print("\033[1;33m⚠️  YOLO MODE — all commands allowed "
                      "(except sudo/vim/nano/emacs/systemctl)\033[0m\n")

            print(f"\033[1mAllowed shell commands ({len(commands)}):\033[0m")
            col_width = max(len(c) for c in commands) + 2 if commands else 20
            cols = max(1, 80 // col_width)
            for i in range(0, len(commands), cols):
                row = commands[i:i + cols]
                print("  " + "".join(c.ljust(col_width) for c in row))

            merged_config = get_default_shell_config()
            defaults = set(merged_config["allowedCommands"])
            base_defaults = set(DEFAULT_SHELL_CONFIG["allowedCommands"])
            plugin_commands = sorted(defaults - base_defaults)
            added = sorted(set(commands) - defaults)
            removed = sorted(base_defaults - set(commands))
            if plugin_commands:
                print(f"\n\033[34m🔌 From plugins: {', '.join(plugin_commands)}\033[0m")
            if added:
                print(f"\n\033[32m+ Custom: {', '.join(added)}\033[0m")
            if removed:
                print(f"\033[31m- Removed from defaults: {', '.join(removed)}\033[0m")
            return

        # /shell add <cmd> [cmd...] [save]
        if sub == 'add':
            if not sub_args:
                print("Usage: /shell add <command> [command...] [save]")
                return
            commands = self._get_session_commands()
            added = []
            for cmd in sub_args:
                if cmd not in commands:
                    commands.append(cmd)
                    added.append(cmd)
            if added:
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print(f"\033[32m✓ Added: {', '.join(added)}\033[0m")
                if persist:
                    print("\033[90m  (saved permanently)\033[0m")
                else:
                    print("\033[90m  (session only — add 'save' to persist)\033[0m")
                await self._restart_shell_server()
            else:
                print("Already in allowlist.")
            return

        # /shell rm|remove <cmd> [cmd...] [save]
        if sub in ('rm', 'remove'):
            if not sub_args:
                print("Usage: /shell rm <command> [command...] [save]")
                return
            commands = self._get_session_commands()
            removed = []
            for cmd in sub_args:
                if cmd in commands:
                    commands.remove(cmd)
                    removed.append(cmd)
            if removed:
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print(f"\033[31m✓ Removed: {', '.join(removed)}\033[0m")
                if persist:
                    print("\033[90m  (saved permanently)\033[0m")
                else:
                    print("\033[90m  (session only — add 'save' to persist)\033[0m")
                await self._restart_shell_server()
            else:
                print("Not in allowlist.")
            return

        # /shell yolo [off]  — always session-only
        if sub == 'yolo':
            if sub_args and sub_args[0] == 'off':
                self._session_yolo = False
                os.environ["ZIYA_YOLO_MODE"] = "false"
                print("\033[32m✓ YOLO mode disabled.\033[0m")
                await self._restart_shell_server()
                return

            if self._session_yolo:
                print("\033[33mYOLO mode is already enabled.\033[0m")
                print("Disable with: /shell yolo off")
                return

            print("\033[1;33m" + "=" * 50)
            print("  ⚠️   YOLO MODE — LIVING DANGEROUSLY")
            print("=" * 50 + "\033[0m")
            print("\nAllows the AI to run \033[1many\033[0m shell command.")
            print("Still blocked: sudo, vim, nano, emacs, systemctl\n")

            try:
                confirm = await asyncio.to_thread(
                    input, "\033[1mType 'yolo' to confirm: \033[0m"
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            if confirm.strip().lower() != 'yolo':
                print("Aborted.")
                return

            self._session_yolo = True
            os.environ["ZIYA_YOLO_MODE"] = "true"
            print("\n\033[1;33m🔥 YOLO mode enabled (this session only).\033[0m")
            print("Disable with: /shell yolo off")
            await self._restart_shell_server()
            return

        # /shell git <operation|all> [save]
        if sub == 'git':
            if not sub_args:
                print("\033[1mGit access control:\033[0m")
                print("  /shell git all          Allow ALL git operations")
                print("  /shell git add          Allow 'git add'")
                print("  /shell git commit       Allow 'git commit'")
                print("  /shell git push         Allow 'git push'")
                print("  /shell git safe         Reset to safe (read-only) git ops")
                print("  Append 'save' to persist across sessions.")
                return
            op = sub_args[0]
            commands = self._get_session_commands()
            if op == 'all':
                # Add bare 'git' which the shell server treats as "allow all"
                if 'git' not in commands:
                    commands.append('git')
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print("\033[33m✓ All git operations enabled.\033[0m")
            elif op == 'safe':
                # Remove bare 'git' and any explicit git subcommands
                commands = [c for c in commands if c != 'git' and not c.startswith('git ')]
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print("\033[32m✓ Git reset to safe (read-only) operations.\033[0m")
            else:
                # Add specific git subcommand
                entry = f"git {op}"
                if entry not in commands:
                    commands.append(entry)
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print(f"\033[32m✓ 'git {op}' enabled.\033[0m")
            if persist:
                print("\033[90m  (saved permanently)\033[0m")
            else:
                print("\033[90m  (session only — add 'save' to persist)\033[0m")
            await self._restart_shell_server()
            return

        # /shell reset [save]
        if sub == 'reset':
            merged_config = get_default_shell_config()
            self._session_shell_commands = merged_config["allowedCommands"].copy()
            self._session_yolo = False
            os.environ["ZIYA_YOLO_MODE"] = "false"
            print(f"\033[32m✓ Shell config reset to defaults ({n} commands, YOLO off)\033[0m")
            if persist:
                print("\033[90m  (saved permanently)\033[0m")
            else:
                print("\033[90m  (session only — add 'save' to persist)\033[0m")
            await self._restart_shell_server()
            return

        # /shell timeout <seconds> [save]
        if sub == 'timeout':
            if not sub_args:
                current = self._session_timeout
                if current is None:
                    from app.config.shell_config import _read_mcp_config
                    cfg = _read_mcp_config()
                    current = int(cfg.get("mcpServers", {}).get("shell", {}).get("env", {}).get("COMMAND_TIMEOUT", "30"))
                if current == 0:
                    print(f"\033[1mCommand timeout: disabled (no limit)\033[0m")
                else:
                    print(f"\033[1mCommand timeout: {current}s\033[0m")
                return
            try:
                val = int(sub_args[0])
                if val < 0:
                    raise ValueError
            except ValueError:
                print("Usage: /shell timeout <seconds>  (0 to disable)")
                return
            self._session_timeout = val
            if persist:
                from app.config.shell_config import _read_mcp_config, _ensure_shell_env, _write_mcp_config
                cfg = _read_mcp_config()
                env = _ensure_shell_env(cfg)
                env["COMMAND_TIMEOUT"] = str(val)
                _write_mcp_config(cfg)
            if val == 0:
                print("\033[33m✓ Command timeout disabled (no limit)\033[0m")
            else:
                print(f"\033[32m✓ Command timeout: {val}s\033[0m")
            if persist:
                print("\033[90m  (saved permanently)\033[0m")
            else:
                print("\033[90m  (session only — add 'save' to persist)\033[0m")
            await self._restart_shell_server()
            return

        print(f"\033[90mUnknown: /shell {sub}\033[0m")
        print("Usage: /shell [list|add|rm|yolo|git|timeout|reset]")
        print("  Append 'save' to persist changes across sessions.")

    def _get_session_commands(self) -> list:
        """Get the effective command list (session override or persisted)."""
        if self._session_shell_commands is not None:
            return self._session_shell_commands.copy()
        from app.config.shell_config import get_persisted_allowed_commands
        return get_persisted_allowed_commands()

    async def _restart_shell_server(self):
        """Restart the shell MCP server with current session state."""
        try:
            from app.mcp.manager import get_mcp_manager
            from app.config.shell_config import _read_mcp_config
            mcp_manager = get_mcp_manager()
            if mcp_manager and mcp_manager.is_initialized:
                # Start from the manager's builtin config (correct absolute
                # command/args paths).  Never take command/args from
                # mcp_config.json — those may contain stale relative paths
                # that don't resolve from the current working directory.
                shell_cfg = dict(mcp_manager.server_configs.get("shell", {}))
                # Layer persisted env customizations on top
                cfg = _read_mcp_config()
                persisted_env = cfg.get("mcpServers", {}).get("shell", {}).get("env", {})
                env = {**persisted_env, **shell_cfg.get("env", {})}
                # Apply session-local overrides
                env["ALLOW_COMMANDS"] = ",".join(self._get_session_commands())
                env["YOLO_MODE"] = "true" if self._session_yolo else "false"
                if self._session_timeout is not None:
                    env["COMMAND_TIMEOUT"] = str(self._session_timeout)
                shell_cfg["env"] = env
                ok = await mcp_manager.restart_server("shell", shell_cfg)
                if ok:
                    print("\033[32m✓ Shell server restarted — changes are live.\033[0m")
                    return
            print("\033[2mRestart Ziya session for changes to take effect.\033[0m")
        except (ImportError, OSError, RuntimeError, asyncio.TimeoutError) as e:
            print(f"\033[2mRestart Ziya session for changes to take effect. ({e})\033[0m")

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
        
        # Thinking effort if model supports adaptive thinking
        if model_config.get('supports_adaptive_thinking'):
            valid_efforts = model_config.get('supported_efforts', ['low', 'medium', 'high', 'max'])
            default_effort = model_config.get('thinking_effort_default', 'medium')
            current_effort = current_settings.get('thinking_effort') or os.environ.get('ZIYA_THINKING_EFFORT') or default_effort
            effort_input = input(f"Thinking Effort [{'/'.join(valid_efforts)}] (current: {current_effort}): ").strip().lower()
            if effort_input:
                if effort_input in valid_efforts:
                    settings['thinking_effort'] = effort_input
                else:
                    print(f"\033[33mInvalid choice, using {current_effort}\033[0m")
                    settings['thinking_effort'] = current_effort
            else:
                settings['thinking_effort'] = current_effort

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
                    # Check if anything actually changed
                    model_changed = selected_model != current_model
                    settings_changed = False
                    for key, value in settings.items():
                        env_key = f"ZIYA_{key.upper()}"
                        old_value = os.environ.get(env_key)
                        new_value = str(value)
                        if old_value != new_value:
                            settings_changed = True
                            break

                    if not model_changed and not settings_changed:
                        print(f"\n\033[90mSettings unchanged for {selected_model}\033[0m")
                        return

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
        except (EOFError, KeyboardInterrupt, OSError, RuntimeError, ValueError) as e:
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
    except (ImportError, OSError, RuntimeError, asyncio.TimeoutError) as e:
        print(f"\033[90mMCP initialization skipped: {e}\033[0m", file=sys.stderr)


async def _run_with_mcp(coro):
    """Initialize MCP servers then run the given coroutine in the same event loop.

    Avoids the double-asyncio.run() bug where the first run tears down MCP connections.
    """
    await _initialize_mcp()
    return await coro


async def _run_async_cli(cli):
    """Run CLI in async context with MCP initialized."""
    # Initialize MCP in this event loop
    await _initialize_mcp()

    # Install a custom SIGINT handler on the event loop so that ^C during
    # streaming cancels the active task gracefully instead of tearing down
    # the entire event loop (which is what the default KeyboardInterrupt
    # propagation through asyncio.run() does).
    loop = asyncio.get_running_loop()

    def _sigint_handler():
        if cli._active_task and not cli._active_task.done():
            # Mid-stream: request cancellation and cancel the task
            cli._cancellation_requested = True
            cli._active_task.cancel()
        else:
            # At the prompt the terminal is in raw mode, so SIGINT
            # won't fire — prompt_toolkit handles ^C as a character.
            # Nothing to do here; keep the handler installed to
            # prevent the default asyncio SIGINT teardown.
            pass

    try:
        loop.add_signal_handler(signal.SIGINT, _sigint_handler)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler; fall back to
        # default behavior (KeyboardInterrupt).
        pass

    await cli.chat()


# ============================================================================
# Session factory — single source of truth for init → auth → CLI creation
# ============================================================================

def _init_and_authenticate(args, *, skip_setup_env: bool = False):
    """Common initialisation: environment setup, plugin loading, and auth check.

    Exits the process with a clear error message if authentication fails.

    Args:
        args: Parsed CLI arguments.
        skip_setup_env: Set ``True`` if ``setup_env(args)`` was already called
            (e.g. when the handler needs env set up before an early-exit path).
    """
    if not skip_setup_env:
        setup_env(args)

    from app.plugins import initialize as initialize_plugins
    initialize_plugins()

    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)


def _create_cli_session(args, files=None) -> 'CLI':
    """Perform full init, authenticate, resolve files, and return a CLI instance.

    This is the canonical entry point for command handlers that follow the
    standard setup_env → plugins → auth → resolve_files → CLI() sequence.
    """
    _init_and_authenticate(args)
    if files is None:
        root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        files = resolve_files(args.files, root) if getattr(args, 'files', None) else []
    return CLI(files=files)


# ============================================================================
# Command handlers
# ============================================================================

def cmd_chat(args):
    """Handle: ziya chat [FILES...]"""
    # Environment + plugins needed before --resume early path
    setup_env(args)
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Handle session resume (needs env + plugins but also needs auth)
    if getattr(args, 'resume', False):
        # Authenticate on resume path — fixes historical auth bypass
        profile = getattr(args, 'profile', None)
        if not _check_auth_quick(profile):
            _print_auth_error()
            sys.exit(1)

        resume_arg = args.resume if isinstance(args.resume, str) else None
        if resume_arg:
            session_id = find_session_by_name(resume_arg)
            if not session_id:
                print(f"\033[33mNo session matching '{resume_arg}'\033[0m")
                sys.exit(1)
        else:
            session_id = asyncio.run(select_session())
        if session_id:
            try:
                session_data = load_session(session_id)
                files = session_data.get('files', [])
                history = session_data.get('history', [])
                
                cli = CLI(files=files)
                cli.history = history
                cli._session_start_time = session_data.get('start_time', session_data.get('timestamp'))
                cli._session_id = session_data.get('id', session_id)
                cli._session_name = session_data.get('name')
                
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
    
    # Normal (non-resume) path — skip setup_env/plugins (already ran above)
    _init_and_authenticate(args, skip_setup_env=True)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    files = resolve_files(args.files, root) if args.files else []
    cli = CLI(files=files)
    asyncio.run(_run_async_cli(cli))
    
    if not getattr(args, 'ephemeral', False):
        save_session(cli)
        print(f"\n\033[90mSession saved\033[0m")


def cmd_ask(args):
    """Handle: ziya ask "question" [FILES...]"""
    cli = _create_cli_session(args)
    
    # Build question from args and stdin
    question = args.question
    
    # Check for piped input
    stdin_content = read_stdin_if_available()
    if stdin_content:
        question = f"{question}\n\n```\n{stdin_content}\n```" if question else stdin_content
    
    if not question:
        print("Error: No question provided", file=sys.stderr)
        sys.exit(1)
    
    asyncio.run(_run_with_mcp(cli.ask(question, stream=not args.no_stream)))


def cmd_review(args):
    """Handle: ziya review [FILES...] [--staged]"""
    cli = _create_cli_session(args)
    print_chat_startup_info(args)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    
    # Get content to review
    content = None
    
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
    
    prompt = args.prompt or "Review this code. Focus on bugs, security issues, and improvements."
    
    if content:
        question = f"{prompt}\n\n```\n{content}\n```"
    else:
        question = prompt
    
    asyncio.run(_run_with_mcp(cli.ask(question, stream=not args.no_stream)))


def cmd_explain(args):
    """Handle: ziya explain [FILES...]"""
    cli = _create_cli_session(args)
    
    content = read_stdin_if_available()
    prompt = args.prompt or "Explain this code clearly and concisely."
    
    if content:
        question = f"{prompt}\n\n```\n{content}\n```"
    else:
        question = prompt
    
    asyncio.run(_run_with_mcp(cli.ask(question, stream=not args.no_stream)))


def cmd_task(args):
    """Handle: ziya task <name> [--list] [--show TASK]"""
    from app.task_runner import (
        load_tasks, validate_task_allow,
        apply_task_permissions, restore_permissions,
    )
    setup_env(args)  # needed for root dir before --list/--show early exits
    setup_env(args)
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    tasks = load_tasks(root)

    # --list: print available tasks and exit
    if getattr(args, 'list_tasks', False):
        if not tasks:
            print("No tasks defined.")
            print("Create ~/.ziya/tasks.yaml or .ziya/tasks.yaml")
            return
        max_name = max(len(n) for n in tasks)
        print(f"\033[1mAvailable tasks:\033[0m\n")
        for name in sorted(tasks):
            desc = tasks[name].get("description", "")
            print(f"  \033[36m{name:<{max_name}}\033[0m  {desc}")
        print(f"\nRun: ziya task <name>")
        return

    # --show: print task prompt and exit
    if getattr(args, 'show', None):
        task_name = args.show
        if task_name not in tasks:
            print(f"\033[31mUnknown task: {task_name}\033[0m", file=sys.stderr)
            sys.exit(1)
        task = tasks[task_name]
        print(f"\033[1m{task_name}\033[0m: {task.get('description', '')}\n")
        print(task.get("prompt", "(no prompt)"))
        return

    # Running a task requires a name
    task_name = getattr(args, 'task_name', None)
    if not task_name:
        args.list_tasks = True
        cmd_task(args)
        return

    if task_name not in tasks:
        print(f"\033[31mUnknown task: {task_name}\033[0m", file=sys.stderr)
        print(f"Run \033[36mziya task --list\033[0m to see available tasks.", file=sys.stderr)
        sys.exit(1)

    task_def = tasks[task_name]

    # Validate and apply escalated permissions BEFORE MCP init
    errors = validate_task_allow(task_def)
    if errors:
        print(f"\033[31mTask '{task_name}' has invalid allow block:\033[0m", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        sys.exit(1)

    saved_env = apply_task_permissions(task_def)
    if saved_env:
        allow = task_def.get("allow", {})
        parts = []
        if allow.get("commands"):
            parts.append(f"commands: {', '.join(allow['commands'])}")
        if allow.get("git_operations"):
            parts.append(f"git: {', '.join(allow['git_operations'])}")
        if allow.get("write_patterns"):
            parts.append(f"write: {', '.join(allow['write_patterns'])}")
        print(f"\033[33m⚡ Escalated permissions: {'; '.join(parts)}\033[0m", file=sys.stderr)

    try:
        # Full init for actual task execution (setup_env already called above)
        _init_and_authenticate(args, skip_setup_env=True)

        cli = CLI(files=[])  # Tasks don't use file context
        asyncio.run(_run_with_mcp(cli.ask(task_def["prompt"], stream=not args.no_stream)))
    finally:
        restore_permissions(saved_env)

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
        except (ImportError, OSError, RuntimeError, ValueError):
            return False
    elif endpoint == "google":
        return bool(os.environ.get("GOOGLE_API_KEY"))
    elif endpoint == "openai":
        return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"))
    elif endpoint == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    
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
    elif endpoint == "openai":
        print("OPENAI_API_KEY environment variable is not set.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  export OPENAI_API_KEY=sk-...", file=sys.stderr)
    elif endpoint == "anthropic":
        print("ANTHROPIC_API_KEY environment variable is not set.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        print("  # or for a compatible local server:", file=sys.stderr)
        print("  export OPENAI_BASE_URL=http://localhost:8080/v1", file=sys.stderr)
    
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
    chat_parser.add_argument('--resume', nargs='?', const=True, default=False, metavar='NAME',
                             help='Resume a session; optional NAME/id to skip the picker')
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
    
    # task
    task_parser = subparsers.add_parser('task', parents=[common_parent],
                                        help='Run a named task prompt')
    task_parser.add_argument('task_name', nargs='?', help='Task to run')
    task_parser.add_argument('--list', '-l', action='store_true',
                             dest='list_tasks', help='List available tasks')
    task_parser.add_argument('--show', metavar='TASK',
                             help='Show the prompt for a task')
    task_parser.set_defaults(func=cmd_task)

    return parser
    
    


def main():
    """CLI entry point."""
    parser = create_parser()
    
    # Save current terminal title and set ours (xterm title stack push/pop)
    sys.stdout.write("\033[22;0t")
    sys.stdout.write("\033]0;Ziya Chat\007")
    sys.stdout.flush()
    
    # Pre-process argv to support flags both before and after subcommand
    # e.g., "ziya --profile x chat" -> "ziya chat --profile x"
    argv = sys.argv[1:]
    commands = {'chat', 'ask', 'review', 'explain', 'task'}
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
        sys.stdout.write("\033[23;0t")
        sys.stdout.flush()
        print()
        sys.exit(0)
    except Exception as e:  # Intentionally broad: top-level CLI error handler
        # Extract traceback info for better error reporting
        tb = traceback.extract_tb(sys.exc_info()[2])
        sys.stdout.write("\033[23;0t")
        sys.stdout.flush()
        if tb:
            last_frame = tb[-1]
            location = f"{last_frame.filename}:{last_frame.lineno}"
            print(f"\033[31mError in {location}: {e}\033[0m", file=sys.stderr)
            if os.environ.get('ZIYA_LOG_LEVEL') == 'DEBUG':
                print("\nFull traceback:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
        else:
            print(f"\033[31mError: {e}\033[0m", file=sys.stderr)
        sys.stdout.write("\033[23;0t")
        sys.stdout.flush()
        sys.exit(1)


    # Restore terminal title on clean exit
    sys.stdout.write("\033[23;0t")
    sys.stdout.flush()

if __name__ == '__main__':
    main()

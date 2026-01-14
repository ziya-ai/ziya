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

import os
import sys
import asyncio
import re
import hashlib
import argparse
from pathlib import Path
from typing import Optional, List, Tuple 
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import PathCompleter, WordCompleter, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import FormattedText


def setup_env(args):
    """Minimal environment setup for CLI mode."""
    # Set root directory
    root = getattr(args, 'root', None) or os.getcwd()
    os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", root)
    
    # Model settings
    if getattr(args, 'model', None):
        os.environ["ZIYA_MODEL"] = args.model
    
    # AWS settings - set BOTH env vars for compatibility
    if getattr(args, 'profile', None):
        os.environ["ZIYA_AWS_PROFILE"] = args.profile
        os.environ["AWS_PROFILE"] = args.profile
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
        self._setup_prompt_session()
    
    @property
    def model(self):
        """Lazy-load model on first use."""
        if self._model is None:
            self._model = self._initialize_model()
        return self._model
    
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
        # Custom completer that only shows commands at line start
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
                ], ignore_case=True, sentence=True)
                
                self.path_completer = PathCompleter(
                    only_directories=False,
                    expanduser=True
                )
            
            def get_completions(self, document: Document, complete_event):
                text = document.text_before_cursor
                
                # Only show command completions at the start of the line
                if text.lstrip().startswith('/'):
                    # Check if we're still typing the command (no space after /)
                    if ' ' not in text.lstrip():
                        yield from self.command_completer.get_completions(document, complete_event)
                        return
                
                # Otherwise, show path completions
                yield from self.path_completer.get_completions(document, complete_event)
        
        """Set up prompt_toolkit session with history and completions."""
        # History file in user's home directory
        history_file = Path.home() / '.ziya' / 'history'
        history_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Command completer for slash commands
        command_completer = WordCompleter([
            '/add', '/a',
            '/rm', '/remove',
            '/files', '/ls', '/f',
            '/clear', '/c',
            '/model', '/m',
            '/quit', '/q', '/exit',
            '/help', '/h'
        ], ignore_case=True)
        
        # Key bindings for ^C handling
        bindings = KeyBindings()
        
        @bindings.add('c-c')
        def _(event):
            """Handle Ctrl+C - cancel operation or clear input."""
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
        
        self.session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=SmartCompleter(),
            complete_while_typing=True,
            key_bindings=bindings
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
        
        try:
            response = await self._run_with_tools(question, stream)
        except Exception as e:
            error_str = str(e)
            print(f"\n\033[31mError: {error_str}\033[0m", file=sys.stderr)
            
            # Check for specific error types
            if 'ThrottlingException' in error_str or 'Too many tokens' in error_str:
                print("\033[33mRate limit hit. Please wait a moment before trying again.\033[0m", file=sys.stderr)
            elif 'ExpiredToken' in error_str:
                print("\033[33mCredentials expired. Please refresh: aws sso login --profile <profile>\033[0m", file=sys.stderr)
            elif isinstance(e, asyncio.CancelledError):
                # Graceful cancellation
                print("\n\033[33mOperation cancelled.\033[0m")
                return ""
            
            return ""
        
        # Update history
        self.history.append({'type': 'human', 'content': question})
        if response:
            self.history.append({'type': 'ai', 'content': response})
        
        return response
    
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
    
    async def _stream_handler(self, stream_generator, stream: bool) -> str:
        """Handle streaming with cancellation support."""
        full_response = ""
        
        async for chunk in stream_generator:
            chunk_type = chunk.get('type')
            
            if chunk_type == 'text':
                content = chunk.get('content', '')
                if stream:
                    print(content, end='', flush=True)
                full_response += content
            
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
                
                # Print tool header in cyan
                print(f"\n\033[36m┌─ {tool_name}\033[0m", flush=True)
                
                # Print command/args if available
                if 'command' in args:
                    print(f"\033[90m│ $ {args['command']}\033[0m", flush=True)
                
                # Print result
                if result:
                    for line in result.split('\n'):
                        print(f"\033[90m│\033[0m {line}", flush=True)
                
                print(f"\033[36m└─\033[0m", flush=True)
            
            elif chunk_type == 'stream_end':
                break
            
            elif chunk_type == 'throttling_error':
                # Handle throttling gracefully
                wait_time = chunk.get('suggested_wait', 60)
                print(f"\n\033[33m⚠ Rate limit hit. Waiting {wait_time}s...\033[0m\n", file=sys.stderr)
                # Don't break - let the executor handle the retry
            
            elif chunk_type == 'error':
                error_msg = chunk.get('content', 'Unknown error')
                print(f"\n\033[31mError: {error_msg}\033[0m", file=sys.stderr)
                break
        
        if stream:
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
                    result = await manager.execute_tool(tool_name, arguments)
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
        # Check model availability before starting
        if self.model is None:
            print(f"\n\033[31mError: {self._init_error or 'Model not available'}\033[0m", file=sys.stderr)
            self._print_auth_help()
            return
        
        print(f"\033[90mZiya CLI • {len(self.files)} files in context • /help for commands\033[0m\n")
        
        while True:
            try:
                # Use prompt_toolkit for rich input
                try:
                    user_input = await asyncio.to_thread(
                        self.session.prompt,
                        FormattedText([('bold cyan', '> ')]),
                        # Add context-aware completion
                        refresh_interval=0.5
                    )
                    user_input = user_input.strip()
                except KeyboardInterrupt:
                    # Ctrl+C on empty prompt - continue
                    print()
                    continue
                
                if not user_input:
                    continue
                
                # Commands
                if user_input.startswith('/'):
                    if not self._handle_command(user_input):
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
    
    def _handle_command(self, cmd: str) -> bool:
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
        
        elif command in ['/rm', '/remove']:
            if arg:
                before = len(self.files)
                self.files = [f for f in self.files if arg not in f]
                print(f"\033[90mRemoved {before - len(self.files)} files\033[0m")
            else:
                print("Usage: /rm <path>")
        
        elif command in ['/files', '/ls', '/f']:
            if self.files:
                print(f"\033[90m{len(self.files)} files:\033[0m")
                for f in self.files[:20]:
                    print(f"  {f}")
                if len(self.files) > 20:
                    print(f"  ... and {len(self.files) - 20} more")
            else:
                print("\033[90mNo files in context\033[0m")
        
        elif command in ['/clear', '/c']:
            self.history = []
            print("\033[90mHistory cleared\033[0m")
        
        elif command in ['/model', '/m']:
            if arg:
                os.environ["ZIYA_MODEL"] = arg
                self._model = None  # Force reload
                print(f"\033[90mSwitched to {arg}\033[0m")
            else:
                from app.agents.models import ModelManager
                print(f"\033[90mCurrent: {ModelManager.get_model_alias()}\033[0m")
        
        else:
            print(f"\033[90mUnknown command: {command}\033[0m")
        
        return True



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
    
    # Now check auth with the profile from setup_env
    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    files = resolve_files(args.files, root) if args.files else []
    
    cli = CLI(files=files)
    asyncio.run(_run_async_cli(cli))


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
    
    # Global options
    parser.add_argument('--model', '-m', help='Model to use')
    parser.add_argument('--profile', help='AWS profile')
    parser.add_argument('--region', help='AWS region')
    parser.add_argument('--root', help='Root directory (default: cwd)')
    parser.add_argument('--no-stream', action='store_true', help='Disable streaming output')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # chat - needs to accept global args too
    chat_parser = subparsers.add_parser('chat', help='Interactive chat')
    chat_parser.add_argument('files', nargs='*', help='Files/directories for context')
    chat_parser.set_defaults(func=cmd_chat)
    
    # ask
    ask_parser = subparsers.add_parser('ask', help='Ask a question')
    ask_parser.add_argument('question', nargs='?', help='Question to ask')
    ask_parser.add_argument('files', nargs='*', help='Files for context')
    ask_parser.set_defaults(func=cmd_ask)
    
    # review
    review_parser = subparsers.add_parser('review', help='Review code')
    review_parser.add_argument('files', nargs='*', help='Files to review')
    review_parser.add_argument('--staged', '-s', action='store_true', help='Review staged git changes')
    review_parser.add_argument('--diff', '-d', action='store_true', help='Review unstaged git changes')
    review_parser.add_argument('--prompt', '-p', help='Custom review prompt')
    review_parser.set_defaults(func=cmd_review)
    
    # explain  
    explain_parser = subparsers.add_parser('explain', help='Explain code')
    explain_parser.add_argument('files', nargs='*', help='Files to explain')
    explain_parser.add_argument('--prompt', '-p', help='Custom prompt')
    explain_parser.set_defaults(func=cmd_explain)
    
    return parser


# Keep for backwards compat but don't use - causes argparse conflicts
# def _add_common_args(parser):
#     """Add common arguments to a subparser."""
#     parser.add_argument('--model', '-m', help='Model to use')
#     parser.add_argument('--profile', help='AWS profile')
#     parser.add_argument('--region', help='AWS region')
#     parser.add_argument('--root', help='Root directory (default: cwd)')
#     parser.add_argument('--no-stream', action='store_true', help='Disable streaming output')


def main():
    """CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()
    
    # Debug what we got
    import sys
    print(f"DEBUG: args.profile = {getattr(args, 'profile', None)}", file=sys.stderr)
    print(f"DEBUG: args.command = {getattr(args, 'command', None)}", file=sys.stderr)
    
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
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

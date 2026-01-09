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
from typing import Optional, List, Tuple 


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
        
        response = await self._run_with_tools(question, stream)
        
        # Update history
        self.history.append({'type': 'human', 'content': question})
        self.history.append({'type': 'ai', 'content': response})
        
        return response
    
    async def _run_with_tools(self, question: str, stream: bool = True) -> str:
        """Run model with tool execution loop."""
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        messages = self._build_messages(question)
        full_response = ""
        max_iterations = 15  # Prevent infinite loops
        iteration = 0
        processed_tool_calls = set()
        
        while iteration < max_iterations:
            iteration += 1
            current_response = ""
            printed_len = 0
            in_tool_block = False
            
            # Stream response
            if stream:
                async for chunk in self.model.astream(messages):
                    content = getattr(chunk, 'content', '')
                    if isinstance(content, str) and content:
                        current_response += content
                        
                        # Track if we're in a tool block
                        if TOOL_SENTINEL_OPEN in current_response:
                            in_tool_block = TOOL_SENTINEL_CLOSE not in current_response.split(TOOL_SENTINEL_OPEN)[-1]
                        
                        # Print content that's not in a tool block
                        if not in_tool_block:
                            # Find safe content to print (before any tool block)
                            safe_content = current_response
                            if TOOL_SENTINEL_OPEN in safe_content:
                                safe_content = safe_content.split(TOOL_SENTINEL_OPEN)[0]
                            
                            # Print new content only
                            if len(safe_content) > printed_len:
                                new_content = safe_content[printed_len:]
                                print(new_content, end='', flush=True)
                                printed_len = len(safe_content)
            else:
                result = self.model.invoke(messages)
                current_response = getattr(result, 'content', str(result))
            
            # Check for and execute tool calls
            tool_result = await self._execute_tool_calls(
                current_response, processed_tool_calls, TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
            )
            
            # Debug: show raw response if it contains tool sentinel
            has_open = TOOL_SENTINEL_OPEN in current_response
            has_close = TOOL_SENTINEL_CLOSE in current_response
            if has_open:
                print(f"\n\033[33mDEBUG: has_open={has_open}, has_close={has_close}\033[0m", file=sys.stderr)
                print(f"\033[90mDEBUG: last 300 chars: {repr(current_response[-300:])}\033[0m", file=sys.stderr)
            
            if tool_result is None:
                # No more tool calls - print any remaining content and finish
                if stream:
                    # Print anything after the last tool block
                    clean = self._remove_tool_blocks(current_response, TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE)
                    if len(clean) > printed_len:
                        print(clean[printed_len:], end='', flush=True)
                    print()  # Final newline
                else:
                    print(self._remove_tool_blocks(current_response, TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE))
                
                full_response += current_response
                break
            
            tool_call_block, tool_name, tool_output = tool_result
            
            # Show tool execution status
            print(f"\n\033[90m⚡ {tool_name}\033[0m", flush=True)
            
            # Add to conversation for continuation
            full_response += current_response
            messages.append({"role": "assistant", "content": current_response})
            messages.append({"role": "user", "content": f"<tool_result>\n{tool_output}\n</tool_result>\n\nContinue based on this result."})
        
        return self._remove_tool_blocks(full_response, TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE)
    
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
                user_input = input("\033[1m>\033[0m ").strip()
                if not user_input:
                    continue
                
                # Commands
                if user_input.startswith('/'):
                    if not self._handle_command(user_input):
                        break
                    continue
                
                # Regular message
                print()
                await self.ask(user_input)
                print()
                
            except KeyboardInterrupt:
                print("\n")
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


# ============================================================================
# Command handlers
# ============================================================================

def cmd_chat(args):
    """Handle: ziya chat [FILES...]"""
    # Debug: show what args we received
    print(f"DEBUG args: profile={getattr(args, 'profile', 'NOT SET')}", file=sys.stderr)
    
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Check model availability early with helpful error (after setup_env sets AWS_PROFILE)
    profile = getattr(args, 'profile', None)
    if not _check_auth_quick(profile):
        _print_auth_error()
        sys.exit(1)
    
    root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    files = resolve_files(args.files, root) if args.files else []
    
    # Initialize MCP servers
    asyncio.run(_initialize_mcp())
    
    cli = CLI(files=files)
    asyncio.run(cli.chat())


def cmd_ask(args):
    """Handle: ziya ask "question" [FILES...]"""
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Quick auth check before doing work (after setup_env sets AWS_PROFILE)
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
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Quick auth check before doing work (after setup_env sets AWS_PROFILE)
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
    setup_env(args)
    
    # Initialize plugins BEFORE auth check - needed for Amazon auth provider
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    
    # Quick auth check before doing work (after setup_env sets AWS_PROFILE)
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

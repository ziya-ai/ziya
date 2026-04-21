#!/usr/bin/env python3
"""
MCP server that provides shell command execution functionality.
"""

import asyncio
import json
import subprocess
import sys
import os
import re
import glob
import time
import shlex
from typing import Dict, Any, Optional

# Shell keywords that begin compound constructs requiring a shell interpreter
_COMPOUND_STARTERS = frozenset({'for', 'while', 'until', 'if', 'case', 'select'})

# All shell keywords (structural tokens that are never standalone executables)
_SHELL_KEYWORDS = frozenset({
    'for', 'while', 'until', 'if', 'then', 'else', 'elif', 'fi',
    'do', 'done', 'case', 'esac', 'in', 'select',
    'function', 'return', 'break', 'continue',
})

# Import centralized shell configuration
# Go up two levels: shell_server.py -> mcp_servers/ -> app/ -> site-packages (or project root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.config.shell_config import get_default_shell_config
from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker


class ShellServer:
    """MCP server that provides shell command execution tools."""
    
    def __init__(self):
        self.request_id = 0
        
        # Yolo mode — bypass command allowlist (except always_blocked)
        self.yolo_mode = os.environ.get('YOLO_MODE', 'false').lower() in ('true', '1', 'yes')

        # Use centralized configuration (merged with plugin provider additions)
        self._effective_config = get_default_shell_config()
        self.allowed_commands = self._effective_config["allowedCommands"].copy()

        # Write policy
        self.wp_manager = WritePolicyManager()
        self.wp_manager.merge_env_overrides(dict(os.environ))
        self.write_checker = ShellWriteChecker(self.wp_manager)

        # Get configuration from environment
        self.git_operations_enabled = os.environ.get('GIT_OPERATIONS_ENABLED', 'true').lower() in ('true', '1', 'yes')
        self.command_timeout = int(os.environ.get('COMMAND_TIMEOUT', '30'))  # Increased from 10 to 30 seconds
        
        # Hard ceiling for model-requested timeouts (matches TOOL_EXEC_TIMEOUT in streaming_tool_executor)
        self.max_timeout = int(os.environ.get('MAX_COMMAND_TIMEOUT', '300'))
        
        # Default pattern for commands: command name followed by optional arguments
        self.default_command_pattern = r"^{cmd}(\s+.*)?$"
        
        # Command pattern overrides for commands that need special handling
        self.command_pattern_overrides = {
            # Add any special pattern overrides here if needed
        }
        
        # Get additional allowed commands from environment (legacy support)
        env_commands = os.environ.get('ALLOW_COMMANDS', '').split(',')
        env_commands = [cmd.strip() for cmd in env_commands if cmd.strip()]
        
        # Add environment commands to allowed commands list
        for cmd in env_commands:
            if cmd and cmd not in self.allowed_commands:
                self.allowed_commands.append(cmd)

        # Add destructive commands (rm, mv, cp, mkdir, etc.) to the allowlist
        # so they pass the command-name gate.  The write policy checker
        # (ShellWriteChecker._destructive) validates their *target paths*
        # against safe_write_paths / allowed_write_patterns, so these are
        # only permitted when operating on declared-safe areas.
        for cmd in self.wp_manager.policy.get('destructive_commands', []):
            if cmd not in self.allowed_commands:
                self.allowed_commands.append(cmd)

        # Add interpreters to command allowlist
        for interp in self.wp_manager.policy.get('allowed_interpreters', []):
            if interp not in self.allowed_commands:
                self.allowed_commands.append(interp)

        # Build safe command patterns dynamically from allowed commands
        self.safe_command_patterns = self._build_safe_command_patterns()

        # Add git operations if enabled
        if self.git_operations_enabled:
            safe_git_ops = os.environ.get('SAFE_GIT_OPERATIONS', 'status,log,show,diff,branch,remote,ls-files,blame').split(',')
            safe_git_ops = [op.strip() for op in safe_git_ops if op.strip()]
            
            self.git_patterns = {
                'status': r'^git\s+status(\s+.*)?$',
                'log': r'^git\s+log(\s+.*)?$',
                'show': r'^git\s+show(\s+.*)?$',
                'diff': r'^git\s+diff(\s+.*)?$',
                'branch': r'^git\s+branch(\s+(?!-[dD]|--delete).*)?$',  # Allow branch listing, not deletion
                'remote': r'^git\s+remote(\s+(?!rm|remove).*)?$',  # Allow remote listing, not removal
                'config --get': r'^git\s+config\s+--get(\s+.*)?$',  # Only allow getting config, not setting
                'ls-files': r'^git\s+ls-files(\s+.*)?$',
                'ls-tree': r'^git\s+ls-tree(\s+.*)?$',
                'blame': r'^git\s+blame(\s+.*)?$',
                'tag': r'^git\s+tag(\s+(?!-[dD]|--delete).*)?$',  # Allow tag listing, not deletion
                'stash list': r'^git\s+stash\s+list(\s+.*)?$',
                'reflog': r'^git\s+reflog(\s+.*)?$',
                'rev-parse': r'^git\s+rev-parse(\s+.*)?$',
                'describe': r'^git\s+describe(\s+.*)?$',
                'shortlog': r'^git\s+shortlog(\s+.*)?$',
                'whatchanged': r'^git\s+whatchanged(\s+.*)?$',
            }
            
            # Only add enabled git operations
            for op in safe_git_ops:
                if op in self.git_patterns:
                    self.safe_command_patterns[f'git_{op.replace(" ", "_").replace("-", "_")}'] = self.git_patterns[op]
                    # Also add to allowed commands list for display purposes
                    if op not in self.allowed_commands and f'git {op}' not in self.allowed_commands:
                        self.allowed_commands.append(f'git {op}')

        print(f"Shell server starting with {len(self.safe_command_patterns)} allowed command patterns", file=sys.stderr)
        available_commands = ', '.join(sorted(set([p.split('_')[0] if '_' in p else p for p in self.safe_command_patterns.keys()])))
        print(f"Available commands: {available_commands}", file=sys.stderr)
        print(f"YOLO mode: {self.yolo_mode}", file=sys.stderr)
        print(f"Git operations enabled: {self.git_operations_enabled}", file=sys.stderr)
        print(f"Safe write paths: {self.wp_manager.policy.get('safe_write_paths', [])}", file=sys.stderr)
        print(f"Write patterns: {self.wp_manager.policy.get('allowed_write_patterns', [])}", file=sys.stderr)
        print(f"Interpreters: {self.wp_manager.policy.get('allowed_interpreters', [])}", file=sys.stderr)
        
    def _expand_and_tokenize(self, cmd_segment: str) -> list:
        """Expand shell features in Python and tokenize into an args list.

        Handles environment variables, tilde expansion, and glob patterns
        so that subprocess can be called with shell=False.
        """
        # Expand environment variables ($VAR, ${VAR}) before tokenizing
        expanded = os.path.expandvars(cmd_segment)

        try:
            tokens = shlex.split(expanded)
        except ValueError as e:
            # Malformed quoting — fall back to naive whitespace split
            print(f"shlex.split failed ({e}), falling back to split()", file=sys.stderr)
            tokens = expanded.split()

        if not tokens:
            return tokens

        # Apply tilde expansion and glob expansion per-token (after splitting)
        tokens = [os.path.expanduser(t) for t in tokens]
        result = [tokens[0]]
        for arg in tokens[1:]:
            if any(ch in arg for ch in ('*', '?', '[')):
                matches = glob.glob(arg)
                if matches:
                    result.extend(sorted(matches))
                else:
                    # No matches — pass the literal pattern (matches bash behavior)
                    result.append(arg)
            else:
                result.append(arg)

        return result

    @staticmethod
    def _is_compound_command(command: str) -> bool:
        """Check if command is a compound shell construct (for/while/if/etc)."""
        first_word = command.strip().split()[0] if command.strip() else ''
        return first_word in _COMPOUND_STARTERS

    def _validate_compound_body(self, command: str) -> tuple:
        """Validate commands inside a compound shell construct.

        Splits the compound into segments, strips shell keywords, and
        checks the actual commands against the allowlist.
        Returns (allowed: bool, denial_reason: str).
        """
        # Split into fine-grained segments by ; \n && || |
        parts = re.split(r'[;\n]|\s*\&\&\s*|\s*\|\|\s*|\s*\|\s*', command)

        for part in parts:
            part = part.strip()
            if not part:
                continue
            words = part.split()
            if not words:
                continue

            # Strip leading shell keywords to find the real command
            idx = 0
            while idx < len(words) and words[idx] in _SHELL_KEYWORDS:
                # After 'for', skip variable name + word list (not commands)
                if words[idx] == 'for' and idx + 1 < len(words):
                    idx += 1          # skip loop variable name
                    while idx < len(words) and words[idx] not in ('do', 'in'):
                        idx += 1
                idx += 1

            if idx >= len(words):
                continue

            cmd_word = words[idx]

            # Variable assignment (var=...) — not a command invocation
            if '=' in cmd_word and not cmd_word.startswith('=') and not cmd_word.startswith('-'):
                continue

            # Validate the command against the allowlist
            test_cmd = ' '.join(words[idx:])
            segment_ok = any(
                re.match(p, test_cmd, re.IGNORECASE)
                for p in self.safe_command_patterns.values()
            )
            if not segment_ok:
                return False, f"'{cmd_word}' is not allowed"

        # Validate $() and backtick substitutions
        for sub in re.findall(r'\$\(([^)]+)\)', command) + re.findall(r'`([^`]+)`', command):
            sub_ok, sub_reason = self.is_command_allowed(sub.strip())
            if not sub_ok:
                return False, sub_reason

        return True, ""

    @staticmethod
    def _extract_redirections(args: list) -> tuple:
        """Extract shell-style redirections from tokenized args.

        Returns (cleaned_args, subprocess_kwargs) where subprocess_kwargs
        contains stdout/stderr overrides for subprocess.run().

        Supported redirections:
          2>&1           -> stderr=subprocess.STDOUT
          2>/dev/null    -> stderr=subprocess.DEVNULL
          >/dev/null     -> stdout=subprocess.DEVNULL
          1>/dev/null    -> stdout=subprocess.DEVNULL
        """
        cleaned = []
        kwargs = {}
        skip_next = False

        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue

            # 2>&1  (may appear as one token or shlex may keep it as-is)
            if arg == '2>&1':
                kwargs['stderr'] = subprocess.STDOUT
            # 2>/dev/null  — single token
            elif arg == '2>/dev/null':
                kwargs['stderr'] = subprocess.DEVNULL
            # 2> /dev/null — split across two tokens
            elif arg == '2>' and i + 1 < len(args) and args[i + 1] == '/dev/null':
                kwargs['stderr'] = subprocess.DEVNULL
                skip_next = True
            # >/dev/null or 1>/dev/null — single token
            elif arg in ('>/dev/null', '1>/dev/null'):
                kwargs['stdout'] = subprocess.DEVNULL
            # > /dev/null or 1> /dev/null — split across two tokens
            elif arg in ('>', '1>') and i + 1 < len(args) and args[i + 1] == '/dev/null':
                kwargs['stdout'] = subprocess.DEVNULL
                skip_next = True
            else:
                cleaned.append(arg)

        return cleaned, kwargs

    def _resolve_substitutions(self, cmd_segment: str, timeout: float, cwd: str) -> str:
        """Resolve $(...) and backtick command substitutions by executing them."""
        import re as _re

        def _run_substitution(inner_cmd: str) -> str:
            args = self._expand_and_tokenize(inner_cmd)
            if not args:
                return ""
            try:
                r = subprocess.run(
                    args, shell=False, capture_output=True, text=True,
                    timeout=timeout,
                    cwd=cwd if cwd and os.path.isdir(cwd) else None,
                )
                return r.stdout.rstrip("\n")
            except Exception as exc:
                print(f"Substitution failed for '{inner_cmd}': {exc}", file=sys.stderr)
                return ""

        # Replace $(...) — outermost only (non-greedy, no nested parens)
        result = _re.sub(
            r'\$\(([^)]+)\)',
            lambda m: _run_substitution(m.group(1)),
            cmd_segment,
        )
        # Replace `...`
        result = _re.sub(
            r'`([^`]+)`',
            lambda m: _run_substitution(m.group(1)),
            result,
        )
        return result

    def _execute_pipeline(self, command: str, timeout: float, cwd: str) -> subprocess.CompletedProcess:
        """Execute a command pipeline with shell=False for all subprocess calls.

        Handles pipes (|), conditional chaining (&&, ||), and sequential
        execution (;) by orchestrating individual subprocess.run() calls.
        """
        effective_cwd = cwd if cwd and os.path.isdir(cwd) else None
        segments = self._split_by_shell_operators(command)

        # Compound shell constructs (for/while/if/case/select) require a
        # shell interpreter — they aren't standalone executables.
        if self._is_compound_command(command):
            return subprocess.run(
                ['sh', '-c', command],
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=effective_cwd,
            )

        last_result = None
        accumulated_stdout = ""
        accumulated_stderr = ""

        for idx, (operator, cmd_segment) in enumerate(segments):
            # Resolve command substitutions first
            resolved = self._resolve_substitutions(cmd_segment, timeout, cwd)
            args = self._expand_and_tokenize(resolved)
            if not args:
                continue
            
            # Extract redirections (2>&1, >/dev/null, etc.) from args
            args, redir_kwargs = self._extract_redirections(args)

            # Conditional chaining: skip based on previous result
            if operator == "&&" and last_result and last_result.returncode != 0:
                continue
            if operator == "||" and last_result and last_result.returncode == 0:
                continue

            stdin_data = None
            if operator == "|" and last_result:
                stdin_data = last_result.stdout

            # Build subprocess kwargs, letting explicit redirections override defaults
            run_kwargs = dict(
                shell=False, capture_output=False, text=True,
                timeout=timeout, cwd=effective_cwd, input=stdin_data,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            run_kwargs.update(redir_kwargs)

            last_result = subprocess.run(
                args, **run_kwargs,
            )

            # Check if the next segment will pipe from this one
            next_is_pipe = (idx + 1 < len(segments) and segments[idx + 1][0] == "|")
            if operator == "|" or next_is_pipe:
                # In a pipe, only keep stderr; stdout feeds the next stage
                accumulated_stderr += last_result.stderr or ""
            else:
                accumulated_stdout += last_result.stdout or ""
                accumulated_stderr += last_result.stderr or ""

        if last_result is None:
            return subprocess.CompletedProcess(
                args=command, returncode=1,
                stdout="", stderr="No executable segments",
            )

        # For pipes, the final stage's stdout is the pipeline output
        if any(op == "|" for op, _ in segments):
            accumulated_stdout += last_result.stdout or ""

        return subprocess.CompletedProcess(
            args=command, returncode=last_result.returncode,
            stdout=accumulated_stdout, stderr=accumulated_stderr,
        )

    def _split_by_shell_operators(self, command: str) -> list[tuple[str, str]]:
        """
        Split a command by shell operators while preserving the operators.
        Returns a list of (operator, command) tuples.
        First tuple has empty operator string.
        
        Handles: &&, ||, ;, | (pipe), and command substitution $(...)
        Respects backslash escaping (e.g., \\; in find -exec)
        """
        segments = []
        current_segment = ""
        current_operator = ""
        i = 0
        in_single_quote = False
        in_double_quote = False
        in_backtick = False
        paren_depth = 0
        
        while i < len(command):
            char = command[i]
            next_char = command[i + 1] if i + 1 < len(command) else ''
            
            # Handle backslash escaping first
            if char == '\\' and i + 1 < len(command):
                next_next_char = command[i + 1]
                # If we're escaping a backtick, treat it as literal
                if next_next_char == '`':
                    current_segment += char + next_next_char
                    i += 2
                    continue
            
            # Handle quotes
            if char == "'" and not in_double_quote and not in_backtick:
                in_single_quote = not in_single_quote
                current_segment += char
                i += 1
                continue
            elif char == '"' and not in_single_quote and not in_backtick:
                in_double_quote = not in_double_quote
                current_segment += char
                i += 1
                continue
            elif char == '`' and not in_single_quote and not in_double_quote:
                in_backtick = not in_backtick
                current_segment += char
                i += 1
                continue
            
            # Track command substitution depth $(...) 
            if not in_single_quote and not in_double_quote and not in_backtick:
                if char == '$' and next_char == '(':
                    paren_depth += 1
                    current_segment += char + next_char
                    i += 2
                    continue
                elif char == ')' and paren_depth > 0:
                    paren_depth -= 1
                    current_segment += char
                    i += 1
                    continue
            
            # Only detect operators outside quotes and command substitutions
            if not in_single_quote and not in_double_quote and not in_backtick and paren_depth == 0:
                # Check for escaped characters (backslash before operator)
                # In shell, \; and \| are literal characters, not operators
                if i > 0 and command[i - 1] == '\\' and char in ';|':
                    # This is an escaped operator (like \; in find -exec)
                    # Keep it as part of the current segment
                    current_segment += char
                    i += 1
                    continue
                    
                # Check for two-character operators: &&, ||
                if char in '&|' and next_char == char:
                    if current_segment.strip():
                        segments.append((current_operator, current_segment.strip()))
                    current_operator = char + next_char
                    current_segment = ""
                    i += 2
                    continue
                # Check for single-character operators: ; and |
                elif char in ';|':
                    if current_segment.strip():
                        segments.append((current_operator, current_segment.strip()))
                    current_operator = char
                    current_segment = ""
                    i += 1
                    continue
            
            current_segment += char
            i += 1
        
        # Add final segment
        if current_segment.strip():
            segments.append((current_operator, current_segment.strip()))
        
        return segments
    
    def is_command_allowed(self, command: str) -> tuple:
        """
        Check if a command matches any of the allowed patterns.
        Also validates all commands in chains (&&, ||, ;, |) and substitutions.
        Returns (allowed: bool, denial_reason: str).
        """
        if not command or not command.strip():
            return False, "Empty command"
        
        command = command.strip()
        # Skip leading shell comment lines so "# explanation\nsed ..."
        # correctly identifies 'sed' as the command, not '#'.
        lines = command.split('\n')
        lines = [l for l in lines if not l.lstrip().startswith('#')]
        command = lines[0].strip() if lines else ''
        if not command:
            return False, "Command is only comments"

        # Compound shell constructs get dedicated validation that inspects
        # the body commands rather than naively splitting on `;`.
        if self._is_compound_command(command):
            if not self.yolo_mode:
                return self._validate_compound_body(command)
            # In yolo mode, fall through to the normal always_blocked check
            # (the block below handles it)

        # YOLO mode — allow everything except always_blocked commands
        if self.yolo_mode:
            always_blocked = self.wp_manager.policy.get('always_blocked', [])
            for token in re.split(r'\s*[|;&]+\s*', command):
                first_word = token.strip().split()[0] if token.strip() else ''
                if first_word in always_blocked:
                    print(f"YOLO mode: '{first_word}' is in always_blocked list", file=sys.stderr)
                    return False, f"'{first_word}' is in the always-blocked list"
            print(f"YOLO mode: allowing '{command[:80]}'", file=sys.stderr)
            return True, ""
        
        # Split by shell operators and validate each segment
        segments = self._split_by_shell_operators(command)
        
        if not segments:
            print(f"Command parsing resulted in no segments", file=sys.stderr)
            return False, "Command parsing produced no segments"
        
        print(f"Command split into {len(segments)} segment(s)", file=sys.stderr)
        
        # Validate each segment
        for i, (operator, cmd_segment) in enumerate(segments):
            if i > 0:
                print(f"Validating segment {i} after operator '{operator}': '{cmd_segment}'", file=sys.stderr)
            else:
                print(f"Validating segment {i}: '{cmd_segment}'", file=sys.stderr)
            
            # Check for command substitution in the segment
            if '$(' in cmd_segment or '`' in cmd_segment:
                # Extract and validate substituted commands
                # Pattern for $(...) 
                substitutions = re.findall(r'\$\(([^)]+)\)', cmd_segment)
                # Pattern for `...`
                substitutions.extend(re.findall(r'`([^`]+)`', cmd_segment))
                for sub_cmd in substitutions:
                    print(f"Validating command substitution: '{sub_cmd}'", file=sys.stderr)
                    sub_ok, _sub_reason = self.is_command_allowed(sub_cmd)
                    if not sub_ok:
                        print(f"Command substitution '{sub_cmd}' is not allowed", file=sys.stderr)
                        first_word = sub_cmd.strip().split()[0] if sub_cmd.strip() else sub_cmd
                        return False, f"'{first_word}' (in command substitution) is not allowed"
            
            # Validate the segment itself
            
            # Validate the segment itself
            segment_allowed = False
            for pattern_name, pattern in self.safe_command_patterns.items():
                try:
                    if re.match(pattern, cmd_segment, re.IGNORECASE):
                        print(f"Segment '{cmd_segment}' matched pattern '{pattern_name}'", file=sys.stderr)
                        segment_allowed = True
                        break
                except re.error as e:
                    print(f"Regex error in pattern '{pattern_name}': {e}", file=sys.stderr)
                    continue
            
            if not segment_allowed:
                print(f"Segment '{cmd_segment}' did not match any allowed patterns", file=sys.stderr)
                first_word = cmd_segment.strip().split()[0] if cmd_segment.strip() else cmd_segment
                return False, f"'{first_word}' is not allowed"
        
        # All segments are valid
        print(f"All {len(segments)} segments validated successfully", file=sys.stderr)
        return True, ""
        
    
    def _validate_single_command(self, cmd_segment: str) -> bool:
        """Validate a single command segment against allowed patterns."""
        # Check against all allowed patterns
        for pattern_name, pattern in self.safe_command_patterns.items():
            try:
                if re.match(pattern, cmd_segment, re.IGNORECASE):
                    return True
            except re.error as e:
                print(f"Regex error in pattern '{pattern_name}': {e}", file=sys.stderr)
                continue
        
        return False

    def _build_safe_command_patterns(self) -> Dict[str, str]:
        """Build safe command patterns from allowed commands list."""
        patterns = {}
        
        # Apply default pattern to each allowed command
        for cmd in self.allowed_commands:
            # Git commands: if user explicitly added "git add", "git commit" etc.
            # to the allowlist, honour them instead of blocking via safe git ops.
            # Bare "git" means "allow all git subcommands".
            if cmd.startswith('git '):
                # Explicit git subcommand — create a permissive pattern for it
                git_subcmd = cmd[4:]  # e.g. "add", "commit -m", "push"
                pattern_key = f'git_explicit_{git_subcmd.replace(" ", "_").replace("-", "_")}'
                patterns[pattern_key] = r'^git\s+' + re.escape(git_subcmd) + r'(\s+.*)?$'
                continue  # Don't also add the default pattern
            if cmd == 'git':
                # Bare "git" — allow ALL git subcommands
                patterns['git_all'] = r'^git(\s+.*)?$'
                continue
            if cmd in self.command_pattern_overrides:
                patterns[cmd] = self.command_pattern_overrides[cmd]
            else:
                patterns[cmd] = self.default_command_pattern.format(cmd=re.escape(cmd))
        
        # Add patterns for complex shell constructs that use allowed commands
        allowed_cmd_pattern = '|'.join([re.escape(cmd) for cmd in self.allowed_commands if not cmd.startswith('git ')])
        patterns['piped_commands'] = f'^({allowed_cmd_pattern})(\\s+.*?)?(\\s*\\|\\s*({allowed_cmd_pattern})(\\s+.*?)?)*$'
        
        # Allow find with -exec using allowed commands
        patterns['find_exec'] = r'^find\s+.*-exec\s+(' + '|'.join([re.escape(cmd) for cmd in self.allowed_commands if not cmd.startswith('git ')]) + r')\s+.*$'
        
        return patterns

    def get_allowed_commands_description(self) -> str:
        """Get a human-readable description of allowed commands."""
        base_commands = set()
        for pattern_name in self.safe_command_patterns.keys():
            if pattern_name.startswith('git_'):
                base_commands.add('git (safe operations)')
            elif pattern_name.startswith('env_'):
                base_commands.add(pattern_name[4:])  # Remove 'env_' prefix
            else:
                base_commands.add(pattern_name)
        
        return ', '.join(sorted(base_commands))
        
    async def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle incoming MCP requests."""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")
        
        print(f"Received request: {method}", file=sys.stderr)
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {
                            "listChanged": True
                        }
                    },
                    "serverInfo": {
                        "name": "shell-server",
                        "version": "1.0.0"
                    }
                }
            }
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "run_shell_command",
                            "description": f"Execute a complete, non-interactive shell command. Commands must be self-contained with all arguments provided - do NOT use interactive mode (e.g., use 'echo \"2+2\" | bc' not just 'bc'). Allowed commands: {self.get_allowed_commands_description()}",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "command": {
                                        "type": "string",
                                        "description": "A complete, non-interactive shell command with all required arguments (e.g., 'ls -la', 'grep pattern file', 'echo \"2+2\" | bc'). CRITICAL: Commands must be complete operations that do not require interactive input. For calculators like bc, pipe the expression: 'echo \"expression\" | bc'. Do not use incomplete commands or interactive modes."
                                    },
                                    "timeout": {
                                        "type": "number",
                                        "description": f"Timeout in seconds (default: {self.command_timeout}, max: {self.max_timeout}). Increase for long-running operations like large builds, recursive searches over big trees, or network requests to slow endpoints.",
                                        "default": self.command_timeout
                                    }
                                },
                                "required": ["command"]
                            }
                        }
                    ]
                }
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if tool_name == "run_shell_command":
                command = arguments.get("command")
                # Handle timeout parameter - convert string to number if needed
                timeout_param = arguments.get("timeout", self.command_timeout)
                try:
                    timeout = float(timeout_param) if timeout_param is not None else self.command_timeout
                    timeout = max(1, min(timeout, self.max_timeout))  # Clamp to [1, max]
                except (ValueError, TypeError):
                    timeout = self.command_timeout
                    print(f"Warning: Invalid timeout value '{timeout_param}', using default {self.command_timeout}s", file=sys.stderr)
                
                if not command:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": "Command is required"
                        }
                    }
                
                # Check if command is allowed
                allowed, denial_reason = self.is_command_allowed(command)
                if not allowed:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": f"🚫 BLOCKED: {denial_reason}\n\n" +
                                     f"📋 Allowed commands: {self.get_allowed_commands_description()}\n\n" +
                                     f"💡 Tip: You can configure allowed commands in the Shell Configuration settings."
                        }
                    }
                
                # Write policy check (after allowlist, before execution)
                write_ok, write_reason = (True, "") if self.yolo_mode else self.write_checker.check(
                    command, self._split_by_shell_operators
                )
                if not write_ok:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": f"🚫 WRITE BLOCKED: {write_reason}"
                        }
                    }

                try:
                    # The parent spawns a separate instance of this server per project,
                    # so our env reflects the correct project directory.
                    cwd = os.environ.get("ZIYA_USER_CODEBASE_DIR")
                    
                    print(f"Executing command: {command}", file=sys.stderr)
                    if cwd and os.path.isdir(cwd):
                        print(f"Working directory: {cwd}", file=sys.stderr)

                    result = self._execute_pipeline(command, timeout, cwd)

                    # Format output to be more shell-like
                    output = f"$ {command}\n"
                    if result.stdout:
                        output += result.stdout
                    if result.stderr:
                        output += result.stderr
                    
                    # Add exit code if non-zero
                    if result.returncode != 0:
                        output += f"\n[Exit code: {result.returncode}]"
                    
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": output
                                }
                            ]
                        }
                    }
                    
                except subprocess.TimeoutExpired:
                    # Always return timeout error instead of suppressing it
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Command timed out after {timeout} seconds"
                        }
                    }
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Error executing command: {str(e)}"
                        }
                    }
        
        # Handle notifications (no response needed)
        if method == "notifications/initialized":
            return None
            
        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }
    
    async def run(self):
        """Run the MCP server."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    print("EOF received, shutting down", file=sys.stderr)
                    break
                    
                line = line.strip()
                if not line:
                    continue
                    
                request = json.loads(line.strip())
                response = await self.handle_request(request)
                
                if response:
                    print(json.dumps(response), flush=True)
                    
            except json.JSONDecodeError:
                print("JSON decode error", file=sys.stderr)
                continue
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }
                print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    server = ShellServer()
    asyncio.run(server.run())

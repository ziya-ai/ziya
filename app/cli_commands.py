"""The top-level ``ziya`` CLI subcommands, in one place.

Imported by ``app.main`` *before* anything heavy (it decides whether this
invocation is a CLI run, which changes logging and mode before the first
logger is created) and by ``app.cli`` (argv pre-processing and the argparse
subparsers).  Keep this module free of any non-stdlib import.
"""

import difflib
from typing import Optional, Sequence

TOP_LEVEL_COMMANDS = frozenset({"chat", "ask", "review", "explain", "task", "shadow", "schedule"})


def unknown_command_message(extra_args: Sequence[str]) -> Optional[str]:
    """Explain a leftover bare word the server-mode parser could not place.

    The server parser takes no positional arguments, so a bare word left over
    after parsing is almost always a mistyped subcommand (``ziya shodow``).
    Returns None when every leftover is a flag, so the caller can fall back to
    argparse's own "unrecognized arguments" error.
    """
    word = next((a for a in extra_args if a and not a.startswith("-")), None)
    if word is None:
        return None
    commands = sorted(TOP_LEVEL_COMMANDS)
    close = difflib.get_close_matches(word, commands, n=1, cutoff=0.6)
    if close:
        return f"unknown command '{word}'. Did you mean 'ziya {close[0]}'?"
    return (f"unknown command '{word}'. Commands: {', '.join(commands)} "
            f"(ziya <command> --help for details)")

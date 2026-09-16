"""The top-level ``ziya`` CLI subcommands, in one place.

Imported by ``app.main`` *before* anything heavy (it decides whether this
invocation is a CLI run, which changes logging and mode before the first
logger is created) and by ``app.cli`` (argv pre-processing and the argparse
subparsers).  Keep this module free of any non-stdlib import.
"""

TOP_LEVEL_COMMANDS = frozenset({"chat", "ask", "review", "explain", "task", "shadow"})

"""Parameterised file tasks: ``ziya task <name> [args...]``.

A task prompt in ``tasks.yaml`` may reference the arguments given after
the task name:

``$ARGUMENTS``
    every argument, space-joined, as typed
``$1``, ``$2``, …
    one positional argument (``${1}`` also works)
``$ARGUMENTS[N]`` / ``${ARGUMENTS[N]}``
    the same, zero-based

A placeholder with no argument behind it renders as an empty string.  A
literal dollar sign that is not one of these forms is left alone, so a
prompt that mentions ``$HOME`` or ``$5.00`` is unchanged.  When the
prompt has no placeholders and arguments were given, they are appended as
a final ``Arguments: …`` line so they are never silently dropped.
"""
from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

_PLACEHOLDER = re.compile(
    r"\$(?:"
    r"\{ARGUMENTS\[(?P<braced_idx>\d+)\]\}"
    r"|ARGUMENTS\[(?P<idx>\d+)\]"
    r"|\{ARGUMENTS\}"
    r"|ARGUMENTS\b"
    r"|\{(?P<braced_pos>[1-9]\d*)\}"
    r"|(?P<pos>[1-9]\d*)\b(?!\.\d)"
    r")"
)


def has_placeholders(prompt: str) -> bool:
    return bool(_PLACEHOLDER.search(prompt or ""))


def render_task_arguments(prompt: str, args: Sequence[str]) -> str:
    """Substitute the argument placeholders in ``prompt``."""
    args = [str(a) for a in (args or [])]
    joined = " ".join(args)

    def _at(i: int) -> str:
        return args[i] if 0 <= i < len(args) else ""

    def _sub(m: "re.Match[str]") -> str:
        if m.group("braced_idx") is not None:
            return _at(int(m.group("braced_idx")))
        if m.group("idx") is not None:
            return _at(int(m.group("idx")))
        if m.group("braced_pos") is not None:
            return _at(int(m.group("braced_pos")) - 1)
        if m.group("pos") is not None:
            return _at(int(m.group("pos")) - 1)
        return joined

    text = prompt or ""
    if _PLACEHOLDER.search(text):
        return _PLACEHOLDER.sub(_sub, text)
    if args:
        return text.rstrip("\n") + f"\n\nArguments: {joined}\n"
    return text


def split_task_args(remainder: Sequence[str]) -> List[str]:
    """argparse's REMAINDER keeps a leading ``--`` separator; drop it."""
    rest = list(remainder or [])
    if rest and rest[0] == "--":
        rest = rest[1:]
    return rest


# ``ziya task`` options that argparse's REMAINDER would otherwise hand to
# the prompt when typed after the task name (``ziya task sweep --strict``
# worked before arguments existed).  Flags take no value; the rest take
# one, as ``--opt value`` or ``--opt=value``.
TASK_OPTION_FLAGS = {"--strict": "strict", "--no-stream": "no_stream"}
TASK_OPTION_VALUES = {"--output-format": "output_format"}


def peel_task_options(remainder: Sequence[str]) -> Tuple[Dict[str, object], List[str]]:
    """Split ``ziya task <name> …`` leftovers into options and prompt arguments.

    Known ``ziya task`` options anywhere before an explicit ``--`` are
    recognised and returned as ``{dest: value}``; everything else is a
    prompt argument.  ``--`` ends option recognition, so ``ziya task t --
    --strict`` passes a literal ``--strict`` to the prompt.  Unknown
    ``--options`` are prompt arguments (a task may well take flags of its
    own), which is why the known set is small and explicit.
    """
    opts: Dict[str, object] = {}
    args: List[str] = []
    rest = list(remainder or [])
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--":
            args.extend(rest[i + 1:])
            break
        if tok in TASK_OPTION_FLAGS:
            opts[TASK_OPTION_FLAGS[tok]] = True
        elif tok in TASK_OPTION_VALUES and i + 1 < len(rest):
            opts[TASK_OPTION_VALUES[tok]] = rest[i + 1]
            i += 1
        elif "=" in tok and tok.split("=", 1)[0] in TASK_OPTION_VALUES:
            name, value = tok.split("=", 1)
            opts[TASK_OPTION_VALUES[name]] = value
        else:
            args.append(tok)
        i += 1
    return opts, args

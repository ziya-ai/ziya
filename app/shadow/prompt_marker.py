"""Prompt-marker recommendation for shadowed shells (design doc §8).

The tint and title mark the *terminal*; a ``⏺`` in the prompt marks every
*line*, and it is the only indicator that also survives a scrollback
search.  The shell child inherits ``ZIYA_SHADOW_SESSION``, so one line in
the user's rc file is enough.  On the first interactive start whose shell
rc lacks that line, the frontend shows the exact snippet once and offers
a menu key that appends it — the human's keystroke at their own terminal
is the consent to edit their dotfile.  Nothing is ever written silently.
"""
import os
from pathlib import Path
from typing import Optional, Tuple

MARKER_ENV = "ZIYA_SHADOW_SESSION"
_SNIPPETS = {
    "zsh": '[[ -n $ZIYA_SHADOW_SESSION ]] && PROMPT="%F{yellow}⏺%f $PROMPT"  # ziya shadow marker',
    "bash": '[ -n "$ZIYA_SHADOW_SESSION" ] && PS1="\\[\\e[33m\\]⏺\\[\\e[0m\\] $PS1"  # ziya shadow marker',
}
_RC = {"zsh": "~/.zshrc", "bash": "~/.bashrc"}


def shell_kind(argv) -> Optional[str]:
    """'zsh' | 'bash' | None from the wrapped command's basename."""
    if not argv:
        return None
    base = os.path.basename(str(argv[0]))
    return base if base in _SNIPPETS else None


def rc_path(kind: str) -> Path:
    return Path(os.path.expanduser(_RC[kind]))


def snippet(kind: str) -> str:
    return _SNIPPETS[kind]


def is_installed(kind: str) -> bool:
    """True if the rc file already references the marker env var."""
    try:
        return MARKER_ENV in rc_path(kind).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def hint_marker() -> Path:
    return Path(os.path.expanduser("~/.ziya/shadow/prompt_hint_shown"))


def should_hint(argv) -> Tuple[Optional[str], bool]:
    """(shell kind, whether to show the one-time hint now)."""
    kind = shell_kind(argv)
    if kind is None or is_installed(kind) or hint_marker().exists():
        return kind, False
    return kind, True


def mark_hinted() -> None:
    try:
        hint_marker().parent.mkdir(parents=True, exist_ok=True)
        hint_marker().touch()
    except OSError:
        pass


def install(kind: str) -> Path:
    """Append the snippet to the rc file (creating it if absent)."""
    p = rc_path(kind)
    # Never write through a symlink: a planted ~/.zshrc -> elsewhere would
    # turn the human's one keystroke into an append to an arbitrary file.
    if p.is_symlink():
        raise OSError(f"{p} is a symlink; refusing to append")
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n# ziya shadow: mark the prompt while this shell is shadowed\n" + snippet(kind) + "\n")
    return p

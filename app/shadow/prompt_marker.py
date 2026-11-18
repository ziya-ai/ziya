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


# OSC 133 command marks (design doc §4.1): A prompt start, B prompt end,
# C command start, D;<status> command end.  With them the segmenter records
# exact command boundaries and real exit codes instead of guessing from the
# prompt.  Unlike the ⏺ marker these are not gated on ZIYA_SHADOW_SESSION:
# ssh does not forward that variable, and the host that most needs the marks
# is the remote one.  Terminals that do not know OSC 133 ignore it.
# D runs first in precmd so it sees the command's status; B is re-appended
# last, after any theme has rebuilt the prompt.  Each hook returns the
# status it found so the user's own prompt still shows it.
_HOOKS = {
    "zsh": r"""# ziya shadow: OSC 133 command marks (exact command boundaries and exit codes)
if [[ -o interactive && -z $_ZIYA_OSC133 ]]; then
  _ZIYA_OSC133=1
  _ZIYA_133_B=$'%{\033]133;B\007%}'
  _ziya_133_end() { local s=$?; printf '\033]133;D;%s\007\033]133;A\007' "$s"; return $s; }
  _ziya_133_ps() { local s=$?; [[ $PROMPT == *"$_ZIYA_133_B"* ]] || PROMPT+=$_ZIYA_133_B; return $s; }
  _ziya_133_run() { printf '\033]133;C\007'; }
  precmd_functions=(_ziya_133_end $precmd_functions _ziya_133_ps)
  preexec_functions+=(_ziya_133_run)
fi""",
    # PS0 (the C mark) needs bash 4.4; older bash gets no marks at all,
    # since B without C would leave every command's output unattributed.
    "bash": r"""# ziya shadow: OSC 133 command marks (exact command boundaries and exit codes)
if [[ $- == *i* && -z $_ZIYA_OSC133 ]] && (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then
  _ZIYA_OSC133=1
  _ZIYA_133_B='\[\e]133;B\a\]'
  _ziya_133_end() { local s=$?; printf '\033]133;D;%s\007\033]133;A\007' "$s"; return $s; }
  _ziya_133_ps() { local s=$?; [[ $PS1 == *"$_ZIYA_133_B"* ]] || PS1+=$_ZIYA_133_B; return $s; }
  PROMPT_COMMAND=$'_ziya_133_end\n'"${PROMPT_COMMAND}"$'\n_ziya_133_ps'
  PS0+='\e]133;C\a'
fi""",
}


def hooks_snippet(kind: str) -> str:
    """The OSC 133 hook block for ``kind``'s rc file (``ziya shadow --hooks``).

    Put it at the end of the rc, after any prompt theme, on every host a
    shadowed terminal reaches -- including the far side of an ssh hop.
    """
    return _HOOKS[kind]


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

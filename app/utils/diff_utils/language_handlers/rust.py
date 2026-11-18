"""
Rust-specific language handler.

The validator's job is to tell a patch that BROKE the file (unclosed brace,
``let x = ;``) from one that did not.  It is not a linter, and it is not a
build: the file it sees is one module of a crate, with nothing else of the
crate present.

The previous handler failed every real Rust file three ways over.  ``rustc
--emit=metadata`` on a lone module cannot resolve ``use crate::…``, ``mod
x;``, derive macros or any external crate, and without ``--crate-type lib``
demanded a ``main``; a set of "common issue" lints then refused elided
lifetimes (``fn f(x: &str)``), re-exports as "unused imports" and any
``unsafe`` block without a comment; and the no-rustc fallback read ``match``
arms and struct fields as "missing semicolon".  Every ``.rs`` diff was
therefore refused with "the patched file fails the language check".

Now:

* ``rustc`` runs with ``--crate-type lib --emit=metadata --cap-lints allow``
  and the crate's edition (from the nearest ``Cargo.toml``, default 2021).
* Its diagnostics are split into PARSE-class errors (no error code, or one
  of the few codes the parser itself emits) and everything else (name
  resolution, types, missing modules), which a single-file view cannot
  judge.
* A parse error that the original file did not have is a hard failure.
  Anything else is accepted, as the C++ handler accepts errors the
  pre-patch file shares.
* Without ``rustc`` (or on timeout) the only check is delimiter balance,
  with comments, strings and char literals skipped so ``"{"`` and ``'}'``
  are not counted.
"""

import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional, Set, Tuple

from app.utils.logging_utils import logger
from .base import LanguageHandler


# Error codes rustc's parser/lexer emits.  Every other coded error comes
# from a later phase (resolution, type check, borrow check) and says nothing
# about whether the patch produced well-formed Rust.
_PARSE_ERROR_CODES = frozenset({
    "E0585",  # doc comment not attached to anything
    "E0586",  # inclusive range with no end
    "E0758",  # unterminated block comment
    "E0762",  # unterminated character literal
    "E0763",  # unterminated byte constant
    "E0765",  # unterminated double quote string
    "E0766",  # unterminated double quote byte string
    "E0767",  # unreachable label (parser-ish, rare)
    "E0768",  # number with no digits
})

# ``--error-format=short`` lines: ``path:line:col: error[E0432]: message`` or
# ``path:line:col: error: message``.
_SHORT_DIAG_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+): error(?:\[(?P<code>E\d{4})\])?: (?P<msg>.*)$")

_RUSTC_TIMEOUT_S = 30


class RustHandler(LanguageHandler):
    """Handler for Rust files."""

    @classmethod
    def can_handle(cls, file_path: str) -> bool:
        return file_path.endswith('.rs')

    @classmethod
    def verify_changes(cls, original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Differential syntax check of one module file.

          1. If rustc accepts the modified file outright, accept.
          2. If it reports parse-class errors the original file does not
             have, reject with them: the patch broke the file.
          3. Otherwise (resolution/type errors only, or parse errors the
             original shares) accept; those are the missing crate context
             or pre-existing, not this patch.
          4. With no rustc, fall back to delimiter balance.
        """
        edition = cls._crate_edition(file_path)
        mod_errs = cls._rustc_errors(modified_content, edition)
        if mod_errs is None:
            return cls._balanced_delimiters(modified_content)
        if not mod_errs:
            return True, None

        mod_parse = [d for d in mod_errs if cls._is_parse_error(d)]
        if mod_parse:
            orig_errs = cls._rustc_errors(original_content, edition) or []
            orig_keys = {cls._diag_key(d) for d in orig_errs if cls._is_parse_error(d)}
            new_parse = [d for d in mod_parse if cls._diag_key(d) not in orig_keys]
            if new_parse:
                msg = "\n".join(cls._format(d, file_path) for d in new_parse[:5])
                logger.error(f"Rust syntax validation failed for {file_path}: {msg}")
                return False, msg
            logger.warning(
                f"Rust validation for {file_path}: parse errors present but the "
                f"pre-patch file has the same ones; not a patch defect. Accepting."
            )
            return True, None

        # Only resolution / type errors.  A lone module cannot resolve its
        # crate, so these are expected; they are a patch defect only when the
        # original file compiled clean on its own, which a real module never
        # does but a self-contained file might.
        orig_errs = cls._rustc_errors(original_content, edition)
        if orig_errs == []:
            msg = "\n".join(cls._format(d, file_path) for d in mod_errs[:5])
            logger.error(f"Rust validation failed for {file_path} (original compiled clean): {msg}")
            return False, msg
        logger.debug(
            f"Rust validation for {file_path}: only resolution/type errors "
            f"({len(mod_errs)}), expected for a single module file. Accepting."
        )
        return True, None

    # -- rustc ---------------------------------------------------------------

    @classmethod
    def _rustc_errors(cls, content: str, edition: str) -> Optional[List[Dict[str, str]]]:
        """Parsed error diagnostics from rustc, [] when clean, None when
        rustc is unavailable, timed out, or produced nothing parseable
        while failing (the caller then falls back)."""
        # Source and output both live in a scratch directory.  ``-o
        # os.devnull`` (the previous form) made rustc create its working
        # temp dir beside the output, i.e. under /dev, which fails with
        # "couldn't create a temp dir" -- so rustc never returned 0, even
        # for a file that compiled cleanly.
        with tempfile.TemporaryDirectory(prefix="ziya-rustc-") as work:
            src = os.path.join(work, "module.rs")
            with open(src, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                result = subprocess.run(
                    ['rustc', '--crate-type', 'lib', '--edition', edition,
                     '--emit=metadata', '--error-format=short', '--cap-lints', 'allow',
                     '--out-dir', work, src],
                    capture_output=True, text=True, timeout=_RUSTC_TIMEOUT_S,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
                logger.warning(f"Falling back to basic Rust validation: {e}")
                return None
            if result.returncode == 0:
                return []
            diags = cls._parse_short_diagnostics(result.stderr)
            if not diags:
                # Failed for a reason that is not a source diagnostic (bad
                # edition flag on an old toolchain, ICE, ...): no verdict.
                logger.warning(f"rustc failed without diagnostics; falling back: {result.stderr.strip()[:200]}")
                return None
            return diags

    @staticmethod
    def _parse_short_diagnostics(stderr: str) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for line in (stderr or "").splitlines():
            m = _SHORT_DIAG_RE.match(line.strip())
            if m:
                out.append(m.groupdict())
        return out

    @staticmethod
    def _is_parse_error(d: Dict[str, str]) -> bool:
        code = d.get("code")
        return not code or code in _PARSE_ERROR_CODES

    @staticmethod
    def _diag_key(d: Dict[str, str]) -> Tuple[Optional[str], str]:
        """Identity of a diagnostic independent of where it sits: a patch
        above a pre-existing error moves its line number."""
        return (d.get("code"), d.get("msg", ""))

    @staticmethod
    def _format(d: Dict[str, str], file_path: str) -> str:
        code = f"[{d['code']}]" if d.get("code") else ""
        return f"{file_path}:{d['line']}:{d['col']}: error{code}: {d['msg']}"

    @staticmethod
    def _crate_edition(file_path: str) -> str:
        """``edition`` of the nearest enclosing Cargo.toml, default 2021."""
        try:
            d = os.path.dirname(os.path.abspath(file_path))
            for _ in range(12):
                cargo = os.path.join(d, "Cargo.toml")
                if os.path.isfile(cargo):
                    with open(cargo, "r", encoding="utf-8", errors="replace") as f:
                        m = re.search(r'^\s*edition\s*=\s*"(\d{4})"', f.read(), re.MULTILINE)
                    return m.group(1) if m else "2021"
                parent = os.path.dirname(d)
                if parent == d:
                    break
                d = parent
        except OSError:
            pass
        return "2021"

    # -- fallback ------------------------------------------------------------

    # Leftmost-match alternation so a ``//`` inside a string is a string and
    # a ``"`` inside a comment is a comment.  Lifetimes (``'a``) do not match
    # the char-literal branch because no closing quote follows.
    _SKIP_RE = re.compile(
        r"/\*.*?\*/"                      # block comment (non-nested)
        r"|//[^\n]*"                      # line comment
        r"|b?r(#*)\"(?:.|\n)*?\"\1"       # raw string r"…" / r#"…"#
        r"|b?\"(?:\\.|[^\"\\])*\""        # string / byte string
        r"|b?'(?:\\[^']*|[^'\\])'",       # char / byte literal
        re.DOTALL,
    )

    @classmethod
    def _balanced_delimiters(cls, content: str) -> Tuple[bool, Optional[str]]:
        """Delimiter balance over the code outside comments and literals."""
        def _blank(m: "re.Match") -> str:
            # Keep newlines so reported line numbers stay right.
            return re.sub(r"[^\n]", " ", m.group(0))
        code = cls._SKIP_RE.sub(_blank, content)
        pairs = {')': '(', '}': '{', ']': '['}
        stack: List[Tuple[str, int]] = []
        for i, ch in enumerate(code):
            if ch in '({[':
                stack.append((ch, i))
            elif ch in ')}]':
                if not stack or stack[-1][0] != pairs[ch]:
                    line = code.count('\n', 0, i) + 1
                    col = i - code.rfind('\n', 0, i)
                    return False, f"error: unexpected closing delimiter '{ch}' at line {line}, column {col}"
                stack.pop()
        if stack:
            ch, i = stack[-1]
            line = code.count('\n', 0, i) + 1
            return False, f"error: unclosed delimiter '{ch}' opened at line {line}"
        return True, None

    # -- duplicates (advisory; see git_diff.py) ------------------------------

    @classmethod
    def detect_duplicates(cls, original_content: str, modified_content: str) -> Tuple[bool, List[str]]:
        """Definitions that occur more often after the patch than before."""
        original_definitions = cls._extract_rust_definitions(original_content)
        modified_definitions = cls._extract_rust_definitions(modified_content)
        duplicates = []
        for def_type, def_dict in modified_definitions.items():
            for def_name, occurrences in def_dict.items():
                if len(occurrences) > 1:
                    original_count = len(original_definitions.get(def_type, {}).get(def_name, []))
                    if len(occurrences) > original_count:
                        line_numbers = ", ".join(str(line) for line in occurrences)
                        duplicates.append(f"{def_type} {def_name} (lines {line_numbers})")
                        logger.warning(f"{def_type} '{def_name}' appears to be duplicated after diff application at lines {line_numbers}")
        return bool(duplicates), duplicates

    @classmethod
    def _extract_rust_definitions(cls, content: str) -> Dict[str, Dict[str, List[int]]]:
        definitions: Dict[str, Dict[str, List[int]]] = {k: {} for k in ('fn', 'struct', 'enum', 'trait', 'mod')}
        patterns = {
            'fn': r'\bfn\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:<[^>]*>)?\s*\(',
            'struct': r'\bstruct\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'enum': r'\benum\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'trait': r'\btrait\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            'mod': r'\bmod\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[;{]',
        }
        for i, line in enumerate(content.splitlines(), 1):
            for def_type, pattern in patterns.items():
                for match in re.finditer(pattern, line):
                    definitions[def_type].setdefault(match.group(1), []).append(i)
        return definitions

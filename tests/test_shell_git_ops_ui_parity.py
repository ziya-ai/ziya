"""
Seam test: the shell-config modal's git-operation checkbox list must match the
backend's default safe-git set exactly.

Why this exists
---------------
The two lists live in different languages and are edited independently:

  app/config/shell_config.py   DEFAULT_SHELL_CONFIG["safeGitOperations"]
  frontend/.../ShellConfigModal.tsx   const allGitOperations = [...]

Nothing connects them at build time, so they drift silently and the drift is
invisible from either side:

  * op in backend, NOT in modal  -> the op is ACTIVE but has no checkbox, so a
    user cannot see it and cannot revoke it.  (This is exactly how 'rev-list'
    shipped: enabled by default, absent from the UI.)
  * op in modal, NOT in backend  -> the checkbox renders unchecked and ticking
    it appears to grant an op the server has no pattern for.

Both directions are asserted.  The modal list is parsed out of the TSX source
rather than mocked, because a mocked copy of the list would agree with itself
and certify nothing.
"""

import re
from pathlib import Path

import pytest

from app.config.shell_config import DEFAULT_SHELL_CONFIG

MODAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "components"
    / "ShellConfigModal.tsx"
)


def _parse_modal_array(name: str) -> list:
    """Extract a named string-array literal from the modal source."""
    source = MODAL_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"const\s+{re.escape(name)}\s*(?::[^=]*)?=\s*\[(.*?)\]\s*;",
        source,
        re.DOTALL,
    )
    assert match, (
        f"could not locate 'const {name} = [...]' in "
        f"{MODAL_PATH}; if it was renamed or restructured, update this parser "
        "rather than deleting the parity assertion"
    )
    return re.findall(r"""['"]([^'"]+)['"]""", match.group(1))


def _parse_modal_git_ops() -> list:
    return _parse_modal_array("allGitOperations")


@pytest.fixture(scope="module")
def modal_ops():
    return _parse_modal_git_ops()


@pytest.fixture(scope="module")
def backend_ops():
    return list(DEFAULT_SHELL_CONFIG["safeGitOperations"])


class TestParserSanity:
    """Positive controls: the parser actually found a real list.

    Without these, an empty parse would make every parity assertion below
    pass vacuously.
    """

    def test_modal_file_exists(self):
        assert MODAL_PATH.is_file(), f"{MODAL_PATH} not found"

    def test_parsed_a_nonempty_list(self, modal_ops):
        assert len(modal_ops) > 5, f"suspiciously short parse: {modal_ops}"

    def test_parse_includes_a_known_op(self, modal_ops):
        # 'status' has been in both lists since the feature existed; if this
        # fails the parser is matching the wrong array.
        assert "status" in modal_ops

    def test_backend_list_nonempty(self, backend_ops):
        assert len(backend_ops) > 5


class TestGitOpParity:
    """The two lists must agree in both directions."""

    def test_no_backend_op_missing_from_modal(self, modal_ops, backend_ops):
        missing = [op for op in backend_ops if op not in modal_ops]
        assert not missing, (
            f"git ops active by default but with no checkbox in the modal: "
            f"{missing} — they cannot be seen or revoked from the UI"
        )

    def test_no_modal_op_absent_from_backend(self, modal_ops, backend_ops):
        extra = [op for op in modal_ops if op not in backend_ops]
        assert not extra, (
            f"modal offers git ops the backend default set does not contain: "
            f"{extra} — ticking them would appear to grant an op with no "
            f"server-side pattern"
        )

    def test_no_duplicates_in_modal(self, modal_ops):
        dupes = {op for op in modal_ops if modal_ops.count(op) > 1}
        assert not dupes, f"duplicate checkboxes would render twice: {dupes}"


class TestRegressionOps:
    """Ops whose absence was an actual shipped defect.

    Named individually so a future edit that drops one produces a message that
    says which, rather than a bare set difference.
    """

    @pytest.mark.parametrize("op", ["grep", "cat-file", "check-ignore", "rev-list"])
    def test_op_present_in_both(self, op, modal_ops, backend_ops):
        assert op in backend_ops, f"'{op}' dropped from the backend default set"
        assert op in modal_ops, f"'{op}' dropped from the modal checkbox list"

"""Three diff-validation defects that each cost a correction round in one
session (2026-10-25..27, the "Round" harp-score music work).

1. TypeScript syntax classification.  The tsc check runs with --noResolve and
   is meant to fail a diff only for SYNTAX errors (TS1000-TS1999).  It matched
   any code starting with "1", so TS18004 ("No value exists in scope for the
   shorthand property") counted as syntax: three diffs whose only fault was a
   semantic error -- or a symbol introduced by a sibling diff in the same
   response -- were rolled back.
2. The reader-facing reason for that rollback said the hunks "did not match the
   current file", sending the model after a context mismatch that did not exist.
3. The phantom-diff detector fired on a phrase that was QUOTED -- a reply
   describing the detector ('the model says "apply the diff above"') was sent
   back for a diff it never claimed to contain.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.diff_utils.language_handlers import typescript as ts
from app.utils.diff_validation_hook import reader_reason
from app.utils.phantom_diff import find_phantom_diff_reference

ROOT = Path(__file__).resolve().parents[1]
# A path under frontend/ so the handler finds the project-local tsc binary;
# the file itself need not exist (content is passed in, tsc is stubbed).
TS_PATH = str(ROOT / "frontend" / "src" / "utils" / "d3Plugins" / "musicPlugin.ts")

# The diagnostics from the rejected 2026-10-25 diff, abridged.
SEMANTIC_ONLY = (
    "musicPlugin.ts(18,8): error TS2307: Cannot find module './musicAccidentals'.\n"
    "musicPlugin.ts(6584,19): error TS2339: Property 'hooks' does not exist on type 'MusicBracket'.\n"
    "musicPlugin.ts(6588,11): error TS18004: No value exists in scope for the shorthand property 'stave'.\n"
)
SYNTAX = "musicPlugin.ts(12,3): error TS1005: ';' expected.\n"


class TestTsSyntaxCode:
    @pytest.mark.parametrize("code,expected", [
        ("1005", True), ("1128", True),
        ("18004", False), ("18048", False), ("2307", False), ("2339", False),
        ("1323", False),  # config-only
    ])
    def test_classification(self, code, expected):
        assert ts._is_ts_syntax_code(code, {"1323", "1378", "1375", "1432", "1208"}) is expected


def _stub_tsc(monkeypatch, outputs):
    """Every tsc call returns the next queued stdout (rc 2), or rc 0 when empty."""
    if not (ROOT / "frontend" / "node_modules" / ".bin" / "tsc").is_file():
        pytest.skip("project tsc not installed")
    queue = list(outputs)

    def fake_run(args, **kw):
        out = queue.pop(0) if queue else ""
        return SimpleNamespace(returncode=2 if out else 0, stdout=out, stderr="")
    monkeypatch.setattr(ts.subprocess, "run", fake_run)


class TestVerifyChanges:
    def test_semantic_only_diagnostics_do_not_fail_the_diff(self, monkeypatch):
        _stub_tsc(monkeypatch, [SEMANTIC_ONLY])
        ok, err = ts.TypeScriptHandler.verify_changes("const a = 1;\n", "const a = 2;\n", TS_PATH)
        assert ok, err

    def test_a_real_syntax_error_introduced_by_the_diff_still_fails(self, monkeypatch):
        # Modified fails with TS1005; the original re-check is clean.
        _stub_tsc(monkeypatch, [SYNTAX, ""])
        ok, err = ts.TypeScriptHandler.verify_changes("const a = 1;\n", "const a = \n", TS_PATH)
        assert not ok and "TS1005" in err


class TestReaderReason:
    def test_language_validation_rollback_is_not_called_a_mismatch(self):
        result = {
            "failed_hunks": [1, 2], "total_hunks": 2,
            "hunk_details": {
                "1": {"error_details": {"error": "language_validation", "message":
                      "/var/folders/x/tmpab/musicPlugin.ts(6588,11): error TS18004: No value exists"}},
                "2": {"error_details": {"error": "language_validation", "message": "same"}},
            },
        }
        reason = reader_reason(result)
        assert "did not match" not in reason
        assert "fails the language check" in reason
        assert reason.endswith("musicPlugin.ts(6588,11): error TS18004: No value exists")

    def test_a_real_mismatch_keeps_its_wording(self):
        result = {"failed_hunks": [1], "total_hunks": 1,
                  "hunk_details": {"1": {"error_details": {"error": "context_mismatch"}}}}
        assert "did not match the current file" in reader_reason(result)


class TestPhantomQuotedMention:
    def test_a_quoted_phrase_is_a_mention_not_a_claim(self):
        text = ('It covers the case where the model says "apply the diff above" but never '
                'writes one, and the correction pass answered "no diff needed".')
        assert find_phantom_diff_reference(text) is None
        assert find_phantom_diff_reference("The detector matches `the diff above`.") is None

    def test_an_unquoted_claim_is_still_caught(self):
        assert find_phantom_diff_reference("Apply the whole-rest diff above, then restart.") == "diff above"
        # A quote elsewhere in the sentence does not hide the claim.
        assert find_phantom_diff_reference('Rename to "Round", then apply the diff below.') is not None

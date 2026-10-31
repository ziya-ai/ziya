"""
Three applier defects behind the 2026-10-17 kuiper-telemetry docs failure
(README.md / SKILL.md "Malformed hunk detected"; example-notes.md "applied"
with a stale line kept and a kept line dropped).  Each test is pinned to the
outermost surface: apply_diff_pipeline on a real temp file.

1. Count-less hunk headers.  "@@ -63 +63 @@" is valid unified diff (count
   defaults to 1) and is the form the system prompt asks the model to use for
   single-line hunks.  The parser accepted it; the difflib stage, the atomic
   git path and _is_valid_hunk_header rejected it as "Malformed hunk".

2. Backslash-backtick transport escaping.  The model is told to preserve the
   \\` escaping it sees in context, and the frontend forwards the fence
   verbatim.  Only the difflib stage's parser unescaped it, so `patch` and
   `git apply` failed on every hunk whose context carries a backtick and the
   hunk reached difflib as its last chance -- where defect 1 then killed it.
   The pipeline now unescapes at entry, grounded in the target file so a JS
   template literal's genuine \\` is left alone.

3. difflib "context preservation" lockstep walk.  Entered whenever
   len(old_block) == len(new_lines), it consumed at most one old line per
   added line.  A hunk that adds one line and replaces two with one has equal
   lengths but misaligns by one: the trailing context was copied from the
   wrong file line, keeping a removed line and dropping a kept one, and the
   stage reported success.  The walk is now driven by the hunk's raw markers.
"""
import os

import pytest

from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline
from app.utils.diff_utils.application.patch_apply import apply_diff_with_difflib_hybrid_forced

BT = chr(96)
ESC = "\\" + BT


def _esc(s: str) -> str:
    return s.replace(BT, ESC)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _stages(result):
    return {h.get("stage") for h in (result.get("hunk_statuses") or {}).values()}


# ---------------------------------------------------------------------------
# Defects 1 + 2: a single-line, count-less hunk whose line carries backticks
# ---------------------------------------------------------------------------

OLD63 = "  | Example CloudWatch | 123456789012 (4fc3) | `ExampleMetricsReadOnlyRole` via **SSO**; posix group `example-metric-readers` | unverified |"
NEW63 = "  | Example CloudWatch | 123456789012 (4fc3) | `ExampleMetricsReadOnlyRole` via **SSO**; posix group `example-metric-readers` (allow ~8 h) | verified |"
README = "\n".join([f"filler line {i}" for i in range(1, 63)] + [OLD63, "  | CT syslog | `beta-ct-logs` |", "", "## Next"]) + "\n"


def _readme_diff(old, new, header="@@ -63 +63 @@"):
    return ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n"
            f"{header}\n-{old}\n+{new}\n")


def test_countless_header_with_escaped_backticks_applies(tmp_path):
    p = _write(tmp_path, "README.md", README)
    r = apply_diff_pipeline(_readme_diff(_esc(OLD63), _esc(NEW63)), p, user_codebase_dir=str(tmp_path))
    assert r["status"] == "success", r
    assert r["failed"] == []
    after = open(p).read()
    assert NEW63 in after and OLD63 not in after
    assert ESC not in after                      # escaping never reaches the file


def test_escaped_backticks_are_undone_before_the_patch_stage(tmp_path):
    # Defect 2 on its own: with the unescape at pipeline entry the system
    # patch stage applies it; before, every stage but difflib failed.
    p = _write(tmp_path, "README.md", README)
    r = apply_diff_pipeline(_readme_diff(_esc(OLD63), _esc(NEW63), "@@ -63,1 +63,1 @@"),
                            p, user_codebase_dir=str(tmp_path))
    assert r["status"] == "success", r
    assert _stages(r) == {"system_patch"}, _stages(r)


def test_countless_header_accepted_by_difflib_stage(tmp_path):
    # Defect 1 on its own.  The fallthrough path (patch and git apply both
    # failed) hands the difflib stage the ORIGINAL diff text, recount pass
    # and all skipped, so the raw "@@ -63 +63 @@" is what its gate sees.
    # Drive the stage the way that path does.
    from app.utils.diff_utils.pipeline.diff_pipeline import DiffPipeline
    from app.utils.diff_utils.pipeline.pipeline_manager import run_difflib_stage
    from app.utils.diff_utils.parsing.diff_parser import parse_unified_diff_exact_plus

    p = _write(tmp_path, "README.md", README)
    diff = _readme_diff(OLD63, NEW63)
    pipeline = DiffPipeline(p, diff)
    pipeline.initialize_hunks(list(parse_unified_diff_exact_plus(diff, p)))
    ok = run_difflib_stage(pipeline, p, diff, README.splitlines(True))
    statuses = {hid: (t.status.value, (t.error_details or {}).get("error"))
                for hid, t in pipeline.result.hunks.items()}
    assert ok, statuses
    assert "Malformed hunk detected" not in str(statuses), statuses
    assert NEW63 in open(p).read()


def test_genuine_template_literal_escape_is_preserved(tmp_path):
    # Negative control for defect 2: the file itself contains \` (a JS
    # template literal escaping a backtick), so the diff's \` is content.
    js_old = "const s = `use \\` here`;"
    js_new = "const s = `use \\` there`;"
    content = "// header\n" + js_old + "\nexport default s;\n"
    p = _write(tmp_path, "a.js", content)
    diff = ("diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1,3 +1,3 @@\n"
            " // header\n-" + js_old + "\n+" + js_new + "\n export default s;\n")
    r = apply_diff_pipeline(diff, p, user_codebase_dir=str(tmp_path))
    assert r["status"] == "success", r
    assert open(p).read() == "// header\n" + js_new + "\nexport default s;\n"


# ---------------------------------------------------------------------------
# Defect 3: add one line, replace two with one, trailing context
# ---------------------------------------------------------------------------

ROWS = [
    "| `a` | row a |",
    "| `b` | row b |",
    "",
    "Refused: the other stacks, the",
    "cross-account role, EdgeBuilder, the",
    "per-stack role (group-gated), and the",
    "prodlab dataplane. **Keep me**: each stack has one",
    "of each; the list is elsewhere.",
]
FILE3 = "\n".join(["# Title", ""] + ROWS + ["", "## Next"]) + "\n"
ADDED = "| `c` | row c |"
MERGED = "cross-account role, EdgeBuilder, and the"
# Model's miscounted header, as emitted (-6,+7 for an 8/8 body).
DIFF3 = ("diff --git a/f.md b/f.md\n--- a/f.md\n+++ b/f.md\n@@ -3,6 +3,7 @@\n"
         " " + ROWS[0] + "\n"
         " " + ROWS[1] + "\n"
         "+" + ADDED + "\n"
         " \n"
         " " + ROWS[3] + "\n"
         "-" + ROWS[4] + "\n"
         "-" + ROWS[5] + "\n"
         "+" + MERGED + "\n"
         " " + ROWS[6] + "\n")
EXPECTED3 = "\n".join(["# Title", "", ROWS[0], ROWS[1], ADDED, "", ROWS[3], MERGED, ROWS[6], ROWS[7], "", "## Next"]) + "\n"


def test_hybrid_forced_keeps_trailing_context_after_two_for_one_replace(tmp_path):
    p = _write(tmp_path, "f.md", FILE3)
    out = apply_diff_with_difflib_hybrid_forced(p, DIFF3, FILE3.splitlines(True))
    assert "".join(out) == EXPECTED3


def test_pipeline_difflib_stage_two_for_one_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_FORCE_DIFFLIB", "1")
    p = _write(tmp_path, "f.md", FILE3)
    r = apply_diff_pipeline(DIFF3, p, user_codebase_dir=str(tmp_path))
    assert r["status"] == "success", r
    after = open(p).read()
    assert after == EXPECTED3
    assert ROWS[5] not in after          # removed line is gone
    assert ROWS[6] in after              # trailing context survived


def test_second_apply_is_already_applied_not_malformed(tmp_path, monkeypatch):
    # The streaming verifier re-runs the same pipeline on a copy of the live
    # file.  After a correct apply it must see "already applied", not the
    # "Malformed state - both old and new content exist" that the corrupted
    # first apply produced on 2026-10-17.
    monkeypatch.setenv("ZIYA_FORCE_DIFFLIB", "1")
    p = _write(tmp_path, "f.md", FILE3)
    assert apply_diff_pipeline(DIFF3, p, user_codebase_dir=str(tmp_path))["status"] == "success"
    r2 = apply_diff_pipeline(DIFF3, p, user_codebase_dir=str(tmp_path))
    assert r2["failed"] == [], r2
    assert open(p).read() == EXPECTED3

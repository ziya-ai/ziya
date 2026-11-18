"""
The Rust language handler must accept valid Rust and refuse only broken Rust.

Reported 2026-11-10: every ``.rs`` diff was refused with "the patch applied,
but the patched file fails the language check".  Three independent causes
in ``RustHandler.verify_changes``, each alone enough for 100% failure:
``rustc`` on a lone module file cannot resolve ``use crate::…`` (and wanted
a ``main``); style "lints" refused elided lifetimes, re-exports and
uncommented ``unsafe``; and the no-rustc fallback read ``match`` arms as
missing semicolons.  The probe file below (a typical module) hit all three.

Covered: the probe passes with rustc and without; an unclosed brace and a
``let x = ;`` are refused with the line named; a parse error the ORIGINAL
already had is not blamed on the patch; the fallback ignores delimiters in
strings, chars and comments while still catching a real imbalance; and the
seam -- the real ``apply_diff_pipeline`` keeps a fuzzy-placed valid Rust
diff and rolls back a fuzzy-placed broken one.
"""
import shutil
import subprocess

import pytest

from app.utils.diff_utils.language_handlers import LanguageHandlerRegistry
from app.utils.diff_utils.language_handlers.rust import RustHandler
from app.utils.diff_utils.pipeline import apply_diff_pipeline

HAVE_RUSTC = shutil.which("rustc") is not None
needs_rustc = pytest.mark.skipif(not HAVE_RUSTC, reason="rustc not installed")

# A typical module: crate-internal import, elided lifetimes, a struct with a
# lifetime, match arms without semicolons, an uncommented unsafe block, a
# derive macro the single-file view cannot resolve, and a submodule.
MODULE = '''use crate::config::Settings;
use std::collections::HashMap;
use serde::Serialize;

mod helpers;

#[derive(Debug, Serialize)]
pub struct Cache<'a> {
    map: HashMap<&'a str, u64>,
}

pub fn lookup(cache: &Cache, key: &str) -> Option<u64> {
    let v = cache.map.get(key).copied();
    match v {
        Some(x) => Some(x),
        None => None,
    }
}

pub fn raw(p: *const u8) -> u8 {
    unsafe { *p }
}
'''

UNCLOSED = MODULE.replace("        None => None,\n    }\n", "        None => None,\n")
BAD_STMT = MODULE.replace("let v = cache.map.get(key).copied();", "let v = ;")


@pytest.fixture
def no_rustc(monkeypatch):
    """Force the fallback path regardless of the machine."""
    def _missing(*a, **k):
        raise FileNotFoundError("rustc")
    monkeypatch.setattr(subprocess, "run", _missing)


def test_registry_routes_rs_files_to_the_rust_handler():
    assert LanguageHandlerRegistry.get_handler("src/cache.rs") is RustHandler


# -- rustc path ---------------------------------------------------------------

@needs_rustc
def test_typical_module_file_is_accepted():
    ok, err = RustHandler.verify_changes(MODULE, MODULE, "src/cache.rs")
    assert (ok, err) == (True, None)


@needs_rustc
@pytest.mark.parametrize("broken, expect_line", [(UNCLOSED, None), (BAD_STMT, "13")])
def test_patch_that_breaks_syntax_is_refused_with_location(broken, expect_line):
    ok, err = RustHandler.verify_changes(MODULE, broken, "src/cache.rs")
    assert ok is False
    assert err and "src/cache.rs:" in err and "error" in err
    if expect_line:
        assert f"src/cache.rs:{expect_line}:" in err
    # Resolution noise from the lone-module view is not what is reported.
    assert "E0432" not in err and "unresolved import" not in err


@needs_rustc
def test_preexisting_parse_error_is_not_blamed_on_the_patch():
    # The original already has the bad statement; the patch adds a line above
    # it (so the error moves) and touches nothing else.  Not a patch defect.
    patched = "// header\n" + BAD_STMT
    ok, err = RustHandler.verify_changes(BAD_STMT, patched, "src/cache.rs")
    assert (ok, err) == (True, None)
    # Positive control: the same error introduced by the patch IS refused.
    assert RustHandler.verify_changes(MODULE, BAD_STMT, "src/cache.rs")[0] is False


@needs_rustc
def test_rustc_actually_returns_clean_for_a_clean_file():
    # ``-o /dev/null`` made rustc fail creating its temp dir, so the old
    # handler never saw exit 0 even for valid code.  [] means "clean".
    clean = "pub fn add(a: u32, b: u32) -> u32 {\n    a + b\n}\n"
    assert RustHandler._rustc_errors(clean, "2021") == []
    errs = RustHandler._rustc_errors(MODULE, "2021")
    assert errs and all(not RustHandler._is_parse_error(d) for d in errs)


@needs_rustc
def test_self_contained_file_that_compiled_clean_is_held_to_it():
    clean = "pub fn add(a: u32, b: u32) -> u32 {\n    a + b\n}\n"
    assert RustHandler.verify_changes(clean, clean, "lib.rs") == (True, None)
    # A type error the patch introduced in a file that compiled on its own.
    typed = clean.replace("a + b", "a + \"b\"")
    ok, err = RustHandler.verify_changes(clean, typed, "lib.rs")
    assert ok is False and err and "lib.rs:2:" in err


def test_edition_read_from_nearest_cargo_toml(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nedition = "2018"\n')
    src = tmp_path / "src"
    src.mkdir()
    assert RustHandler._crate_edition(str(src / "lib.rs")) == "2018"
    assert RustHandler._crate_edition("/nonexistent/dir/lib.rs") == "2021"


# -- fallback path -------------------------------------------------------------

def test_fallback_accepts_the_typical_module(no_rustc):
    assert RustHandler.verify_changes(MODULE, MODULE, "src/cache.rs") == (True, None)


def test_fallback_ignores_delimiters_in_literals_and_comments(no_rustc):
    src = '''// a stray ) in a comment
/* and a { in a block comment */
fn f() -> &'static str {
    let open = '{';
    let close = "}}}";
    let raw = r#"({["#;
    let lt: Option<&'a str> = None;
    open; close; raw; lt;
    "ok"
}
'''
    assert RustHandler.verify_changes(src, src, "x.rs") == (True, None)


def test_fallback_catches_a_real_imbalance_with_line(no_rustc):
    ok, err = RustHandler.verify_changes(MODULE, UNCLOSED, "src/cache.rs")
    assert ok is False and err and "unclosed delimiter" in err
    ok, err = RustHandler.verify_changes(MODULE, MODULE + "}\n", "src/cache.rs")
    assert ok is False and err and "unexpected closing delimiter" in err
    assert f"line {MODULE.count(chr(10)) + 1}" in err


def test_fallback_when_rustc_exits_without_diagnostics(monkeypatch):
    class R:
        returncode = 1
        stderr = "error: unknown edition flag\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert RustHandler.verify_changes(MODULE, MODULE, "x.rs") == (True, None)
    assert RustHandler.verify_changes(MODULE, UNCLOSED, "x.rs")[0] is False


# -- duplicates (advisory) ------------------------------------------------------

def test_detect_duplicates_flags_a_repeated_fn_but_not_repeated_impl_blocks():
    two_impls = "struct A;\nimpl A { fn x() {} }\nimpl A { fn y() {} }\n"
    assert RustHandler.detect_duplicates(two_impls, two_impls) == (False, [])
    dup_fn = two_impls + "impl A { fn x() {} }\n"
    has, dups = RustHandler.detect_duplicates(two_impls, dup_fn)
    assert has and dups == ["fn x (lines 2, 4)"]


# -- seam: the real pipeline ----------------------------------------------------

@pytest.fixture(autouse=True)
def _codebase_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.delenv("ZIYA_FORCE_DRY_RUN", raising=False)


def _fuzzy_diff(name: str, old_line: str, new_line: str) -> str:
    # The context line carries a leading space the file does not have, so
    # patch and git apply refuse it and the difflib (fuzzy) stage places the
    # hunk -- the only case where the language check rolls back.
    return (
        f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
        "@@ -13,2 +13,2 @@\n"
        "     let v = cache.map.get(key).copied();\n"
        f"-{old_line}\n+{new_line}\n"
    )


def test_pipeline_keeps_a_valid_rust_diff(tmp_path):
    f = tmp_path / "cache.rs"
    f.write_text(MODULE)
    diff = _fuzzy_diff("cache.rs", "    match v {", "    match v { // patched")
    result = apply_diff_pipeline(diff, str(f))
    assert result.get("status") != "error", result.get("error")
    assert "match v { // patched" in f.read_text()
    assert not any(
        (h.get("error_details") or {}).get("stage") == "language_validation"
        for h in (result.get("hunk_statuses") or {}).values()
    ), result


@needs_rustc
def test_pipeline_rolls_back_a_rust_diff_that_breaks_syntax(tmp_path):
    f = tmp_path / "cache.rs"
    f.write_text(MODULE)
    diff = _fuzzy_diff("cache.rs", "    match v {", "    match v ")
    result = apply_diff_pipeline(diff, str(f))
    assert f.read_text() == MODULE
    assert result.get("status") == "error"
    assert "language validation" in (result.get("error") or "").lower()

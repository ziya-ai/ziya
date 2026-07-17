"""Regression tests for PenPal #80/#81/#76 [CWE-502/400]: bounded document
extraction.

`_check_document_safe_to_extract` refuses a file before any parser touches it
when (a) it is larger than the on-disk cap, or (b) it is a ZIP-container Office
format whose declared uncompressed size / compression ratio indicates a zip
bomb. This prevents a small crafted .docx/.xlsx/.pptx from OOMing the parser.
"""
import os
import io
import zipfile
import importlib

import pytest

import app.utils.document_extractor as de


def _reload_with_env(**env):
    """Reload the module so module-level cap constants pick up env overrides."""
    for k, v in env.items():
        os.environ[k] = str(v)
    importlib.reload(de)


def test_oversized_plain_file_refused(tmp_path):
    _reload_with_env(ZIYA_MAX_DOCUMENT_DISK_BYTES=100)
    f = tmp_path / "big.pdf"
    f.write_bytes(b"%PDF-1.4\n" + b"A" * 5000)
    reason = de._check_document_safe_to_extract(str(f), ".pdf")
    assert reason is not None
    assert "on disk" in reason


def test_small_plain_file_allowed(tmp_path):
    _reload_with_env(ZIYA_MAX_DOCUMENT_DISK_BYTES=10_000)
    f = tmp_path / "ok.pdf"
    f.write_bytes(b"%PDF-1.4\nshort")
    assert de._check_document_safe_to_extract(str(f), ".pdf") is None


def test_zip_bomb_uncompressed_size_refused(tmp_path):
    # A tiny .docx whose central directory declares a huge uncompressed member.
    _reload_with_env(
        ZIYA_MAX_DOCUMENT_DISK_BYTES=0,          # disable disk cap; test the zip guard
        ZIYA_MAX_ZIP_UNCOMPRESSED_BYTES=1_000_000,
        ZIYA_MAX_ZIP_COMPRESSION_RATIO=0,        # disable ratio; isolate size guard
    )
    f = tmp_path / "bomb.docx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        # 50 MB of highly-compressible zeros -> tiny on disk, huge uncompressed
        zf.writestr("word/document.xml", b"\x00" * (50 * 1024 * 1024))
    reason = de._check_document_safe_to_extract(str(f), ".docx")
    assert reason is not None
    assert "uncompressed" in reason


def test_zip_compression_ratio_refused(tmp_path):
    _reload_with_env(
        ZIYA_MAX_DOCUMENT_DISK_BYTES=0,
        ZIYA_MAX_ZIP_UNCOMPRESSED_BYTES=0,       # disable size guard; isolate ratio
        ZIYA_MAX_ZIP_COMPRESSION_RATIO=50,
    )
    f = tmp_path / "ratio.xlsx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/x.xml", b"\x00" * (10 * 1024 * 1024))
    reason = de._check_document_safe_to_extract(str(f), ".xlsx")
    assert reason is not None
    assert "ratio" in reason


def test_benign_office_zip_allowed(tmp_path):
    _reload_with_env(
        ZIYA_MAX_DOCUMENT_DISK_BYTES=10_000_000,
        ZIYA_MAX_ZIP_UNCOMPRESSED_BYTES=1_000_000_000,
        ZIYA_MAX_ZIP_COMPRESSION_RATIO=200,
    )
    f = tmp_path / "ok.docx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"<xml>hello world</xml>")
    assert de._check_document_safe_to_extract(str(f), ".docx") is None


def test_non_zip_office_ext_does_not_crash(tmp_path):
    # A .docx that isn't actually a zip must not raise — parser reports it.
    _reload_with_env(ZIYA_MAX_DOCUMENT_DISK_BYTES=0)
    f = tmp_path / "notzip.docx"
    f.write_bytes(b"this is not a zip file")
    assert de._check_document_safe_to_extract(str(f), ".docx") is None


def test_impl_refuses_bomb_before_parsing(tmp_path):
    # End-to-end: _extract_document_text_impl returns None (refused) for a bomb.
    _reload_with_env(
        ZIYA_MAX_DOCUMENT_DISK_BYTES=0,
        ZIYA_MAX_ZIP_UNCOMPRESSED_BYTES=1_000_000,
        ZIYA_MAX_ZIP_COMPRESSION_RATIO=0,
    )
    f = tmp_path / "bomb2.docx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\x00" * (50 * 1024 * 1024))
    # Should short-circuit to None without invoking python-docx.
    assert de._extract_document_text_impl(str(f)) is None


def test_negative_control_disabled_guard_allows_bomb(tmp_path):
    # Prove the guard is what refuses it: with all caps disabled, the same
    # bomb passes the safety check (it would then reach the parser).
    _reload_with_env(
        ZIYA_MAX_DOCUMENT_DISK_BYTES=0,
        ZIYA_MAX_ZIP_UNCOMPRESSED_BYTES=0,
        ZIYA_MAX_ZIP_COMPRESSION_RATIO=0,
    )
    f = tmp_path / "bomb3.docx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\x00" * (50 * 1024 * 1024))
    assert de._check_document_safe_to_extract(str(f), ".docx") is None


def teardown_module(_):
    for k in ("ZIYA_MAX_DOCUMENT_DISK_BYTES", "ZIYA_MAX_ZIP_UNCOMPRESSED_BYTES",
              "ZIYA_MAX_ZIP_COMPRESSION_RATIO"):
        os.environ.pop(k, None)
    importlib.reload(de)

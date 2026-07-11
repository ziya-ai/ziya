"""
Regression coverage for PenPal #127 [CWE-476]: NULL deref via unvalidated
JSON deserialization in PdfIndex cache loading.

json.loads() can legally return a non-dict (null/int/string) — from a
truncated/partial cache write, or a file planted by a local process. Both
cache-load paths (`meta.json` via PdfIndex.load, `bm25.json` via
_load_or_build_bm25) consumed the result as a dict with no type guard, so a
non-dict crashed every PDF MCP tool with AttributeError instead of the cache
cleanly rebuilding. Fixed with isinstance(..., dict) guards that fall through
to a cache miss / rebuild.
"""
import json
import os
import tempfile
import shutil

import pytest

import app.utils.pdf_rag as pdf_rag
from app.utils.pdf_rag import PdfIndex, _cache_key_for


@pytest.fixture
def project(monkeypatch):
    """A tmp project root + a real (tiny) file to key the cache off, with the
    cache dir pre-created so we can plant a poisoned cache entry."""
    root = tempfile.mkdtemp()
    monkeypatch.setattr(pdf_rag, "_get_project_root", lambda: root)
    # A real file so os.stat() in _cache_key_for succeeds and the digest is stable.
    pdf_path = os.path.join(root, "doc.pdf")
    with open(pdf_path, "wb") as fh:
        fh.write(b"%PDF-1.7 minimal\n")
    _digest, cache_dir = _cache_key_for(pdf_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    yield pdf_path, cache_dir
    shutil.rmtree(root, ignore_errors=True)


class TestMetaJsonGuard:
    @pytest.mark.parametrize("poison", ["null", '"a string"', "12345", "[1,2,3]"])
    def test_non_dict_meta_is_cache_miss(self, project, poison):
        pdf_path, cache_dir = project
        (cache_dir / "meta.json").write_text(poison, encoding="utf-8")
        # Must return None (cache miss → rebuild), not raise AttributeError.
        assert PdfIndex.load(pdf_path) is None

    def test_malformed_meta_is_cache_miss(self, project):
        pdf_path, cache_dir = project
        (cache_dir / "meta.json").write_text("{not valid json", encoding="utf-8")
        assert PdfIndex.load(pdf_path) is None

    def test_valid_dict_meta_loads(self, project):
        pdf_path, cache_dir = project
        (cache_dir / "meta.json").write_text(
            json.dumps({"page_count": 5, "total_tokens": 100, "light": False}),
            encoding="utf-8",
        )
        idx = PdfIndex.load(pdf_path)
        assert idx is not None
        # The properties that would have crashed on a None meta now work.
        assert idx.page_count == 5
        assert idx.total_tokens == 100
        assert idx.is_light is False


class TestBm25JsonGuard:
    @pytest.mark.parametrize("poison", ["null", '"x"', "42"])
    def test_non_dict_bm25_rebuilds_not_crashes(self, project, poison):
        pdf_path, cache_dir = project
        (cache_dir / "meta.json").write_text(
            json.dumps({"page_count": 1, "total_tokens": 1, "light": False}),
            encoding="utf-8",
        )
        (cache_dir / "bm25.json").write_text(poison, encoding="utf-8")
        idx = PdfIndex.load(pdf_path)
        assert idx is not None
        # _load_or_build_bm25 must return a dict (rebuilt), never the poisoned
        # non-dict — so search()'s `index.get("n_docs")` cannot AttributeError.
        result = idx._load_or_build_bm25()
        assert isinstance(result, dict)


class TestNegativeControlPreFix:
    """Proves the guard is non-vacuous: consuming a null cache as a dict
    (the pre-fix behavior) is an AttributeError."""

    def test_null_meta_get_is_attributeerror(self):
        meta = json.loads("null")   # None
        with pytest.raises(AttributeError):
            meta.get("page_count", 0)   # exactly the pre-fix property crash

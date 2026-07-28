"""
Tests for app.utils.task_artifacts — the run-scoped output-artifact
collection engine behind the emit_artifact builtin tool.

Covers:
  - collector lifecycle (start/emit/finish, ContextVar isolation)
  - part validation and normalization for text/file/data types
  - grouping vocabulary passthrough (group/label/seq)
  - hierarchy stamping (block_id from collector, iteration from ctx)
  - path authorization for file parts (project root + readable grants)
  - part-count cap
  - blob persistence round trip (plaintext path; encryption is policy-off
    by default in tests)
"""

import contextvars
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.task_artifacts import (
    MAX_PARTS_PER_TASK,
    MAX_TEXT_CHARS,
    build_part,
    collection_active,
    emit_part,
    finish_artifact_collection,
    read_artifact_blob,
    save_artifact_blob,
    start_artifact_collection,
)


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with patch("app.context.get_project_root", return_value=str(root)):
        yield root


@pytest.fixture
def collector():
    token = start_artifact_collection(block_id="task-1", artifacts_dir=None)
    yield token
    # Test may have already finished it; reset defensively.
    try:
        finish_artifact_collection(token)
    except (ValueError, LookupError, RuntimeError):
        pass


class TestCollectorLifecycle:
    def test_inactive_by_default(self):
        assert collection_active() is False

    def test_active_inside_collection(self, collector):
        assert collection_active() is True

    def test_emit_without_collector_fails(self):
        ok, msg = emit_part({"name": "x"})
        assert ok is False
        assert "no active artifact collection" in msg

    def test_emit_and_drain_preserves_order(self, collector):
        for i in range(3):
            part, err = build_part(name=f"p{i}", part_type="text", text=f"body {i}")
            assert err is None
            ok, _ = emit_part(part)
            assert ok
        parts = finish_artifact_collection(collector)
        assert [p["name"] for p in parts] == ["p0", "p1", "p2"]

    def test_finish_clears_collector(self, collector):
        finish_artifact_collection(collector)
        assert collection_active() is False

    def test_part_cap_enforced(self, collector):
        for i in range(MAX_PARTS_PER_TASK):
            part, _ = build_part(name=f"p{i}", part_type="text", text="x")
            ok, _ = emit_part(part)
            assert ok
        part, _ = build_part(name="overflow", part_type="text", text="x")
        ok, msg = emit_part(part)
        assert ok is False
        assert "limit" in msg.lower()

    def test_contextvar_isolation(self):
        """A collector opened in one context is invisible in another."""
        def _in_fresh_context():
            return collection_active()

        token = start_artifact_collection(block_id="b")
        try:
            ctx = contextvars.Context()
            assert ctx.run(_in_fresh_context) is False
            assert collection_active() is True
        finally:
            finish_artifact_collection(token)


class TestBuildPartValidation:
    def test_text_part(self):
        part, err = build_part(name="notes", part_type="text", text="hello")
        assert err is None
        assert part["part_type"] == "text"
        assert part["text"] == "hello"
        assert part["status"] == "ok"
        assert part["created_at"] > 0

    def test_text_part_requires_text(self):
        part, err = build_part(name="notes", part_type="text", text="")
        assert part is None
        assert "non-empty" in err

    def test_text_truncated_at_cap(self):
        part, err = build_part(name="big", part_type="text", text="x" * (MAX_TEXT_CHARS + 10))
        assert err is None
        assert len(part["text"]) <= MAX_TEXT_CHARS + 30
        assert part["text"].endswith("[truncated at emit]")

    def test_data_part(self):
        part, err = build_part(name="metrics", part_type="data", data={"count": 5})
        assert err is None
        assert part["data"] == {"count": 5}

    def test_data_part_requires_dict(self):
        part, err = build_part(name="metrics", part_type="data", data=None)
        assert part is None
        assert "JSON object" in err

    def test_data_part_rejects_unserializable(self):
        part, err = build_part(name="bad", part_type="data", data={"f": object()})
        assert part is None
        assert "serializable" in err

    def test_invalid_part_type(self):
        part, err = build_part(name="x", part_type="image")
        assert part is None
        assert "invalid part_type" in err

    def test_name_required(self):
        part, err = build_part(name="", part_type="text", text="x")
        assert part is None
        part, err2 = build_part(name="   ", part_type="text", text="x")
        assert part is None and err2

    def test_grouping_fields_passthrough(self):
        part, err = build_part(
            name="a", part_type="text", text="x",
            group="issue-5", label="before", seq=0,
        )
        assert err is None
        assert part["group"] == "issue-5"
        assert part["label"] == "before"
        assert part["seq"] == 0

    def test_seq_must_be_int(self):
        part, err = build_part(name="a", part_type="text", text="x", seq="first")
        assert part is None
        assert "seq" in err

    def test_extra_fields_do_not_override(self):
        part, err = build_part(
            name="a", part_type="text", text="x",
            extra={"rendered": True, "name": "evil-override"},
        )
        assert err is None
        assert part["rendered"] is True
        assert part["name"] == "a"  # setdefault, not overwrite


class TestHierarchyStamping:
    def test_block_id_from_collector(self):
        token = start_artifact_collection(block_id="fix-block")
        try:
            part, err = build_part(name="a", part_type="text", text="x")
            assert err is None
            assert part["block_id"] == "fix-block"
        finally:
            finish_artifact_collection(token)

    def test_iteration_from_context(self):
        from app.context import set_task_iteration_context, reset_task_iteration_context
        token = start_artifact_collection(block_id="fix-block")
        iter_token = set_task_iteration_context("repeat-1", 4)
        try:
            part, err = build_part(name="a", part_type="text", text="x")
            assert err is None
            assert part["iteration"] == 4
            assert part["iteration_owner"] == "repeat-1"
        finally:
            reset_task_iteration_context(iter_token)
            finish_artifact_collection(token)

    def test_no_stamps_outside_collection(self):
        part, err = build_part(name="a", part_type="text", text="x")
        assert err is None
        assert "block_id" not in part
        assert "iteration" not in part


class TestFilePartAuthorization:
    def test_file_inside_project_root(self, project_root):
        f = project_root / "report.md"
        f.write_text("# report")
        part, err = build_part(name="report", part_type="file", file_path="report.md")
        assert err is None
        assert part["file_uri"] == str(f.resolve())
        assert part["media_type"] == "text/markdown"
        assert part["size_bytes"] == len("# report")

    def test_missing_file_rejected(self, project_root):
        part, err = build_part(name="x", part_type="file", file_path="nope.txt")
        assert part is None
        assert "not found" in err

    def test_outside_root_rejected_without_grant(self, project_root, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        with patch("app.context.get_task_readable_paths", return_value=None):
            part, err = build_part(name="x", part_type="file", file_path=str(outside))
        assert part is None
        assert "outside the project root" in err

    def test_outside_root_allowed_with_file_grant(self, project_root, tmp_path):
        outside = tmp_path / "granted.txt"
        outside.write_text("ok")
        grant = [{"path": str(outside), "is_dir": False}]
        with patch("app.context.get_task_readable_paths", return_value=grant):
            part, err = build_part(name="x", part_type="file", file_path=str(outside))
        assert err is None
        assert part["file_uri"] == str(outside.resolve())

    def test_outside_root_allowed_with_dir_grant(self, project_root, tmp_path):
        d = tmp_path / "shared"
        d.mkdir()
        f = d / "data.json"
        f.write_text("{}")
        grant = [{"path": str(d), "is_dir": True}]
        with patch("app.context.get_task_readable_paths", return_value=grant):
            part, err = build_part(name="x", part_type="file", file_path=str(f))
        assert err is None

    def test_internal_file_uri_bypasses_validation(self):
        """The render-capture path passes file_uri directly — no fs checks."""
        part, err = build_part(
            name="render", part_type="file",
            file_uri="/runs/abc/artifacts/render.png", media_type="image/png",
        )
        assert err is None
        assert part["file_uri"] == "/runs/abc/artifacts/render.png"
        assert part["media_type"] == "image/png"

    def test_file_part_requires_path_or_uri(self):
        part, err = build_part(name="x", part_type="file")
        assert part is None
        assert "file_path" in err


class TestBlobPersistence:
    def test_round_trip(self, tmp_path):
        token = start_artifact_collection(
            block_id="b", artifacts_dir=str(tmp_path / "artifacts"),
        )
        try:
            uri, err = save_artifact_blob("render.png", b"\x89PNG-bytes")
            assert err is None
            assert Path(uri).exists()
            assert read_artifact_blob(uri) == b"\x89PNG-bytes"
        finally:
            finish_artifact_collection(token)

    def test_collision_gets_suffixed(self, tmp_path):
        token = start_artifact_collection(
            block_id="b", artifacts_dir=str(tmp_path / "artifacts"),
        )
        try:
            uri1, _ = save_artifact_blob("out.png", b"one")
            uri2, _ = save_artifact_blob("out.png", b"two")
            assert uri1 != uri2
            assert read_artifact_blob(uri1) == b"one"
            assert read_artifact_blob(uri2) == b"two"
        finally:
            finish_artifact_collection(token)

    def test_filename_sanitized(self, tmp_path):
        token = start_artifact_collection(
            block_id="b", artifacts_dir=str(tmp_path / "artifacts"),
        )
        try:
            uri, err = save_artifact_blob("../../evil path!!.png", b"x")
            assert err is None
            p = Path(uri)
            # Must land inside the artifacts dir, not escape it
            assert str(p.parent) == str(tmp_path / "artifacts")
            assert ".." not in p.name
        finally:
            finish_artifact_collection(token)

    def test_no_dir_configured(self):
        token = start_artifact_collection(block_id="b", artifacts_dir=None)
        try:
            uri, err = save_artifact_blob("x.png", b"x")
            assert uri is None
            assert "artifacts directory" in err
        finally:
            finish_artifact_collection(token)

    def test_no_collector(self):
        uri, err = save_artifact_blob("x.png", b"x")
        assert uri is None
        assert "no active artifact collection" in err

    def test_read_missing_blob_returns_none(self):
        assert read_artifact_blob("/nonexistent/path.png") is None

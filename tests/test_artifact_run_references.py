"""
Tests for cross-run and same-run artifact references (``from_run``).

A multi-card stack built from Call blocks is ONE run, while separately
launched cards are separate runs, so an aggregating report has to reach
both its own earlier evidence and a prior run's.  These cover:

  - ``self``/``current``/this-run-id -> the blob is referenced IN PLACE
    (no second copy of one piece of evidence in the same report)
  - a sibling run id -> the blob is COPIED in, and provenance recorded
  - resolution by card name / card id -> most recent FINISHED run
  - ambiguity and miss cases produce actionable errors, never a guess
  - resolution is memoized, so a baseline cannot shift mid-report
  - traversal attempts in the run reference are refused
  - ``list_run_artifacts`` indexes a run without its payloads
"""

import sys
import types
from pathlib import Path

import pytest

from app.utils import task_artifacts as ta
from app.utils.task_artifacts import (
    build_part,
    finish_artifact_collection,
    list_run_artifacts,
    start_artifact_collection,
)


RUN_A = "11111111-aaaa-4aaa-8aaa-111111111111"   # prior run (the baseline)
RUN_B = "22222222-bbbb-4bbb-8bbb-222222222222"   # the run under test


@pytest.fixture
def runs_layout(tmp_path):
    """A project dir with two sibling runs, each with an artifacts dir."""
    project = tmp_path / "project"
    a = project / "task_runs" / RUN_A / "artifacts"
    b = project / "task_runs" / RUN_B / "artifacts"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "before.png").write_bytes(b"\x89PNG\r\n\x1a\nBEFORE")
    (b / "after.png").write_bytes(b"\x89PNG\r\n\x1a\nAFTER")
    return types.SimpleNamespace(project=project, a=a, b=b)


@pytest.fixture
def collector(runs_layout):
    """Collector positioned in RUN_B, the run doing the reporting."""
    token = start_artifact_collection(
        block_id="report", artifacts_dir=str(runs_layout.b), run_id=RUN_B,
    )
    yield token
    try:
        finish_artifact_collection(token)
    except (ValueError, LookupError, RuntimeError):
        pass


def _fake_run(run_id, card_id, name=None, completed_at=None, started_at=1.0):
    snap = {"name": name} if name is not None else None
    return types.SimpleNamespace(
        id=run_id, card_id=card_id, card_snapshot=snap,
        completed_at=completed_at, started_at=started_at,
    )


def _install_fake_run_storage(monkeypatch, runs, get_map=None):
    """Stand in for app.storage.task_runs.TaskRunStorage.

    The real one decrypts run records off disk; resolution only needs
    ``id`` / ``card_id`` / ``card_snapshot`` / timestamps.  Also counts
    ``list`` calls so memoization can be asserted rather than assumed.
    """
    calls = {"list": 0}

    class FakeStorage:
        def __init__(self, project_dir):
            self.project_dir = project_dir

        def list(self, card_id=None):
            calls["list"] += 1
            return list(runs)

        def get(self, run_id):
            return (get_map or {}).get(run_id)

    mod = types.ModuleType("app.storage.task_runs")
    mod.TaskRunStorage = FakeStorage
    monkeypatch.setitem(sys.modules, "app.storage.task_runs", mod)
    return calls


class TestSameRunReferences:
    """A stack's later block reaching evidence its earlier block emitted.

    This is the case ``file_path`` cannot serve: the run's artifacts dir
    lives under the Ziya home, not under the project root, so path
    validation refuses it.  Without a self-reference the evidence is
    unreachable from inside the run that produced it.
    """

    @pytest.mark.parametrize("ref", ["self", "current", "this", "THIS-RUN"])
    def test_aliases_resolve_to_current_run(self, collector, ref):
        run_id, err = ta._resolve_run_reference(ref)
        assert err is None
        assert run_id == RUN_B

    def test_own_run_id_resolves(self, collector):
        run_id, err = ta._resolve_run_reference(RUN_B)
        assert err is None
        assert run_id == RUN_B

    def test_self_reference_is_not_copied(self, collector, runs_layout):
        before = sorted(p.name for p in runs_layout.b.iterdir())
        part, err = build_part(
            name="own", part_type="file", file_path="after.png",
            from_run="self", group="subject", label="after",
        )
        assert err is None, err
        # Points at the existing blob, and created no duplicate.
        assert Path(part["file_uri"]).name == "after.png"
        assert sorted(p.name for p in runs_layout.b.iterdir()) == before

    def test_self_reference_records_no_source_run(self, collector):
        """Provenance is for a FOREIGN baseline; here it would be noise."""
        part, err = build_part(
            name="own", part_type="file", file_path="after.png",
            from_run="self",
        )
        assert err is None, err
        assert "source_run_id" not in part


class TestForeignRunReferences:
    def test_sibling_run_blob_is_copied_in(self, collector, runs_layout):
        part, err = build_part(
            name="baseline", part_type="file", file_path="before.png",
            from_run=RUN_A, group="subject", label="before",
        )
        assert err is None, err
        copied = Path(part["file_uri"])
        assert copied.parent == runs_layout.b
        assert copied.read_bytes() == (runs_layout.a / "before.png").read_bytes()
        # Source is left untouched.
        assert (runs_layout.a / "before.png").exists()

    def test_resolved_run_id_recorded_as_provenance(self, collector):
        part, err = build_part(
            name="baseline", part_type="file", file_path="before.png",
            from_run=RUN_A,
        )
        assert err is None, err
        assert part["source_run_id"] == RUN_A

    def test_media_type_inferred(self, collector):
        part, err = build_part(
            name="baseline", part_type="file", file_path="before.png",
            from_run=RUN_A,
        )
        assert err is None, err
        assert part["media_type"] == "image/png"

    def test_pairs_share_a_group_for_side_by_side(self, collector):
        """The shape the viewer reads as a comparison."""
        a, err_a = build_part(
            name="b", part_type="file", file_path="before.png",
            from_run=RUN_A, group="d2/D-020", label="before",
        )
        b, err_b = build_part(
            name="a", part_type="file", file_path="after.png",
            from_run="self", group="d2/D-020", label="after",
        )
        assert err_a is None and err_b is None
        assert a["group"] == b["group"] == "d2/D-020"
        assert {a["label"], b["label"]} == {"before", "after"}


class TestCardReferences:
    def test_card_name_picks_most_recent_finished_run(
        self, collector, monkeypatch,
    ):
        _install_fake_run_storage(monkeypatch, [
            _fake_run(RUN_A, "card-1", name="Stage 1 sweep", completed_at=100.0),
            _fake_run("no-artifacts-run", "card-1", name="Stage 1 sweep",
                      completed_at=200.0),
        ])
        # The newer run has no artifacts dir on disk, so it is unusable
        # and resolution falls to the one that actually has evidence.
        run_id, err = ta._resolve_run_reference("Stage 1 sweep")
        assert err is None, err
        assert run_id == RUN_A

    def test_card_name_is_case_insensitive(self, collector, monkeypatch):
        _install_fake_run_storage(monkeypatch, [
            _fake_run(RUN_A, "card-1", name="Stage 1 Sweep", completed_at=1.0),
        ])
        run_id, err = ta._resolve_run_reference("stage 1 sweep")
        assert err is None, err
        assert run_id == RUN_A

    def test_card_id_resolves(self, collector, monkeypatch):
        _install_fake_run_storage(monkeypatch, [
            _fake_run(RUN_A, "card-1", name="Stage 1", completed_at=1.0),
        ])
        run_id, err = ta._resolve_run_reference("card-1")
        assert err is None, err
        assert run_id == RUN_A

    def test_unfinished_run_used_only_as_last_resort(
        self, collector, monkeypatch,
    ):
        _install_fake_run_storage(monkeypatch, [
            _fake_run(RUN_A, "card-1", name="S", completed_at=None,
                      started_at=50.0),
        ])
        run_id, err = ta._resolve_run_reference("S")
        assert err is None, err
        assert run_id == RUN_A

    def test_ambiguous_name_refuses_to_guess(self, collector, monkeypatch):
        _install_fake_run_storage(monkeypatch, [
            _fake_run(RUN_A, "card-1", name="Sweep", completed_at=1.0),
            _fake_run(RUN_A, "card-2", name="Sweep", completed_at=2.0),
        ])
        run_id, err = ta._resolve_run_reference("Sweep")
        assert run_id is None
        assert "different cards" in err

    def test_unknown_reference_is_explicit(self, collector, monkeypatch):
        _install_fake_run_storage(monkeypatch, [])
        run_id, err = ta._resolve_run_reference("nope")
        assert run_id is None
        assert "not a run id" in err

    def test_card_with_runs_but_no_artifacts_dir(self, collector, monkeypatch):
        _install_fake_run_storage(monkeypatch, [
            _fake_run("ghost-run", "card-9", name="Ghost", completed_at=1.0),
        ])
        run_id, err = ta._resolve_run_reference("Ghost")
        assert run_id is None
        assert "artifacts directory" in err


class TestResolutionMemoized:
    def test_repeated_reference_resolves_once(self, collector, monkeypatch):
        """A report's baseline must not shift while it is being written.

        Also the cost argument: run records are ~100 KB and encrypted at
        rest, so re-reading the whole history per emitted part would make
        a few-hundred-part gallery pathological.
        """
        calls = _install_fake_run_storage(monkeypatch, [
            _fake_run(RUN_A, "card-1", name="Stage 1", completed_at=1.0),
        ])
        first, _ = ta._resolve_run_reference("Stage 1")
        second, _ = ta._resolve_run_reference("Stage 1")
        third, _ = ta._resolve_run_reference("Stage 1")
        assert first == second == third == RUN_A
        assert calls["list"] == 1

    def test_failure_is_memoized_too(self, collector, monkeypatch):
        calls = _install_fake_run_storage(monkeypatch, [])
        _, err1 = ta._resolve_run_reference("ghost")
        _, err2 = ta._resolve_run_reference("ghost")
        assert err1 and err2
        assert calls["list"] == 1

    def test_self_reference_never_touches_storage(self, collector, monkeypatch):
        calls = _install_fake_run_storage(monkeypatch, [])
        ta._resolve_run_reference("self")
        assert calls["list"] == 0


class TestReferenceConfinement:
    @pytest.mark.parametrize("ref", [
        "../../etc", "..", "a/b", "a\\b", "x\x00y",
    ])
    def test_traversal_forms_refused(self, collector, ref):
        _, _, err = ta._copy_from_sibling_run(ref, "before.png")
        assert err is not None

    def test_filename_must_be_bare(self, collector):
        _, _, err = ta._copy_from_sibling_run(RUN_A, "../before.png")
        assert err is not None

    def test_missing_filename_reported(self, collector):
        _, _, err = ta._copy_from_sibling_run(RUN_A, None)
        assert err is not None
        assert "filename" in err

    def test_absent_artifact_names_the_run_it_looked_in(self, collector):
        _, _, err = ta._copy_from_sibling_run(RUN_A, "nonexistent.png")
        assert err is not None
        assert RUN_A in err


class TestListRunArtifacts:
    def _run_with_outputs(self, outputs):
        artifact = types.SimpleNamespace(outputs=outputs)
        return types.SimpleNamespace(id=RUN_A, artifact=artifact)

    def test_index_excludes_payloads(self, collector, monkeypatch):
        """The whole point: an index a model can afford to read."""
        _install_fake_run_storage(
            monkeypatch,
            [_fake_run(RUN_A, "card-1", name="S", completed_at=1.0)],
            get_map={RUN_A: self._run_with_outputs([
                {"part_type": "file", "name": "before",
                 "file_uri": "/abs/path/to/before.png",
                 "media_type": "image/png", "group": "g", "label": "before",
                 "status": "ok", "block_id": "b1", "iteration": 0},
                {"part_type": "text", "name": "notes",
                 "text": "x" * 5000, "group": "g"},
            ])},
        )
        entries, err = list_run_artifacts(RUN_A)
        assert err is None, err
        assert len(entries) == 2
        blob, note = entries
        # Only the bare filename — the form from_run accepts.
        assert blob["filename"] == "before.png"
        assert blob["label"] == "before"
        assert blob["iteration"] == 0
        # No payload of any kind leaked into the index.
        for e in entries:
            assert "text" not in e
            assert "data" not in e
            assert "file_uri" not in e
        assert note["name"] == "notes"

    def test_limit_is_honored(self, collector, monkeypatch):
        _install_fake_run_storage(
            monkeypatch,
            [_fake_run(RUN_A, "card-1", name="S", completed_at=1.0)],
            get_map={RUN_A: self._run_with_outputs([
                {"part_type": "text", "name": f"n{i}"} for i in range(20)
            ])},
        )
        entries, err = list_run_artifacts(RUN_A, limit=5)
        assert err is None, err
        assert len(entries) == 5

    def test_run_without_artifact_record(self, collector, monkeypatch):
        _install_fake_run_storage(
            monkeypatch,
            [_fake_run(RUN_A, "card-1", name="S", completed_at=1.0)],
            get_map={RUN_A: types.SimpleNamespace(id=RUN_A, artifact=None)},
        )
        entries, err = list_run_artifacts(RUN_A)
        assert err is None, err
        assert entries == []

    def test_unresolvable_reference_errors(self, collector, monkeypatch):
        _install_fake_run_storage(monkeypatch, [])
        entries, err = list_run_artifacts("ghost")
        assert entries is None
        assert err


class TestNoCollector:
    def test_resolution_outside_a_run(self):
        run_id, err = ta._resolve_run_reference("self")
        assert run_id is None
        assert err

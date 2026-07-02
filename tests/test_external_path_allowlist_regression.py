"""
Regression tests for PenPal #54 (CWE-22/CWE-200, HIGH) and the follow-on
regression it introduced.

resolve_external_path() now raises ExternalPathNotAllowed for any
"[external]/..." path that is not on the user-approved allowlist
(app.services.folder_service._explicit_external_paths) — see the
security commit "honor [external] paths only when explicitly approved".

That fix, landed as a chokepoint raise with no caller updates, turned
ExternalPathNotAllowed (a ValueError subclass) into an UNCAUGHT
exception at all 5 call sites that invoke resolve_external_path() in a
loop over multiple files — meaning a single unapproved/stale external
path would abort an entire file-load batch instead of just being
skipped. This file pins:

  1. resolve_external_path()'s own allow/deny contract.
  2. Each of the 5 call sites degrading gracefully (skip-one,
     continue-batch) rather than propagating the exception.
"""

import os
import pytest
from unittest.mock import patch


# ── 1. resolve_external_path() / ExternalPathNotAllowed contract ───

class TestResolveExternalPathContract:
    def test_non_external_path_passthrough(self, tmp_path):
        from app.utils.file_utils import resolve_external_path
        result = resolve_external_path("src/main.py", str(tmp_path))
        assert result == os.path.join(str(tmp_path), "src/main.py")

    def test_unapproved_external_path_raises(self):
        from app.utils.file_utils import resolve_external_path, ExternalPathNotAllowed
        with patch("app.services.folder_service._explicit_external_paths", set()):
            with pytest.raises(ExternalPathNotAllowed):
                resolve_external_path("[external]/etc/passwd", "/proj")

    def test_approved_external_path_resolves(self, tmp_path):
        from app.utils.file_utils import resolve_external_path
        approved_dir = tmp_path / "outside"
        approved_dir.mkdir()
        target = approved_dir / "notes.txt"
        target.write_text("hi")
        with patch("app.services.folder_service._explicit_external_paths",
                    {str(approved_dir)}):
            result = resolve_external_path(f"[external]{target}", "/proj")
        assert os.path.realpath(result) == os.path.realpath(str(target))

    def test_approved_ancestor_covers_descendants(self, tmp_path):
        """Approving a directory must cover files nested under it."""
        from app.utils.file_utils import resolve_external_path
        approved_dir = tmp_path / "outside"
        nested = approved_dir / "sub" / "deep.txt"
        nested.parent.mkdir(parents=True)
        nested.write_text("hi")
        with patch("app.services.folder_service._explicit_external_paths",
                    {str(approved_dir)}):
            result = resolve_external_path(f"[external]{nested}", "/proj")
        assert os.path.realpath(result) == os.path.realpath(str(nested))

    def test_sibling_of_approved_path_not_covered(self, tmp_path):
        """A directory that merely shares a name prefix with an approved
        path must not be treated as approved (no sibling-prefix bypass)."""
        from app.utils.file_utils import resolve_external_path, ExternalPathNotAllowed
        approved_dir = tmp_path / "outside"
        approved_dir.mkdir()
        sibling = tmp_path / "outside-evil"
        sibling.mkdir()
        target = sibling / "secret.txt"
        target.write_text("nope")
        with patch("app.services.folder_service._explicit_external_paths",
                    {str(approved_dir)}):
            with pytest.raises(ExternalPathNotAllowed):
                resolve_external_path(f"[external]{target}", "/proj")


# ── 2. Call-site regression: must skip, not crash ───────────────────

class TestExtractFilePathsFromInputSkipsUnapproved:
    """agent.py:extract_file_paths_from_input — site 2."""

    def test_unapproved_path_skipped_not_raised(self, tmp_path):
        from app.agents.agent import extract_file_paths_from_input

        ok_dir = tmp_path
        ok_file = ok_dir / "ok.py"
        ok_file.write_text("x = 1")

        x = {"config": {"files": ["[external]/etc/passwd", "ok.py"]}}
        with patch("app.services.folder_service._explicit_external_paths", set()):
            with patch("app.context.get_project_root", return_value=str(ok_dir)):
                # extract_file_paths_from_input tries get_project_root via
                # ImportError fallback path internally; ensure resolution
                # lands in ok_dir regardless of which branch fires.
                with patch("app.agents.agent.ziya_env", return_value=str(ok_dir)):
                    result = extract_file_paths_from_input(x)

        # Must not raise; the approved plain file must still be present.
        assert any(p.endswith("ok.py") for p in result)
        assert not any("passwd" in p for p in result)

    def test_all_unapproved_yields_empty_list_not_exception(self):
        from app.agents.agent import extract_file_paths_from_input

        x = {"config": {"files": ["[external]/etc/shadow", "[external]/etc/passwd"]}}
        with patch("app.services.folder_service._explicit_external_paths", set()):
            result = extract_file_paths_from_input(x)  # must not raise
        assert result == []


class TestAccurateTokenCountRouteSkipsUnapproved:
    """token_routes.py:get_accurate_token_counts — site 5, exercised via
    the real FastAPI route so the try/except wiring is proven end-to-end."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routes.token_routes import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_unapproved_and_approved_paths_in_same_batch(self, client, tmp_path):
        ok_file = tmp_path / "ok.py"
        ok_file.write_text("x = 1\n")

        with patch("app.services.folder_service._explicit_external_paths", set()):
            with patch("app.context.get_project_root", return_value=str(tmp_path)):
                resp = client.post("/api/accurate-token-count", json={
                    "file_paths": ["[external]/etc/passwd", "ok.py"],
                })

        assert resp.status_code == 200
        body = resp.json()
        results = body["results"]
        # The unapproved path must degrade to a reported error, not a 500
        # that would have aborted the whole batch.
        assert results["[external]/etc/passwd"]["error"] == "File not found"
        assert results["[external]/etc/passwd"]["accurate_count"] == 0
        # The legitimate file in the same batch must still be processed.
        assert "ok.py" in results
        assert "error" not in results["ok.py"]


class TestFileStateManagerRefreshSkipsUnapproved:
    """file_state_manager.py:refresh_file_from_disk — site 4."""

    def test_unapproved_external_path_returns_false_not_raises(self, tmp_path):
        from app.utils.file_state_manager import FileStateManager

        mgr = FileStateManager()
        conv_id = "conv-test-penpal-54"
        mgr.conversation_states[conv_id] = {}

        # Seed a fake FileState so the function proceeds past the
        # "not in conversation" early return and reaches resolve_external_path.
        from app.utils.file_state_manager import FileState
        mgr.conversation_states[conv_id]["[external]/etc/passwd"] = FileState(
            path="[external]/etc/passwd",
            content_hash="deadbeef",
            line_states={},
            original_content=["old"],
            current_content=["old"],
            last_seen_content=["old"],
            last_context_submission_content=["old"],
        )

        with patch("app.services.folder_service._explicit_external_paths", set()):
            result = mgr.refresh_file_from_disk(
                conv_id, "[external]/etc/passwd", str(tmp_path)
            )  # must not raise

        assert result is False


class TestExtractCodebaseFileLoopSkipsUnapproved:
    """agent.py:extract_codebase — site 3 (the try/except with the widened
    except tuple, inside get_combined_docs_from_files' sibling loop)."""

    def test_get_combined_docs_skips_unapproved_without_crashing(self, tmp_path):
        from app.agents.agent import get_combined_docs_from_files

        ok_file = tmp_path / "ok.py"
        ok_file.write_text("hello = 1\n")

        with patch("app.services.folder_service._explicit_external_paths", set()):
            with patch("app.context.get_project_root", return_value=str(tmp_path)):
                # Must not raise even though the external path is unapproved.
                result = get_combined_docs_from_files(
                    ["[external]/etc/passwd", "ok.py"], conversation_id="conv-penpal-54"
                )

        assert "passwd" not in result
        assert "ok.py" in result

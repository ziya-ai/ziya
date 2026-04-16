"""Tests for file watcher gitignore handling.

Validates that the file watcher correctly ignores files matched by .gitignore
patterns, including when the watcher's base_dir is above the project root
(the common case when Ziya is started from a parent directory).
"""
import os
import tempfile
import time
import pytest
from unittest.mock import MagicMock, patch

from app.utils.file_watcher import FileChangeHandler
from app.utils.file_state_manager import FileStateManager


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project structure with .gitignore."""
    project_dir = tmp_path / "workspace" / "my-project"
    project_dir.mkdir(parents=True)

    # Create .gitignore
    gitignore = project_dir / ".gitignore"
    gitignore.write_text("templates/\nbuild/\n*.pyc\n")

    # Create some files that should be ignored
    templates_dir = project_dir / "templates" / "static" / "js"
    templates_dir.mkdir(parents=True)
    (templates_dir / "app.js").write_text("// app")
    (templates_dir / "app.js.map").write_text("{}")

    build_dir = project_dir / "build"
    build_dir.mkdir()
    (build_dir / "output.bin").write_text("binary")

    # Create some files that should NOT be ignored
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')")

    return {
        "root": tmp_path,
        "project": project_dir,
    }


class TestShouldIgnorePathWithProjectGitignore:
    """Test _should_ignore_path when base_dir is the project root."""

    def test_ignores_templates_dir_files(self, temp_project):
        """Files under templates/ should be ignored per .gitignore."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["project"]),
        )
        abs_path = str(
            temp_project["project"] / "templates" / "static" / "js" / "app.js"
        )
        assert handler._should_ignore_path(abs_path) is True

    def test_ignores_build_dir(self, temp_project):
        """Files under build/ should be ignored per .gitignore."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["project"]),
        )
        abs_path = str(temp_project["project"] / "build" / "output.bin")
        assert handler._should_ignore_path(abs_path) is True

    def test_allows_src_files(self, temp_project):
        """Files under src/ should NOT be ignored."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["project"]),
        )
        abs_path = str(temp_project["project"] / "src" / "main.py")
        assert handler._should_ignore_path(abs_path) is False


class TestShouldIgnorePathWithParentBaseDir:
    """Test _should_ignore_path when base_dir is ABOVE the project root.

    This is the common scenario when the user starts Ziya from their home
    directory and then selects a project via the project manager.  The file
    watcher watches from HOME, but the project's .gitignore is nested
    several levels deep.
    """

    def test_ignores_templates_when_watching_from_parent(self, temp_project):
        """templates/ files should be ignored even when base_dir is a parent."""
        # Simulate watcher started from the parent (e.g. HOME) rather than
        # the project root.  The bulk gitignore scan from parent may miss
        # the project's .gitignore due to timeout/depth limits.
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["root"]),  # parent dir, not project root
        )
        # Force patterns loaded (they won't include project's .gitignore
        # because get_ignored_patterns scans from root which lacks one)
        handler._ignored_patterns_loaded = True
        handler.ignored_patterns = []
        handler.should_ignore_fn = lambda path: False

        abs_path = str(
            temp_project["project"] / "templates" / "static" / "js" / "app.js"
        )
        # The inline .gitignore check should catch this
        assert handler._should_ignore_path(abs_path) is True

    def test_ignores_build_when_watching_from_parent(self, temp_project):
        """build/ files should be ignored even when base_dir is a parent."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["root"]),
        )
        handler._ignored_patterns_loaded = True
        handler.ignored_patterns = []
        handler.should_ignore_fn = lambda path: False

        abs_path = str(temp_project["project"] / "build" / "output.bin")
        assert handler._should_ignore_path(abs_path) is True

    def test_allows_src_when_watching_from_parent(self, temp_project):
        """src/ files should NOT be ignored even when watching from parent."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["root"]),
        )
        handler._ignored_patterns_loaded = True
        handler.ignored_patterns = []
        handler.should_ignore_fn = lambda path: False

        abs_path = str(temp_project["project"] / "src" / "main.py")
        assert handler._should_ignore_path(abs_path) is False

    def test_inline_cache_is_populated(self, temp_project):
        """The inline gitignore cache should be populated after first check."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["root"]),
        )
        handler._ignored_patterns_loaded = True
        handler.ignored_patterns = []
        handler.should_ignore_fn = lambda path: False

        abs_path = str(
            temp_project["project"] / "templates" / "static" / "js" / "app.js"
        )
        handler._should_ignore_path(abs_path)

        # The project directory should now be in the inline cache
        project_str = str(temp_project["project"])
        assert project_str in handler._inline_gitignore_cache
        # And the cached entry should be a callable (the parsed patterns)
        assert handler._inline_gitignore_cache[project_str] is not None


class TestInlineGitignoreCache:
    """Test the inline .gitignore caching behavior."""

    def test_cache_does_not_grow_unbounded(self, temp_project):
        """Cache should evict entries when it exceeds the size limit."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["root"]),
        )
        # Pre-fill cache with 600 fake entries (over the 500 limit)
        for i in range(600):
            handler._inline_gitignore_cache[f"/fake/path/{i}"] = None

        # Trigger a real check which will add to cache and trigger eviction
        abs_path = str(
            temp_project["project"] / "templates" / "static" / "js" / "app.js"
        )
        handler._check_inline_gitignore(abs_path)

        # Cache should have been trimmed
        assert len(handler._inline_gitignore_cache) <= 500 + 10  # some slack for new entries

    def test_directories_without_gitignore_cached_as_none(self, temp_project):
        """Directories without .gitignore should be cached as None (not re-checked)."""
        handler = FileChangeHandler(
            FileStateManager(),
            str(temp_project["root"]),
        )

        abs_path = str(temp_project["project"] / "src" / "main.py")
        handler._check_inline_gitignore(abs_path)

        # The src directory has no .gitignore, should be cached as None
        src_dir = str(temp_project["project"] / "src")
        assert src_dir in handler._inline_gitignore_cache
        assert handler._inline_gitignore_cache[src_dir] is None


class TestNestedGitignoreFiles:
    """Test handling of nested .gitignore files in subdirectories."""

    def test_nested_gitignore_respected(self, tmp_path):
        """A .gitignore in a subdirectory should be respected."""
        project = tmp_path / "project"
        project.mkdir()

        # Root .gitignore: nothing special
        (project / ".gitignore").write_text("*.log\n")

        # Subdirectory with its own .gitignore
        sub = project / "subdir"
        sub.mkdir()
        (sub / ".gitignore").write_text("output/\n")

        output_dir = sub / "output"
        output_dir.mkdir()
        (output_dir / "result.txt").write_text("data")

        handler = FileChangeHandler(
            FileStateManager(),
            str(tmp_path),  # watching from parent
        )
        handler._ignored_patterns_loaded = True
        handler.ignored_patterns = []
        handler.should_ignore_fn = lambda path: False

        # File under subdir/output/ should be ignored by subdir's .gitignore
        abs_path = str(output_dir / "result.txt")
        assert handler._should_ignore_path(abs_path) is True

        # File directly in subdir should NOT be ignored
        (sub / "code.py").write_text("x=1")
        assert handler._should_ignore_path(str(sub / "code.py")) is False

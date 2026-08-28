"""
ASR PT-03 — id-to-path containment across the project storage classes.

Every storage class here keys files/directories off an id that arrives verbatim
from a URL path segment. ``ProjectStorage._project_dir()`` was a bare
``self.projects_dir / project_id``, and ``delete()`` hands that straight to
``shutil.rmtree`` -- so ``DELETE /api/v1/projects/../../<anything>`` reached an
unguarded recursive delete. The sibling classes had the same shape with an
arbitrary JSON read/write/delete at the end of it.

The fix put the check in the path *builders* rather than at the ``delete()``
call site, so get/update/delete/touch are all covered by one gate. These tests
assert that placement, not just the one endpoint.
"""

import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.storage.base import contained_path
from app.storage.chats import ChatStorage
from app.storage.contexts import ContextStorage
from app.storage.projects import ProjectStorage
from app.storage.skills import SkillStorage
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage

# Ids that must never resolve outside their own directory. Includes the
# absolute form, since ``Path("/a") / "/etc"`` discards the left operand
# entirely rather than nesting.
TRAVERSAL_IDS = [
    pytest.param("../evil", id="parent"),
    pytest.param("../../evil", id="grandparent"),
    pytest.param("../../../../../../tmp/evil", id="deep"),
    pytest.param("a/../../evil", id="mixed"),
    pytest.param("/etc/passwd", id="absolute"),
    pytest.param("/tmp/evil", id="absolute-tmp"),
]

EMPTY_IDS = [pytest.param("", id="empty"), pytest.param(None, id="none")]


# ---------------------------------------------------------------------------
# The shared primitive
# ---------------------------------------------------------------------------

class TestContainedPath:
    @pytest.mark.parametrize("name", TRAVERSAL_IDS)
    def test_traversal_rejected(self, tmp_path, name):
        with pytest.raises(ValueError):
            contained_path(tmp_path, name)

    @pytest.mark.parametrize("name", EMPTY_IDS)
    def test_empty_id_rejected(self, tmp_path, name):
        with pytest.raises(ValueError):
            contained_path(tmp_path, name)

    @pytest.mark.parametrize("name", [
        "0f8fad5b-d9cb-469f-a165-70867728950e",
        "chat-123.json",
        "nested/child.json",
    ])
    def test_contained_name_accepted(self, tmp_path, name):
        """Paired positive control -- a check that rejected everything would
        satisfy the cases above and break all storage."""
        assert contained_path(tmp_path, name) == tmp_path / name

    def test_message_does_not_leak_the_resolved_path(self, tmp_path):
        """The rejection is surfaced to an API caller; it should name the bad
        id, not the absolute filesystem location it would have reached."""
        with pytest.raises(ValueError) as exc:
            contained_path(tmp_path, "../../etc/passwd")
        assert "../../etc/passwd" in str(exc.value)
        assert str(tmp_path.parent.parent) not in str(exc.value)


# ---------------------------------------------------------------------------
# ProjectStorage -- the rmtree that motivated the finding
# ---------------------------------------------------------------------------

@pytest.fixture
def project_storage(tmp_path):
    return ProjectStorage(tmp_path / "ziya_home")


def _seed_project(storage, project_id="proj-001"):
    pdir = storage.projects_dir / project_id
    pdir.mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": "Seeded",
        "path": "/tmp/seeded",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return project_id


class TestProjectStorageTraversal:
    @pytest.mark.parametrize("project_id", TRAVERSAL_IDS)
    def test_project_dir_rejects_traversal(self, project_storage, project_id):
        with pytest.raises(ValueError):
            project_storage._project_dir(project_id)

    @pytest.mark.parametrize("project_id", TRAVERSAL_IDS)
    def test_delete_does_not_rmtree_outside_projects_dir(
        self, project_storage, tmp_path, project_id
    ):
        """The concrete impact: recursive deletion of a directory the caller
        does not own. Asserts the victim survives, not merely that an
        exception was raised.
        """
        victim = project_storage.projects_dir.parent / "evil"
        victim.mkdir(parents=True)
        (victim / "precious.txt").write_text("do not delete me")

        with pytest.raises(ValueError):
            project_storage.delete(project_id)

        assert victim.exists()
        assert (victim / "precious.txt").read_text() == "do not delete me"

    @pytest.mark.parametrize("project_id", TRAVERSAL_IDS)
    def test_read_paths_also_gated(self, project_storage, project_id):
        """Containment sits in the builder, so the read/update/touch entry
        points are covered by the same gate rather than delete() alone."""
        with pytest.raises(ValueError):
            project_storage.get(project_id)
        with pytest.raises(ValueError):
            project_storage.touch(project_id)

    def test_normal_project_still_round_trips(self, project_storage):
        """Positive control: the guard must not break ordinary operation."""
        project_id = _seed_project(project_storage)
        assert project_storage.get(project_id) is not None
        assert project_storage.delete(project_id) is True
        assert not (project_storage.projects_dir / project_id).exists()

    def test_missing_project_still_returns_false_not_raises(self, project_storage):
        """A well-formed but unknown id is a 404, not a validation error --
        the guard must not change that contract."""
        assert project_storage.delete("no-such-project") is False


# ---------------------------------------------------------------------------
# Sibling storage classes
# ---------------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "projects" / "proj-001"
    d.mkdir(parents=True)
    return d


class TestSiblingStorageTraversal:
    """Same URL-derived-id shape, one class per storage directory.

    ContextStorage and SkillStorage are exercised through their unbound path
    builders with a stub: their constructors need a TokenService and
    SkillStorage's writes the whole built-in skill set on init, neither of
    which is relevant to the containment property under test.
    """

    @pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
    def test_chat_storage(self, project_dir, bad_id):
        with pytest.raises(ValueError):
            ChatStorage(project_dir)._chat_file(bad_id)

    @pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
    def test_task_card_storage(self, project_dir, bad_id):
        with pytest.raises(ValueError):
            TaskCardStorage(project_dir)._card_file(bad_id)

    @pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
    def test_task_run_storage(self, project_dir, bad_id):
        storage = TaskRunStorage(project_dir)
        with pytest.raises(ValueError):
            storage._run_file(bad_id)
        with pytest.raises(ValueError):
            storage._iteration_dir(bad_id)

    @pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
    def test_context_storage(self, project_dir, bad_id):
        stub = SimpleNamespace(contexts_dir=project_dir / "contexts")
        with pytest.raises(ValueError):
            ContextStorage._context_file(stub, bad_id)

    @pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
    def test_skill_storage(self, project_dir, bad_id):
        stub = SimpleNamespace(skills_dir=project_dir / "skills")
        with pytest.raises(ValueError):
            SkillStorage._skill_file(stub, bad_id)

    def test_sibling_positive_controls(self, project_dir):
        """Each builder still produces the expected in-directory path."""
        chats = ChatStorage(project_dir)
        assert chats._chat_file("abc") == chats.chats_dir / "abc.json"

        cards = TaskCardStorage(project_dir)
        assert cards._card_file("abc") == cards.cards_dir / "abc.json"

        runs = TaskRunStorage(project_dir)
        assert runs._run_file("abc") == runs.runs_dir / "abc.json"

        ctx_stub = SimpleNamespace(contexts_dir=project_dir / "contexts")
        assert ContextStorage._context_file(ctx_stub, "abc") == (
            project_dir / "contexts" / "abc.json"
        )

        skill_stub = SimpleNamespace(skills_dir=project_dir / "skills")
        assert SkillStorage._skill_file(skill_stub, "abc") == (
            project_dir / "skills" / "abc.json"
        )


# ---------------------------------------------------------------------------
# Seam: a future storage class must not be able to skip the gate
# ---------------------------------------------------------------------------

class TestNoUngatedPathBuildersRemain:
    """Catch the next class rather than only the six fixed here.

    The per-class tests above enumerate what exists today; a storage class
    added next quarter with ``return self.foo_dir / f"{some_id}.json"`` would
    reintroduce the finding with the suite still green. Scan the package for
    that shape instead.

    Anchored on the identifier pattern (``self.<name>_dir /``), not on line
    numbers or file layout.
    """

    # Names that establish containment. The property under test is "the id is
    # contained", not "this particular helper was used" -- task_bindings.py
    # predates contained_path() and gates with validate_relative_path()
    # inline (PenPal #105), which is equally sound.
    GATES = {"contained_path", "validate_relative_path"}

    @staticmethod
    def _joins_caller_supplied_id(node: "ast.AST") -> bool:
        """True for ``self.<something>dir / <id>`` where <id> is caller data.

        A constant filename (``self._dir / "memories.json"``) carries no id, so
        there is nothing to contain and it is not a finding. Only an f-string
        with an interpolation, or a bare variable, qualifies.
        """
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            return False
        left = node.left
        if not (
            isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "self"
            and left.attr.endswith("dir")
        ):
            return False
        right = node.right
        if isinstance(right, ast.Name):
            return True
        return isinstance(right, ast.JoinedStr) and any(
            isinstance(v, ast.FormattedValue) for v in right.values
        )

    @classmethod
    def _ungated_builders(cls, path: Path) -> list:
        tree = ast.parse(path.read_text())
        offenders = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            gated = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in cls.GATES
                for n in ast.walk(func)
            )
            if gated:
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.Return) and node.value is not None:
                    if cls._joins_caller_supplied_id(node.value):
                        offenders.append(
                            f"{path.name}:{node.lineno}: {func.name}()"
                        )
        return offenders

    def test_no_ungated_path_builders_in_storage_package(self):
        storage_pkg = Path(__file__).resolve().parents[1] / "app" / "storage"
        offenders = []
        for path in sorted(storage_pkg.glob("*.py")):
            offenders.extend(self._ungated_builders(path))
        assert not offenders, (
            "Storage path builder returns a caller-supplied id joined onto a "
            "directory without any containment gate (ASR PT-03):\n  "
            + "\n  ".join(offenders)
        )

    def test_the_scan_would_actually_catch_the_old_code(self, tmp_path):
        """Negative control for the scanner itself.

        A structural invariant is only worth having if it matches the shape it
        claims to. Feed it the exact code PT-03 removed, plus the shapes that
        must NOT be flagged.
        """
        vulnerable = tmp_path / "vulnerable.py"
        vulnerable.write_text(
            "class S:\n"
            "    def _chat_file(self, chat_id):\n"
            '        return self.chats_dir / f"{chat_id}.json"\n'
            "    def _run_file(self, run_id):\n"
            "        return self.runs_dir / run_id\n"
        )
        found = self._ungated_builders(vulnerable)
        assert len(found) == 2
        assert "_chat_file()" in " ".join(found)
        assert "_run_file()" in " ".join(found)

        safe = tmp_path / "safe.py"
        safe.write_text(
            "class S:\n"
            "    def _chat_file(self, chat_id):\n"
            '        return contained_path(self.chats_dir, f"{chat_id}.json")\n'
            "    def _index(self):\n"
            '        return self.projects_dir / "_path_index.json"\n'
            "    def _bindings_file(self, chat_id):\n"
            '        filename = f"{chat_id}.bindings.json"\n'
            "        if not validate_relative_path(str(self.chats_dir), filename):\n"
            "            raise ValueError(chat_id)\n"
            "        return self.chats_dir / filename\n"
        )
        assert self._ungated_builders(safe) == []

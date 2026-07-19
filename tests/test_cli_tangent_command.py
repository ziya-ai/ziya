"""
Tests for the CLI's /tangent command and the context-sensitive /quit
overload — app.cli.CLI.cmd_tangent / cmd_quit / _pop_tangent.

Covers:
  - command-spec/dispatch wiring (regression guard, same shape as the
    /root tests: a spec entry must resolve to a real bound handler)
  - /tangent creates a stack frame, parks the active bead, creates a
    child "[tangent]" bead, and prints the topic
  - /tangent refuses to nest (single-level today, by design)
  - /tangent requires a topic argument
  - /quit with no active tangent is unchanged (returns False -> exit)
  - /quit discard (default, and explicit) drops tangent messages and
    restores the pre-tangent files list
  - /quit verbatim keeps the tangent transcript appended to history
  - /quit summary truncates the tangent transcript and splices in a
    single AI summary message (model.ainvoke mocked)
  - /quit rejects an unknown mode without popping the stack
  - files added during a tangent never survive any pop mode
  - bead lifecycle: tangent bead is completed/abandoned and the parent
    bead is reactivated on pop
  - /clear and /reset discard an in-flight tangent frame instead of
    leaving a dangling one behind
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli import CLI, COMMAND_SPEC, CLI_DISPATCH
from app.models.bead import Bead, BeadTree


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Prevent tests from mutating the real cwd/env or touching real bead storage."""
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.delenv("ZIYA_EXPLICIT_ROOT", raising=False)
    return tmp_path


@pytest.fixture
def cli():
    """A real CLI instance (constructor is side-effect-light — no network/model init)."""
    return CLI(files=["a.py", "b.py"])


def _bead_tree_stub():
    """A mutable in-memory (tree, save-calls) pair standing in for the bead store."""
    tree = BeadTree(beads=[Bead(id="bead_root", content="root task", status="active")])
    saved = {"tree": None}

    def _load(chat_storage=None, conversation_id=None):
        return tree

    def _save(t, chat_storage=None, conversation_id=None):
        saved["tree"] = t

    return tree, saved, _load, _save


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------

class TestCommandSpecWiring:

    def test_tangent_registered_in_command_spec(self):
        names = {e['name'] for e in COMMAND_SPEC}
        assert '/tangent' in names

    def test_dispatch_map_points_at_real_handlers(self):
        assert CLI_DISPATCH.get('/tangent') == 'cmd_tangent'
        assert CLI_DISPATCH.get('/quit') == 'cmd_quit'
        assert CLI_DISPATCH.get('/q') == 'cmd_quit'
        assert CLI_DISPATCH.get('/exit') == 'cmd_quit'

    def test_cmd_tangent_is_a_bound_method_on_cli(self, cli):
        handler = getattr(cli, CLI_DISPATCH['/tangent'], None)
        assert handler is not None
        assert callable(handler)


# ---------------------------------------------------------------------------
# /tangent entry
# ---------------------------------------------------------------------------

class TestTangentEntry:

    def test_requires_topic(self, cli, capsys):
        result = run(cli.cmd_tangent(""))
        assert result is True
        assert cli._tangent_stack == []
        assert "Usage" in capsys.readouterr().out

    def test_whitespace_only_treated_as_missing(self, cli, capsys):
        result = run(cli.cmd_tangent("   "))
        assert result is True
        assert cli._tangent_stack == []

    def test_creates_stack_frame_and_snapshots_files(self, cli, capsys):
        cli.history = [{'type': 'human', 'content': 'earlier question'}]
        _, _, load_stub, save_stub = _bead_tree_stub()
        with patch("app.storage.beads.load_bead_tree", side_effect=load_stub), \
             patch("app.storage.beads.save_bead_tree", side_effect=save_stub):
            result = run(cli.cmd_tangent("what does foo do"))

        assert result is True
        assert len(cli._tangent_stack) == 1
        frame = cli._tangent_stack[0]
        assert frame['topic'] == "what does foo do"
        assert frame['history_len'] == 1
        assert frame['files'] == ["a.py", "b.py"]
        out = capsys.readouterr().out
        assert "Tangent" in out
        assert "what does foo do" in out

    def test_refuses_to_nest(self, cli, capsys):
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            run(cli.cmd_tangent("first"))
            capsys.readouterr()
            result = run(cli.cmd_tangent("second"))

        assert result is True
        assert len(cli._tangent_stack) == 1
        assert cli._tangent_stack[0]['topic'] == "first"
        assert "nesting isn't supported" in capsys.readouterr().out

    def test_survives_bead_storage_failure(self, cli, capsys):
        """Bead bookkeeping is decoration — a storage error must not block
        the tangent itself from being entered."""
        with patch("app.storage.beads.load_bead_tree", side_effect=RuntimeError("boom")):
            result = run(cli.cmd_tangent("resilient topic"))

        assert result is True
        assert len(cli._tangent_stack) == 1
        assert cli._tangent_stack[0]['bead_id'] is None

    def test_parks_active_bead_and_creates_child(self, cli):
        tree, saved, load_stub, save_stub = _bead_tree_stub()
        with patch("app.storage.beads.load_bead_tree", side_effect=load_stub), \
             patch("app.storage.beads.save_bead_tree", side_effect=save_stub):
            run(cli.cmd_tangent("side idea"))

        result_tree = saved["tree"]
        assert result_tree is not None
        root = next(b for b in result_tree.beads if b.id == "bead_root")
        assert root.status == "parked"
        tangent_bead = next(b for b in result_tree.beads if b.id != "bead_root")
        assert tangent_bead.status == "active"
        assert tangent_bead.parent_id == "bead_root"
        assert "side idea" in tangent_bead.content


# ---------------------------------------------------------------------------
# /quit with no tangent active — must be unchanged
# ---------------------------------------------------------------------------

class TestQuitUnchangedWithoutTangent:

    def test_quit_returns_false_to_exit(self, cli):
        assert cli._tangent_stack == []
        result = run(cli.cmd_quit(""))
        assert result is False

    def test_quit_with_stray_arg_still_exits_when_no_tangent(self, cli):
        """Arguments are only meaningful once a tangent is active; with none
        active /quit must behave exactly as it always has."""
        result = run(cli.cmd_quit("summary"))
        assert result is False


# ---------------------------------------------------------------------------
# /quit popping an active tangent
# ---------------------------------------------------------------------------

class TestQuitPopsTangent:

    def _enter_tangent(self, cli, topic="side idea", extra_history=None):
        cli.history = [{'type': 'human', 'content': 'parent question'}]
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            run(cli.cmd_tangent(topic))
        cli.files = cli.files + ["scratch.py"]
        cli.history.extend(extra_history or [
            {'type': 'human', 'content': 'tangent question'},
            {'type': 'ai', 'content': 'tangent answer'},
        ])
        return cli

    def test_quit_returns_true_while_popping(self, cli):
        self._enter_tangent(cli)
        result = run(cli.cmd_quit(""))
        assert result is True
        assert cli._tangent_stack == []

    def test_default_mode_is_discard(self, cli, capsys):
        self._enter_tangent(cli)
        run(cli.cmd_quit(""))
        assert cli.history == [{'type': 'human', 'content': 'parent question'}]
        assert "discarded" in capsys.readouterr().out

    def test_explicit_discard(self, cli):
        self._enter_tangent(cli)
        run(cli.cmd_quit("discard"))
        assert cli.history == [{'type': 'human', 'content': 'parent question'}]

    def test_discard_is_case_insensitive_and_trims_whitespace(self, cli):
        self._enter_tangent(cli)
        run(cli.cmd_quit("  DISCARD  "))
        assert cli.history == [{'type': 'human', 'content': 'parent question'}]

    def test_verbatim_keeps_full_transcript(self, cli):
        self._enter_tangent(cli)
        run(cli.cmd_quit("verbatim"))
        assert cli.history == [
            {'type': 'human', 'content': 'parent question'},
            {'type': 'human', 'content': 'tangent question'},
            {'type': 'ai', 'content': 'tangent answer'},
        ]

    def test_files_are_always_restored_regardless_of_mode(self, cli):
        """Context additions during a tangent must vanish on quit no
        matter which mode is used — this is the explicit requirement,
        distinct from history handling."""
        for mode in ("discard", "verbatim", "summary"):
            local_cli = CLI(files=["a.py", "b.py"])
            self._enter_tangent(local_cli)
            local_cli._model = MagicMock(
                ainvoke=AsyncMock(return_value=MagicMock(content="summary text")))
            run(local_cli.cmd_quit(mode))
            assert local_cli.files == ["a.py", "b.py"], f"mode={mode} leaked tangent files"

    def test_summary_mode_replaces_transcript_with_one_message(self, cli):
        self._enter_tangent(cli)
        cli._model = MagicMock(ainvoke=AsyncMock(return_value=MagicMock(content="Concluded X.")))
        run(cli.cmd_quit("summary"))

        assert len(cli.history) == 2
        assert cli.history[0] == {'type': 'human', 'content': 'parent question'}
        assert cli.history[1]['type'] == 'ai'
        assert "Concluded X." in cli.history[1]['content']
        assert "side idea" in cli.history[1]['content']

    def test_summary_mode_falls_back_gracefully_on_model_error(self, cli):
        """A summarization failure must not crash /quit or leave the
        tangent stack in an inconsistent state."""
        self._enter_tangent(cli)
        cli._model = MagicMock(ainvoke=AsyncMock(side_effect=RuntimeError("model unavailable")))
        result = run(cli.cmd_quit("summary"))

        assert result is True
        assert cli._tangent_stack == []
        assert len(cli.history) == 2
        assert "unavailable" in cli.history[1]['content']

    def test_unknown_mode_rejected_without_popping(self, cli, capsys):
        self._enter_tangent(cli)
        result = run(cli.cmd_quit("bogus"))

        assert result is True
        assert len(cli._tangent_stack) == 1, "stack must remain untouched on rejection"
        assert "Unknown /quit option" in capsys.readouterr().out
        # History/files must be untouched too since nothing was popped.
        assert cli.files == ["a.py", "b.py", "scratch.py"]

    def test_bead_completed_on_non_discard_pop_and_parent_reactivated(self, cli):
        tree, saved, load_stub, save_stub = _bead_tree_stub()
        cli.history = [{'type': 'human', 'content': 'parent question'}]
        with patch("app.storage.beads.load_bead_tree", side_effect=load_stub), \
             patch("app.storage.beads.save_bead_tree", side_effect=save_stub):
            run(cli.cmd_tangent("side idea"))
            cli.history.append({'type': 'ai', 'content': 'answer'})
            run(cli.cmd_quit("verbatim"))

        final_tree = saved["tree"]
        root = next(b for b in final_tree.beads if b.id == "bead_root")
        tangent_bead = next(b for b in final_tree.beads if b.id != "bead_root")
        assert root.status == "active"
        assert tangent_bead.status == "completed"

    def test_bead_abandoned_on_discard_pop(self, cli):
        tree, saved, load_stub, save_stub = _bead_tree_stub()
        cli.history = [{'type': 'human', 'content': 'parent question'}]
        with patch("app.storage.beads.load_bead_tree", side_effect=load_stub), \
             patch("app.storage.beads.save_bead_tree", side_effect=save_stub):
            run(cli.cmd_tangent("side idea"))
            run(cli.cmd_quit("discard"))

        final_tree = saved["tree"]
        tangent_bead = next(b for b in final_tree.beads if b.id != "bead_root")
        assert tangent_bead.status == "abandoned"

    def test_pop_survives_bead_storage_failure(self, cli):
        """As with entry, a bead-store error on pop must not prevent the
        tangent from actually being popped."""
        self._enter_tangent(cli)
        with patch("app.storage.beads.load_bead_tree", side_effect=RuntimeError("boom")):
            result = run(cli.cmd_quit("discard"))

        assert result is True
        assert cli._tangent_stack == []

    def test_after_tangent_can_start_a_new_one(self, cli):
        """Regression guard for the nesting-guard's inverse: once popped,
        the stack must be empty enough to allow starting a fresh tangent."""
        self._enter_tangent(cli, topic="first")
        run(cli.cmd_quit("discard"))
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            result = run(cli.cmd_tangent("second"))
        assert result is True
        assert len(cli._tangent_stack) == 1
        assert cli._tangent_stack[0]['topic'] == "second"


# ---------------------------------------------------------------------------
# /clear and /reset must not leave a dangling tangent frame
# ---------------------------------------------------------------------------

class TestClearAndResetDiscardTangent:

    def test_clear_drops_tangent_stack(self, cli, capsys):
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            run(cli.cmd_tangent("side idea"))
        assert cli._tangent_stack != []

        run(cli.cmd_clear(""))
        assert cli._tangent_stack == []
        assert "discarding" in capsys.readouterr().out

    def test_reset_drops_tangent_stack(self, cli):
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            run(cli.cmd_tangent("side idea"))
        assert cli._tangent_stack != []

        run(cli.cmd_reset(""))
        assert cli._tangent_stack == []

    def test_clear_without_tangent_is_silent_on_that_front(self, cli, capsys):
        assert cli._tangent_stack == []
        run(cli.cmd_clear(""))
        assert "discarding" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Prompt badge
# ---------------------------------------------------------------------------

class TestTangentPromptSegment:

    def test_no_badge_without_active_tangent(self, cli):
        assert cli._tangent_prompt_segment() == []

    def test_badge_shows_topic_while_active(self, cli):
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            run(cli.cmd_tangent("debug the flaky test"))

        segment = cli._tangent_prompt_segment()
        assert segment != []
        rendered = "".join(text for _style, text in segment)
        assert "debug the flaky test" in rendered

    def test_long_topic_is_truncated_in_badge(self, cli):
        long_topic = "a" * 60
        with patch("app.storage.beads.load_bead_tree", side_effect=_bead_tree_stub()[2]), \
             patch("app.storage.beads.save_bead_tree", side_effect=_bead_tree_stub()[3]):
            run(cli.cmd_tangent(long_topic))

        segment = cli._tangent_prompt_segment()
        rendered = "".join(text for _style, text in segment)
        assert "..." in rendered
        assert long_topic not in rendered

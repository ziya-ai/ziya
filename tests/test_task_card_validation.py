"""Pre-launch structural validation of a task card tree.

Why this exists: ``Block`` sets ``extra="allow"`` and declares no
validators, so every one of these constructs cleanly and fails only at
runtime, sometimes hours in:

  - ``repeat_mode="for_each"`` with no source  -> ForEachSourceError when
    the loop is reached (previously: a silent count-fallback)
  - ``call_target`` naming a card that does not exist -> a failed
    artifact when the call is reached.  For an orchestrator card whose
    six Call blocks are the whole deck, a typo in the last one is
    discovered after the first five phases have run.
  - a Task with no instructions -> TaskExecutorError at dispatch
  - a typo'd field name (``repeat_maximum``) -> absorbed by
    ``extra="allow"`` and ignored forever, with the authored intent
    silently dropped

The validator is deliberately split into ERRORS (the run cannot do what
the card says) and WARNINGS (suspicious but runnable), because refusing
to launch on a warning would be a behaviour change, not a safety net.

Cycle detection and the depth cap are NOT re-implemented here: the
executor already enforces both (``ctx.call_stack`` /
``MAX_CALL_DEPTH``) and has tests for them.  This validator only
resolves targets, which is the half that can be known before the run.
"""

import pytest

from app.models.task_card import Block


# ── import surface ────────────────────────────────────────────

def test_module_exposes_validate_card_tree():
    from app.utils.task_card_validation import validate_card_tree
    assert callable(validate_card_tree)


def _validate(root, **kw):
    from app.utils.task_card_validation import validate_card_tree
    return validate_card_tree(root, **kw)


def _task(name="t", instructions="do it"):
    return Block(block_type="task", id="t1", name=name, instructions=instructions)


# ── a well-formed card produces nothing ───────────────────────

class TestCleanCard:
    def test_simple_task_has_no_findings(self):
        res = _validate(_task())
        assert res.errors == []
        assert res.warnings == []
        assert res.ok is True

    def test_group_of_tasks_is_clean(self):
        root = Block(block_type="group", id="g", name="g", body=[
            _task("a"), _task("b"),
        ])
        assert _validate(root).ok is True

    def test_for_each_with_literal_source_is_clean(self):
        root = Block(
            block_type="repeat", id="r", repeat_mode="for_each",
            repeat_for_each_source='["a", "b"]', body=[_task()],
        )
        assert _validate(root).ok is True

    def test_for_each_with_templated_source_is_clean(self):
        # Cannot be resolved statically (it depends on a runtime
        # artifact), so it must NOT be reported — a validator that cries
        # wolf on the canonical planner/fan-out shape is worse than none.
        root = Block(block_type="group", id="g", body=[
            _task("plan"),
            Block(
                block_type="repeat", id="r", repeat_mode="for_each",
                repeat_for_each_source='{{previous_sibling.summary}}',
                body=[_task()],
            ),
        ])
        assert _validate(root).ok is True


# ── errors: the run cannot do what the card says ──────────────

class TestStructuralErrors:
    def test_for_each_without_source_is_an_error(self):
        root = Block(
            block_type="repeat", id="r", repeat_mode="for_each",
            repeat_max=60, body=[_task()],
        )
        res = _validate(root)
        assert res.ok is False
        assert any("for_each" in e.message for e in res.errors)
        assert res.errors[0].block_id == "r"

    def test_task_without_instructions_is_an_error(self):
        root = Block(block_type="task", id="t1", name="empty")
        res = _validate(root)
        assert res.ok is False
        assert any("instructions" in e.message for e in res.errors)

    def test_whitespace_only_instructions_is_an_error(self):
        # task_executor tests `not instructions.strip()`, so whitespace
        # is as fatal as absence and must be reported the same way.
        root = Block(block_type="task", id="t1", name="ws", instructions="   \n ")
        assert _validate(root).ok is False

    def test_call_without_target_is_an_error(self):
        root = Block(block_type="call", id="c1", name="c")
        res = _validate(root)
        assert res.ok is False
        assert any("call_target" in e.message for e in res.errors)

    def test_unknown_block_type_is_an_error(self):
        # extra="allow" plus a Literal that pydantic DOES enforce means
        # this arrives via a hand-edited card file rather than the API.
        root = Block.model_construct(block_type="frobnicate", id="x", name="x")
        res = _validate(root)
        assert res.ok is False
        assert any("block_type" in e.message for e in res.errors)

    def test_errors_are_collected_not_short_circuited(self):
        # An author fixing one error at a time across a 20-block card is
        # the failure mode this avoids.
        root = Block(block_type="group", id="g", body=[
            Block(block_type="task", id="t1", name="no instr"),
            Block(block_type="call", id="c1", name="no target"),
            Block(block_type="repeat", id="r1", repeat_mode="for_each",
                  body=[_task()]),
        ])
        res = _validate(root)
        assert len(res.errors) == 3
        assert {e.block_id for e in res.errors} == {"t1", "c1", "r1"}


# ── warnings: suspicious but runnable ─────────────────────────

class TestWarnings:
    def test_unknown_field_is_a_warning_not_an_error(self):
        # repeat_maximum is absorbed by extra="allow": the authored
        # intent (a 60-iteration bound) is silently dropped, but the
        # card still runs, so this must not block a launch.
        root = Block(block_type="repeat", id="r", repeat_mode="count",
                     repeat_count=2, repeat_maximum=60, body=[_task()])
        res = _validate(root)
        assert res.ok is True, "an unknown field must not block the launch"
        assert any("repeat_maximum" in w.message for w in res.warnings)

    def test_empty_container_body_is_a_warning(self):
        root = Block(block_type="group", id="g", name="g", body=[])
        res = _validate(root)
        assert res.ok is True
        assert any("empty" in w.message.lower() for w in res.warnings)

    def test_parallel_without_concurrency_cap_is_not_warned_below_threshold(self):
        root = Block(block_type="repeat", id="r", repeat_mode="count",
                     repeat_count=4, repeat_parallel=True, body=[_task()])
        assert _validate(root).warnings == []

    def test_wide_parallel_count_warns_about_concurrency(self):
        # A 60-wide parallel count fan-out is bounded by the default cap
        # now, but the author probably wants to know it will be throttled.
        root = Block(block_type="repeat", id="r", repeat_mode="count",
                     repeat_count=60, repeat_parallel=True, body=[_task()])
        res = _validate(root)
        assert res.ok is True
        assert any("concurren" in w.message.lower() for w in res.warnings)


# ── call target resolution ────────────────────────────────────

class TestCallTargetResolution:
    def test_resolvable_target_produces_no_finding(self, monkeypatch):
        import app.utils.task_card_validation as v

        def _fake_resolve(target, kind, *, project_id, project_root):
            return object()

        monkeypatch.setattr(v, "resolve_call_target", _fake_resolve)
        root = Block(block_type="call", id="c1", call_target="Phase 1")
        res = _validate(root, project_id="p1", project_root="/tmp")
        assert res.ok is True

    def test_unresolvable_target_is_an_error(self, monkeypatch):
        import app.utils.task_card_validation as v
        from app.agents.task_call import CallResolutionError

        def _fake_resolve(target, kind, *, project_id, project_root):
            raise CallResolutionError(f"no task card matches {target!r}")

        monkeypatch.setattr(v, "resolve_call_target", _fake_resolve)
        root = Block(block_type="call", id="c1", call_target="Phase 7 typo")
        res = _validate(root, project_id="p1", project_root="/tmp")
        assert res.ok is False
        assert any("Phase 7 typo" in e.message for e in res.errors)

    def test_every_call_is_checked_not_just_the_first(self, monkeypatch):
        """The CL0-orchestrator case: six Calls, the last one typo'd.

        Resolving lazily at runtime means five phases run first.
        """
        import app.utils.task_card_validation as v
        from app.agents.task_call import CallResolutionError

        def _fake_resolve(target, kind, *, project_id, project_root):
            if target == "Phase 6":
                raise CallResolutionError(f"no task card matches {target!r}")
            return object()

        monkeypatch.setattr(v, "resolve_call_target", _fake_resolve)
        root = Block(block_type="group", id="g", body=[
            Block(block_type="call", id=f"c{i}", call_target=f"Phase {i}")
            for i in range(1, 7)
        ])
        res = _validate(root, project_id="p1", project_root="/tmp")
        assert res.ok is False
        assert len(res.errors) == 1
        assert res.errors[0].block_id == "c6"

    def test_resolution_skipped_without_project_id(self, monkeypatch):
        """No project context => cannot resolve => must not invent an error.

        "Cannot verify" is not "invalid", the same rule the credentials
        preflight follows.
        """
        import app.utils.task_card_validation as v

        called = {"n": 0}

        def _fake_resolve(*a, **kw):
            called["n"] += 1
            raise AssertionError("must not be called without a project id")

        monkeypatch.setattr(v, "resolve_call_target", _fake_resolve)
        root = Block(block_type="call", id="c1", call_target="Phase 1")
        res = _validate(root)
        assert called["n"] == 0
        assert res.ok is True

    def test_unexpected_resolver_error_does_not_crash_validation(self, monkeypatch):
        """A resolver bug must degrade to a warning, never break a launch."""
        import app.utils.task_card_validation as v

        def _fake_resolve(*a, **kw):
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr(v, "resolve_call_target", _fake_resolve)
        root = Block(block_type="call", id="c1", call_target="Phase 1")
        res = _validate(root, project_id="p1", project_root="/tmp")
        assert res.ok is True
        assert res.warnings, "an unverifiable target should warn, not error"


# ── recursion ─────────────────────────────────────────────────

class TestNestedTrees:
    def test_findings_are_reported_from_deep_bodies(self):
        deep = Block(block_type="task", id="deep", name="deep")  # no instructions
        root = Block(block_type="group", id="g1", body=[
            Block(block_type="repeat", id="r1", repeat_mode="count",
                  repeat_count=2, body=[
                      Block(block_type="parallel", id="p1", body=[deep]),
                  ]),
        ])
        res = _validate(root)
        assert res.ok is False
        assert res.errors[0].block_id == "deep"

    def test_block_path_is_reported_for_locating_the_block(self):
        deep = Block(block_type="task", id="deep", name="Audit step")
        root = Block(block_type="group", id="g1", name="Root", body=[
            Block(block_type="parallel", id="p1", name="Fan out", body=[deep]),
        ])
        res = _validate(root)
        # A bare block id is useless in a 40-block card; the path is what
        # makes the finding actionable.
        assert "Audit step" in res.errors[0].path
        assert "Fan out" in res.errors[0].path


# ── summary rendering ─────────────────────────────────────────

class TestSummary:
    def test_summary_lists_errors_for_the_held_run_record(self):
        root = Block(block_type="task", id="t1", name="empty")
        res = _validate(root)
        text = res.summary()
        assert "instructions" in text
        assert "empty" in text or "t1" in text

    def test_summary_is_empty_for_a_clean_card(self):
        assert _validate(_task()).summary() == ""

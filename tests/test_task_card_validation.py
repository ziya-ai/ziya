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


# ── {{sibling("...")}} references ─────────────────────────────
#
# The reference's BLOCK half is static and checkable even though the
# artifact half is not.  Measured failure: a for_each source naming the
# upstream block by its NAME; the renderer's by-id lookup missed,
# rendered "", and the run failed with zero iterations and a hint that
# blamed the upstream emit_artifact.

def _pipeline(for_each_ref, *, roster_id="b-roster", roster_name="Build roster"):
    return Block(block_type="group", id="g", name="Pipeline", body=[
        Block(block_type="task", id=roster_id, name=roster_name,
              instructions="emit the roster"),
        Block(block_type="repeat", id="b-fan", name="Fan out",
              repeat_mode="for_each",
              repeat_for_each_source=for_each_ref,
              body=[Block(block_type="task", id="b-one", name="One",
                          instructions="audit {{item}}")]),
    ])


class TestSiblingReferences:
    def test_reference_by_id_is_clean(self):
        res = _validate(_pipeline('{{sibling("b-roster").outputs.roster.docs}}'))
        assert res.errors == []

    def test_for_each_reference_by_name_is_an_error_naming_the_id(self):
        # The exact shape of the observed failure.
        res = _validate(_pipeline('{{sibling("Build roster").outputs.roster.docs}}'))
        assert len(res.errors) == 1
        f = res.errors[0]
        assert f.block_id == "b-fan"
        assert "block NAME" in f.message
        assert 'sibling("b-roster")' in f.message  # the fix, verbatim
        assert "repeat_for_each_source" in f.message

    def test_name_reference_to_unsaved_block_suggests_an_explicit_id(self):
        # An unsaved tree (no ids yet): the remedy cannot quote an id, so it
        # tells the author to set one, and proposes a slug.
        res = _validate(_pipeline('{{sibling("Build roster").outputs.roster.docs}}',
                                  roster_id=""))
        assert len(res.errors) == 1
        assert 'explicit "id"' in res.errors[0].message
        assert '"build-roster"' in res.errors[0].message

    def test_ambiguous_name_is_an_error(self):
        root = _pipeline('{{sibling("Build roster").outputs.roster.docs}}')
        root.body.insert(0, Block(block_type="task", id="b-dup",
                                  name="Build roster", instructions="x"))
        res = _validate(root)
        assert any("shared by 2 blocks" in e.message for e in res.errors)

    def test_for_each_unknown_id_is_an_error_listing_known_ids(self):
        res = _validate(_pipeline('{{sibling("b-nope").outputs.roster.docs}}'))
        assert len(res.errors) == 1
        assert "names no block" in res.errors[0].message
        assert "b-roster" in res.errors[0].message

    def test_instructions_unknown_id_is_only_a_warning(self):
        # A callee card may reference its caller's blocks (shared registry),
        # so an absent id in instructions is not provably wrong.
        root = _pipeline('{{sibling("b-roster").outputs.roster.docs}}')
        root.body[1].body[0].instructions = 'see {{sibling("b-elsewhere")}}'
        res = _validate(root)
        assert res.errors == []
        assert any("b-elsewhere" in w.message and "Called" in w.message
                   for w in res.warnings)

    def test_instructions_name_reference_is_still_an_error(self):
        root = _pipeline('{{sibling("b-roster").outputs.roster.docs}}')
        root.body[1].body[0].instructions = 'see {{sibling("Build roster")}}'
        res = _validate(root)
        assert any("block NAME" in e.message and e.block_id == "b-one"
                   for e in res.errors)

    def test_self_and_ancestor_references_are_errors(self):
        root = _pipeline('{{sibling("b-fan").outputs.x.y}}')  # self
        res = _validate(root)
        assert any("itself" in e.message for e in res.errors)
        root = _pipeline('{{sibling("g").outputs.x.y}}')  # enclosing group
        res = _validate(root)
        assert any("enclosing" in e.message for e in res.errors)

    def test_descendant_reference_is_an_error(self):
        res = _validate(_pipeline('{{sibling("b-one").outputs.x.y}}'))
        assert any("inside this one's body" in e.message for e in res.errors)

    def test_repeated_reference_yields_one_finding(self):
        root = _pipeline('{{sibling("b-roster").outputs.roster.docs}}')
        root.body[1].body[0].instructions = (
            '{{sibling("Build roster")}} and again {{sibling("Build roster")}}'
        )
        res = _validate(root)
        assert len([e for e in res.errors if e.block_id == "b-one"]) == 1

    def test_reference_to_earlier_sibling_of_an_ancestor_is_clean(self):
        # Registry is run-scoped: a nested block may reference a block
        # that completed earlier at a shallower depth.
        root = _pipeline('{{sibling("b-roster").outputs.roster.docs}}')
        root.body[1].body[0].instructions = '{{sibling("b-roster").summary}}'
        assert _validate(root).errors == []


# ── required task under on_failure=stop without a contract ─────

class TestRequiredUnderStopWithoutContract:
    """A required Task under ``on_failure="stop"`` whose only verdict is
    the model's self-assessment halts every later sibling on a single
    'partial'.  Warn before launch; a contract or ``required: false``
    silences it."""

    STOP_MSG = "on_failure=stop has no output_contract"

    @staticmethod
    def _seq(child, **kw):
        return Block(block_type="group", id="g", name="g",
                     on_failure="stop", body=[child, Block(
                         block_type="task", id="t2", name="later",
                         instructions="runs after",
                         output_contract=[{"name": "f", "schema": "ziya:finding"}])], **kw)

    def test_warns_on_bare_required_task(self):
        res = self._warnings(self._seq(_task()))
        assert len(res) == 1 and res[0].block_id == "t1"
        assert "required: false" in res[0].message

    def test_contract_silences_it(self):
        t = Block(block_type="task", id="t1", name="t", instructions="x",
                  output_contract=[{"name": "f", "schema": "ziya:finding"}])
        assert self._warnings(self._seq(t)) == []

    def test_state_default_contract_silences_it(self):
        root = Block(block_type="group", id="g", name="g", on_failure="stop", body=[
            Block(block_type="state", id="s", name="s",
                  output_contract_defaults=[{"name": "f", "schema": "ziya:finding"}]),
            _task(),
        ])
        assert self._warnings(root) == []

    def test_required_false_silences_it(self):
        t = Block(block_type="task", id="t1", name="t", instructions="x", required=False)
        assert self._warnings(self._seq(t)) == []

    def test_continue_or_unset_policy_does_not_warn(self):
        root = Block(block_type="group", id="g", name="g", body=[_task()])
        assert self._warnings(root) == []
        root.on_failure = "continue"
        assert self._warnings(root) == []

    def test_parallel_children_are_not_under_stop(self):
        # _execute_parallel never reads on_failure, so its children are
        # not halted by a sibling's failure even when the field is set.
        root = Block(block_type="parallel", id="p", name="p", on_failure="stop",
                     body=[_task()])
        assert self._warnings(root) == []

    def test_repeat_and_until_bodies_are_sequences(self):
        for kind, extra in (("repeat", {"repeat_mode": "count", "repeat_count": 2}),
                            ("until", {"until_max": 2})):
            root = Block(block_type=kind, id="c", name="c", on_failure="stop",
                         body=[_task()], **extra)
            assert len(self._warnings(root)) == 1, kind

    def _warnings(self, root):
        return [w for w in _validate(root).warnings if self.STOP_MSG in w.message]


# ── scope.cwd on any layer, confined to the project root ───────

class TestCwdConfinedToProject:
    """``scope.cwd`` may be set on ANY block (it flows down like
    model_tier), as long as it stays inside the project root.  The
    executor only discovers a bad cwd at run time and silently runs at
    the root; the validator says so before launch."""

    @staticmethod
    def _with_cwd(kind, cwd, **extra):
        from app.models.task_card import TaskScope
        scope = TaskScope(cwd=cwd)
        if kind == "task":
            return Block(block_type="task", id="t1", name="t", instructions="x", scope=scope)
        return Block(block_type=kind, id="c", name="c", scope=scope, body=[_task()], **extra)

    def _cwd_findings(self, root, **kw):
        res = _validate(root, **kw)
        return ([e for e in res.errors if "scope.cwd" in e.message],
                [w for w in res.warnings if "scope.cwd" in w.message])

    def test_relative_cwd_inside_root_is_clean(self, tmp_path):
        (tmp_path / "sub").mkdir()
        errs, warns = self._cwd_findings(self._with_cwd("task", "sub"), project_root=str(tmp_path))
        assert errs == [] and warns == []

    def test_lexical_escape_is_an_error_without_a_project_root(self):
        errs, _ = self._cwd_findings(self._with_cwd("task", "../elsewhere"))
        assert len(errs) == 1 and "escapes" in errs[0].message
        # "a/../b" stays inside; "a/../.." does not.
        assert self._cwd_findings(self._with_cwd("task", "a/../b"))[0] == []
        assert len(self._cwd_findings(self._with_cwd("task", "a/../.."))[0]) == 1

    def test_absolute_path_outside_root_is_an_error(self, tmp_path):
        other = tmp_path / "other"; other.mkdir()
        root = tmp_path / "proj"; root.mkdir()
        errs, _ = self._cwd_findings(self._with_cwd("task", str(other)), project_root=str(root))
        assert len(errs) == 1 and "escapes" in errs[0].message

    def test_absolute_path_inside_root_is_clean(self, tmp_path):
        (tmp_path / "sub").mkdir()
        errs, warns = self._cwd_findings(self._with_cwd("task", str(tmp_path / "sub")),
                                         project_root=str(tmp_path))
        assert errs == [] and warns == []

    def test_missing_directory_is_a_warning_only_with_a_root(self, tmp_path):
        errs, warns = self._cwd_findings(self._with_cwd("task", "not-yet"), project_root=str(tmp_path))
        assert errs == [] and len(warns) == 1 and "not a directory" in warns[0].message
        assert self._cwd_findings(self._with_cwd("task", "not-yet")) == ([], [])

    @pytest.mark.parametrize("kind,extra", [
        ("group", {}), ("parallel", {}),
        ("repeat", {"repeat_mode": "count", "repeat_count": 1}),
        ("until", {"until_max": 1}),
    ])
    def test_container_cwd_is_checked_too(self, kind, extra):
        errs, _ = self._cwd_findings(self._with_cwd(kind, "../out", **extra))
        assert len(errs) == 1 and errs[0].block_id == "c"

    def test_container_cwd_reaches_the_leaf_through_merge_scopes(self):
        # The seam the grammar relies on: a cwd set on a container is what
        # the leaf runs in, unless the leaf sets its own.
        from app.models.task_card import TaskScope, merge_scopes
        assert merge_scopes(TaskScope(cwd="svc"), None).cwd == "svc"
        assert merge_scopes(TaskScope(cwd="svc"), TaskScope(cwd="svc/api")).cwd == "svc/api"
        assert merge_scopes(TaskScope(cwd="svc"), TaskScope(tools=["x"])).cwd == "svc"

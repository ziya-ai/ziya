"""
Infra faults hold a run rather than failing it (E1).

Across ten runs of one long campaign card, every single termination was
an infrastructure fault or a manual cancel — expired credentials, a lost
Bedrock endpoint, an exhausted throttling retry — and all of them were
recorded as ``failed``, which both misdescribed them and discarded the
loop position, forcing the next attempt to re-pay the earlier stages.
These tests pin the distinction.
"""

import time

import pytest

from app.agents.task_executor import TaskExecutorError, TaskInfraError
from app.models.task_run import TaskRun
from app.utils.run_outcome import classify_terminal_status


class TestTaskInfraError:
    def test_is_a_task_executor_error(self):
        # Existing handlers must keep catching it, or introducing the
        # subclass would turn handled infra faults into crashes.
        exc = TaskInfraError("boom", infra_kind="authentication_error")
        assert isinstance(exc, TaskExecutorError)

    def test_carries_kind_and_block(self):
        exc = TaskInfraError(
            "boom", infra_kind="connection_error", block_id="b-1234",
        )
        assert exc.infra_kind == "connection_error"
        assert exc.block_id == "b-1234"

    def test_detectable_by_attribute_without_importing(self):
        # task_cards.py deliberately branches on getattr rather than
        # isinstance so it needs no new import; that contract is what
        # this asserts.
        infra = TaskInfraError("boom", infra_kind="throttling_error")
        plain = TaskExecutorError("boom")
        assert getattr(infra, "infra_kind", "") == "throttling_error"
        assert getattr(plain, "infra_kind", "") == ""

    def test_defaults_are_empty_not_none(self):
        # task_cards.py truthiness-tests infra_kind; None would work but
        # "" keeps the type stable for the log formatting beside it.
        exc = TaskInfraError("boom")
        assert exc.infra_kind == ""
        assert exc.block_id == ""


class TestHeldStatusModel:
    def test_held_is_an_accepted_status(self):
        run = TaskRun(card_id="c1", status="held")
        assert run.status == "held"

    def test_held_fields_default_to_none(self):
        run = TaskRun(card_id="c1")
        assert run.held_reason is None
        assert run.held_at_block_id is None

    def test_held_fields_round_trip(self):
        run = TaskRun(
            card_id="c1", status="held",
            held_reason="authentication_error",
            held_at_block_id="b-bf007c11",
        )
        restored = TaskRun(**run.model_dump())
        assert restored.held_reason == "authentication_error"
        assert restored.held_at_block_id == "b-bf007c11"


class TestHeldIsNotReclassified:
    """``held`` must survive the partial-reclassification pass.

    "partial" answers *how much got done*; "held" answers *why it
    stopped*.  Collapsing held into partial would lose the only
    actionable half — that the infrastructure needs attention rather
    than the card.
    """

    def _states(self, *pairs):
        return {
            bid: {"block_id": bid, "block_type": "task", "status": st}
            for bid, st in pairs
        }

    def test_held_with_progress_stays_held(self):
        states = self._states(("a", "done"), ("b", "failed"), ("c", "queued"))
        assert classify_terminal_status("held", states) == "held"

    def test_held_with_no_progress_stays_held(self):
        states = self._states(("a", "failed"))
        assert classify_terminal_status("held", states) == "held"

    def test_failed_still_reclassifies(self):
        # Guards against a change here quietly disabling E-partial.
        states = self._states(("a", "done"), ("b", "failed"))
        assert classify_terminal_status("failed", states) == "partial"


class TestMarkHeld:
    @pytest.fixture()
    def storage(self, tmp_path):
        from app.storage.task_runs import TaskRunStorage
        # Takes a Path, not a str: __init__ does ``project_dir / "task_runs"``.
        return TaskRunStorage(tmp_path)

    def _new_run(self, storage):
        from app.models.task_run import TaskRunCreate
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        return run

    def test_sets_status_reason_and_block(self, storage):
        run = self._new_run(storage)
        held = storage.mark_held(
            run.id, reason="connection_error", block_id="b-99",
            error="Could not connect to the endpoint URL",
        )
        assert held.status == "held"
        assert held.held_reason == "connection_error"
        assert held.held_at_block_id == "b-99"
        assert "endpoint" in held.error

    def test_persists_across_reload(self, storage):
        run = self._new_run(storage)
        storage.mark_held(run.id, reason="authentication_error", block_id="b-1")
        reloaded = storage.get(run.id)
        assert reloaded.status == "held"
        assert reloaded.held_reason == "authentication_error"
        assert reloaded.held_at_block_id == "b-1"

    def test_stamps_completed_at(self, storage):
        # Terminal for this run object, so the tile can show a runtime;
        # continuation is a separate run.
        run = self._new_run(storage)
        before = time.time()
        held = storage.mark_held(run.id, reason="throttling_error")
        assert held.completed_at is not None
        assert held.completed_at >= before

    def test_update_status_held_also_stamps_completed_at(self, storage):
        run = self._new_run(storage)
        updated = storage.update_status(run.id, "held")
        assert updated.completed_at is not None

    def test_empty_reason_normalises_to_none(self, storage):
        run = self._new_run(storage)
        held = storage.mark_held(run.id, reason="", block_id="")
        assert held.held_reason is None
        assert held.held_at_block_id is None

    def test_unknown_run_returns_none(self, storage):
        assert storage.mark_held("nope", reason="x") is None


class TestInfraKindReadFromChunk:
    """The fault kind lives in ``error_type``, not in ``type``.

    ``_classify_and_handle_error`` and the StreamError branch of
    ``stream_with_tools`` both emit an expired-credentials failure as
    ``{'type': 'error', 'error_type': 'authentication_error', ...}``.
    The executor matched only on ``type``, so nothing in the codebase
    ever produced a chunk that satisfied the infra branch — the whole
    hold path was unreachable, and every credential expiry was recorded
    as a failure of the card (then reclassified ``partial``, which
    reports the WORK as half-done rather than the environment as
    broken, and discards the resume position a hold preserves).
    """

    def test_infra_kinds_are_module_level(self):
        # Shared between the two branches that classify a chunk, so the
        # two cannot drift into disagreeing about what counts as infra.
        from app.agents.task_executor import INFRA_ERROR_KINDS
        assert "authentication_error" in INFRA_ERROR_KINDS
        assert "throttling_error" in INFRA_ERROR_KINDS
        assert "connection_error" in INFRA_ERROR_KINDS
        assert "transient_service_error" in INFRA_ERROR_KINDS

    def test_a_work_failure_is_not_an_infra_kind(self):
        # Guards against the set widening until everything holds and no
        # genuine card failure is ever reported again.
        from app.agents.task_executor import INFRA_ERROR_KINDS
        assert "error" not in INFRA_ERROR_KINDS
        assert "validation_error" not in INFRA_ERROR_KINDS

    def _kind_of(self, chunk):
        """The classification the executor's error branch performs."""
        from app.agents.task_executor import INFRA_ERROR_KINDS
        kind = str(chunk.get("error_type") or chunk.get("error") or "")
        return kind if kind in INFRA_ERROR_KINDS else ""

    def test_auth_chunk_as_actually_emitted_classifies_as_infra(self):
        # Verbatim shape from streaming_tool_executor's auth paths.
        chunk = {
            "type": "error",
            "error": "authentication_error",
            "error_type": "authentication_error",
            "content": "AWS credentials have expired.",
            "can_retry": True,
        }
        assert self._kind_of(chunk) == "authentication_error"

    def test_type_alone_would_have_missed_it(self):
        # States the bug directly: the old predicate.
        from app.agents.task_executor import INFRA_ERROR_KINDS
        chunk = {"type": "error", "error_type": "authentication_error"}
        assert chunk["type"] not in INFRA_ERROR_KINDS
        assert self._kind_of(chunk) == "authentication_error"

    def test_error_field_alone_is_enough(self):
        # error_type is absent on some producers; error carries the kind.
        assert self._kind_of(
            {"type": "error", "error": "throttling_error"},
        ) == "throttling_error"

    def test_unclassified_error_stays_a_work_failure(self):
        # A model/tool failure must still fail the card, or a real defect
        # would be reported as an infrastructure problem and retried
        # forever against an environment that is fine.
        assert self._kind_of(
            {"type": "error", "content": "the task could not be completed"},
        ) == ""

    def test_held_status_survives_reclassification(self):
        # mark_held deliberately skips the partial pass; this pins that a
        # held run with passing iterations is NOT downgraded to partial,
        # which is what erased the distinction in the observed run.
        states = {
            "root": {
                "block_id": "root", "block_type": "until", "status": "failed",
                "iteration_summaries": [
                    {"index": i, "status": "passed"} for i in range(5)
                ],
            },
        }
        assert classify_terminal_status("failed", states) == "partial"
        assert classify_terminal_status("held", states) == "held"
"""The SEAM the banking suite missed: does the LAUNCH PATH ask for it?

``tests/test_resume_inherited_iteration_banking.py`` went fully green
while this feature was broken end to end, and the reason is worth
recording because it is the classic two-correct-halves defect:

  * ``TaskRunStorage.seed_replayed_iterations`` grew a
    ``create_if_missing`` parameter and honours it correctly.
  * ``_launch_run_for_card`` — the ONLY caller in the entire application —
    never passes it.

Every test in that suite that exercises the created-state behaviour calls
storage directly *with the flag*, so the suite proved the parameter works
without ever proving anyone uses it.  A grep for ``create_if_missing=True``
across ``app/`` returned nothing; the only hits were in the tests
themselves.  Coverage was 100% on the function and 0% on the wiring.

So this file asserts the OUTERMOST observable surface instead: after a
resume launch whose resume point is a block inside a CALLED card, does the
new run's persisted record actually hold the replayed prefix?  That is the
question the next resume's ``parallel_replay_indices`` will ask of it, and
the answer is what decides whether 58 banked iterations are re-run.

The fixture shape is the one that fails: a wide parallel fan-out inside a
Call, so the loop id is absent from the caller's ``card_snapshot`` and
therefore absent from ``block_states`` at launch time.
"""

import json
import time
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import IterationSummary
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage

WIDTH = 60
BANKED = [i for i in range(WIDTH) if i not in (36, 59)]
LOOP_ID = "b-2cf2a30f"
CALL_ID = "b-1c898bc4"


def _callee_root() -> Block:
    """CL4: queue prep, the 60-wide gap fan-out, then the merge."""
    return Block(block_type="group", id="b-1cf756f9", name="Reintegration",
                 on_failure="continue", body=[
        Block(block_type="task", id="b-1f65b497", name="Stage 1",
              instructions="load the gap queue"),
        Block(
            block_type="repeat", id=LOOP_ID, name="Stage 2",
            repeat_mode="count", repeat_count=WIDTH, repeat_max=WIDTH,
            repeat_parallel=True, repeat_propagate="none",
            body=[Block(block_type="task", id="b-42fc8f9b",
                        name="Stage A", instructions="second look")],
        ),
        Block(block_type="task", id="b-6cc68d19", name="Stage 3",
              instructions="apply corrections"),
    ])


def _caller_root() -> Block:
    """CL0: a State block then six Calls, bodies empty by construction."""
    return Block(block_type="group", id="b-f1a1e4d3", name="CL0", body=[
        Block(block_type="state", id="b-7ed737b0", name="Study parameters",
              state_context="Code is truth."),
        Block(block_type="call", id=CALL_ID, name="Phase 4",
              call_target="CL4", call_target_kind="card"),
    ])


@pytest.fixture
def env(tmp_path):
    """A project plus the SOURCE run that a resume is launched from."""
    home = tmp_path / ".ziya"
    pid = "proj-launch-seam"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Launch Seam", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    cards = TaskCardStorage(pdir)
    # The callee must exist as a saved card: launch-time structural
    # validation resolves every Call target by NAME and refuses the launch
    # outright when one dangles.  Saving it does not inline it — the
    # caller's tree still carries an empty Call body, which is the
    # asymmetry under test.
    cards.create(TaskCardCreate(name="CL4", root=_callee_root()))
    card = cards.create(TaskCardCreate(name="CL0", root=_caller_root()))
    return home, pid, pdir, card.id


async def _launch_resume(pdir, pid: str, card_id: str) -> Any:
    """Drive the REAL launch path with resume args, executing nothing.

    ``asyncio.create_task`` is stubbed because the launch's last act is to
    schedule the run; this test is about what the launch PERSISTS, and
    letting real agents start would make it neither fast nor hermetic.
    Line 837 is the module's only ``create_task`` call, so the stub cannot
    swallow anything else.
    """
    from app.api import task_cards as tc

    # pdir is <home>/projects/<pid>, so the ziya home is two levels up.
    home = pdir.parent.parent

    banked_artifacts = {
        i: Artifact(summary=f"gap {i} audited", created_at=time.time())
        for i in BANKED
    }
    banked_summaries = [
        IterationSummary(index=i, status="passed", has_artifact=True,
                         replayed=True)
        for i in BANKED
    ]

    def _no_exec(coro):
        # Close it so Python does not warn about a coroutine never awaited.
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    # ``get_project_dir`` must be patched in the task_cards NAMESPACE, not
    # on app.utils.paths: task_cards does ``from ..utils.paths import
    # get_project_dir``, which binds the name at import time, so patching
    # the source module leaves this caller pointing at the real one — and
    # the launch then writes its run record under the developer's actual
    # ~/.ziya instead of tmp_path.
    # Structural validation resolves Call targets through
    # ``app.agents.task_call``, which reaches storage by its own route and
    # so does not see this tmp_path project.  Skipped deliberately: this
    # test is about what a resume launch PERSISTS, and the fixture's shape
    # is asserted directly by TestTheFixtureReproducesTheFailingShape
    # rather than being taken on trust from the validator.
    with patch.dict("os.environ", {"ZIYA_SKIP_CARD_VALIDATION": "1"}), \
         patch("app.api.task_cards.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.get_ziya_home", return_value=home), \
         patch("app.api.task_cards.asyncio.create_task", new=_no_exec):
        return await tc._launch_run_for_card(
            project_id=pid,
            card_id=card_id,
            resume_root=_caller_root(),
            resume_from_block_id=LOOP_ID,
            resumed_from_block_id=LOOP_ID,
            resume_call_chain=[CALL_ID],
            resume_iteration_artifacts=banked_artifacts,
            resume_iteration_summaries=banked_summaries,
            attempt=5,
            resume_kind="retry_from",
        )


class TestTheFixtureReproducesTheFailingShape:
    """Without these the suite below could pass for the wrong reason."""

    def test_the_loop_is_absent_from_the_callers_tree(self):
        """A Call names its callee; it does not inline it.

        This asymmetry is the entire cause: launch-time seeding walks only
        the caller's tree, so the loop has no state for the prefix to be
        written into.
        """
        from app.utils.resume_targets import find_block
        assert find_block(_caller_root().model_dump(), LOOP_ID) is None, (
            "fixture is wrong: the callee loop must NOT appear in the "
            "caller's own tree, or this file proves nothing"
        )

    def test_the_loop_is_present_in_the_callee_tree(self):
        """Positive counterpart: the block genuinely exists somewhere."""
        from app.utils.resume_targets import find_block
        assert find_block(_callee_root().model_dump(), LOOP_ID) is not None


class TestTheLaunchPersistsTheReplayedPrefix:
    """The outermost surface: what is on the new run's record."""

    @pytest.mark.asyncio
    async def test_the_prefix_reaches_the_run_record(self, env):
        home, pid, pdir, card_id = env
        run = await _launch_resume(pdir, pid, card_id)

        state = (TaskRunStorage(pdir).get(run.id).block_states or {}).get(
            LOOP_ID)
        assert state is not None, (
            "the resumed run holds NO state for the callee loop, so its "
            "58 banked iterations are invisible to the next resume's "
            "parallel_replay_indices — which selects from "
            "iteration_summaries and will therefore re-run them.  The "
            "launch path is not passing create_if_missing=True."
        )
        assert len(state.iteration_summaries) == len(BANKED), (
            f"expected {len(BANKED)} replayed records on the run, got "
            f"{len(state.iteration_summaries)}"
        )

    @pytest.mark.asyncio
    async def test_the_persisted_records_are_marked_replayed(self, env):
        """Load-bearing in the opposite direction.

        A resumed run must not credit itself with a prior attempt's work:
        ``replayed=True`` is what keeps these out of every progress
        aggregate, so a resume that dies on its first real iteration is
        not reclassified ``partial``.
        """
        home, pid, pdir, card_id = env
        run = await _launch_resume(pdir, pid, card_id)

        state = (TaskRunStorage(pdir).get(run.id).block_states or {}).get(
            LOOP_ID)
        assert state is not None, "no state persisted; see the test above"
        assert all(s.replayed for s in state.iteration_summaries)

    @pytest.mark.asyncio
    async def test_the_next_resume_would_bank_them(self, env):
        """The consequence, asserted through the real selector.

        This is the assertion that actually matters: it is not enough for
        the records to exist, they must be the shape
        ``parallel_replay_indices`` accepts — a pass holding a retained
        artifact.  Anything else and the prefix is decoration.
        """
        from app.utils.resume_targets import parallel_replay_indices

        home, pid, pdir, card_id = env
        run = await _launch_resume(pdir, pid, card_id)
        storage = TaskRunStorage(pdir)
        rec = storage.get(run.id)

        state = (rec.block_states or {}).get(LOOP_ID)
        assert state is not None, "no state persisted; see the first test"

        idxs = parallel_replay_indices(
            _callee_root().model_dump(), LOOP_ID,
            [s.model_dump() for s in state.iteration_summaries],
            None,
        )
        assert sorted(idxs or []) == sorted(BANKED), (
            f"the next resume would bank {len(idxs or [])} of {WIDTH} "
            f"iterations instead of {len(BANKED)}; the prefix exists but "
            f"is not in a bankable shape"
        )

    @pytest.mark.asyncio
    async def test_the_artifacts_are_copied_alongside(self, env):
        """The half that already worked, pinned so a fix cannot break it.

        Artifact copying needs no block state, which is exactly why the
        two halves diverged: artifacts landed while summaries did not, and
        the run looked populated on disk while being unbankable.
        """
        home, pid, pdir, card_id = env
        run = await _launch_resume(pdir, pid, card_id)
        storage = TaskRunStorage(pdir)

        present = [
            i for i in BANKED
            if storage.read_iteration_artifact(run.id, LOOP_ID, i) is not None
        ]
        assert len(present) == len(BANKED), (
            f"only {len(present)} of {len(BANKED)} banked artifacts were "
            f"copied onto the resumed run"
        )


class TestTheInvariantIsStillHeld:
    """The correction must not reopen what the gate exists to prevent."""

    def test_storage_still_refuses_an_arbitrary_id_by_default(self, tmp_path):
        """Paired with the tests above so 'it works' cannot mean 'it is
        unconditional'.  A stale or misspelled id must not become a
        phantom row in the run map.
        """
        from app.models.task_run import TaskRunCreate
        storage = TaskRunStorage(tmp_path)
        run = storage.create(TaskRunCreate(card_id="c"))
        storage.seed_replayed_iterations(
            run.id, "b-typo",
            [IterationSummary(index=0, status="passed", has_artifact=True,
                              replayed=True)],
        )
        assert "b-typo" not in (storage.get(run.id).block_states or {}), (
            "storage minted state for an arbitrary block id without being "
            "asked; the default must stay conservative"
        )

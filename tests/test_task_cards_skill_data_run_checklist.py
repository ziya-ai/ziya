"""
The task_cards skill must carry the data-run pre-launch checklist and must
document the Parallel concurrency cap.

Motivating run (chat 3f831376, 2026-10-23): an 8-hour data card re-ran one
expensive CTE ~60 times in 30-minute slices, wrote a 500 MB intermediate no
lane read, had six lanes re-pull row-level data because stage 1 had
persisted aggregates, sized every result with an instrument the project had
already documented as biased, and ran ten slice jobs concurrently against
one 64-second-capped proxy.  Every one of those was decidable before Run,
and the skill prompt is the only place the authoring model is told to
decide them.

The Parallel cap is a seam test: ``repeat_max_concurrency`` exists on the
model and ``_execute_parallel`` reads it, but the prose said nothing about
it under Parallel, so the field was unreachable from an authored card.
"""
import inspect
import re

import pytest


def _skill():
    from app.data.built_in_skills import BUILT_IN_SKILLS
    return next(s for s in BUILT_IN_SKILLS if s["id"] == "task_cards")


def _section(prompt: str, heading: str) -> str:
    """Text of ``heading`` up to the next heading of the same level."""
    level = heading.split(" ", 1)[0]
    start = prompt.index(heading)
    m = re.search(rf"^{re.escape(level)} ", prompt[start + len(heading):], re.M)
    end = start + len(heading) + m.start() if m else len(prompt)
    return prompt[start:end]


class TestParallelCapIsReachable:
    def test_executor_reads_the_field_on_parallel(self):
        from app.agents import block_executor as be
        src = inspect.getsource(be._execute_parallel)
        assert "repeat_max_concurrency" in src

    def test_parallel_prose_names_the_field(self):
        prompt = _skill()["prompt"]
        start = prompt.index("**Parallel**")
        end = prompt.index("**Group**", start)
        assert "repeat_max_concurrency" in prompt[start:end], (
            "the Parallel section must tell the author the cap applies there"
        )


class TestDataRunChecklist:
    HEADING = "## Data runs: before you press Run"

    def test_section_exists_before_output_format(self):
        prompt = _skill()["prompt"]
        assert self.HEADING in prompt
        assert prompt.index(self.HEADING) < prompt.index("## Output format")

    @pytest.mark.parametrize("anchor", [
        "memory_search",          # check the shelf
        "dataset_register",       # pull once, materialize
        "analyze",                # lanes join locally
        "FINEST grain",           # the grain rule that made "reuse" obeyable
        "instrument",             # choose by the question
        "Smoke before fan-out",
        "falsifier",              # define sufficient
        "repeat_max_concurrency", # cap the shared backend
        "No intermediate without a consumer",
        "Parquet",
    ])
    def test_checklist_carries_each_lesson(self, anchor):
        sec = _section(_skill()["prompt"], self.HEADING)
        assert anchor in sec, f"checklist is missing the {anchor!r} lesson"

    def test_checklist_names_the_enforced_budget_field(self):
        # The executor enforces scope.time_budget_secs (test_task_time_budget);
        # the prose must offer it by name, or the field is unreachable from
        # an authored card -- the same gap repeat_max_concurrency had.
        from app.agents import task_executor as te
        assert hasattr(te, "TIME_BUDGET_CLOSING_MESSAGE")
        sec = _section(_skill()["prompt"], self.HEADING)
        assert "time_budget_secs" in sec
        assert "no executor-enforced" not in sec, "stale claim: the budget is enforced now"

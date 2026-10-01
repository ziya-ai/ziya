"""Structural guards for the orchestrated release-sweep task card.

``design/sweep-orchestrated.task-card.json`` is the checked-in definition of
the decomposed replacement for the linear ``sweep`` file task.  It is data,
not code, which is exactly why it needs pinning: every property that makes it
correct is a shape a well-meaning edit can flatten without anything erroring.

Four of those properties are load-bearing enough that losing one produces a
BROKEN RELEASE rather than a failed run:

1. **The commit loop is serial.**  The git index is a single shared resource.
   A parallel commit loop interleaves ``git add`` calls between iterations and
   produces commits containing each other's files — corrupted history that no
   test downstream of it would notice, since every commit "succeeded".

2. **The commit loop asserts roster completeness.**  This is the mechanical
   form of the failure the card exists to prevent (v0.8.6.1 collapsed 63
   changelog entries into 20 commits and reported success).  Coverage has to
   be an assertion, not an instruction.

3. **Every fan-out roster reaches its loop.**  A ``for_each`` source is a
   template; if the producer it names is no longer the loop's immediately
   preceding sibling, the template renders empty and the loop dispatches ZERO
   iterations.  Reordering phases is the natural edit that breaks this.

4. **Escalation stays confined to the mutating blocks.**  The analysis half
   asks for nothing that can change the repository, and no grant anywhere
   admits a history-rewriting or working-tree-destroying git subcommand.

Each assertion below is paired with a mutation applied to a deep copy, so a
guard that has stopped discriminating fails loudly instead of passing.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from app.agents.block_executor import _PRECISE_SOURCE_RE
from app.models.task_card import Block
from app.utils.task_card_validation import validate_card_tree

CARD_PATH = Path(__file__).resolve().parents[1] / "design" / "sweep-orchestrated.task-card.json"

# Subcommands and flags no grant on this card may admit.  Each is destructive
# in a way no flag guard makes recoverable, which is why the shell write tier
# deliberately omits them too.
FORBIDDEN_GIT = (
    "reset", "checkout", "restore", "clean", "rebase", "merge",
    "cherry-pick", "filter-branch", "--force", "--mirror", "--prune",
    "--amend", "--no-verify", "--hard",
)

# Grants that mutate the repository.  Everything else a block may ask for has
# to be validate-only.
MUTATING_GRANT_PREFIXES = (
    "git add", "git rm", "git commit", "git push", "git apply",
    # Tag creation mutates: it writes a ref, and --force silently moves one
    # that already exists.  Listed here (rather than treated as read-only)
    # because the read-only floor covers listing and verification ONLY --
    # creation is a signed grant, held by P8 alone.
    "git tag",
)


# ── helpers ────────────────────────────────────────────────────────────────

def load_root() -> Block:
    data = json.loads(CARD_PATH.read_text())
    return Block.model_validate(data["root"])


def iter_blocks(block: Block):
    """Yield every block in the tree, depth-first."""
    yield block
    for child in block.body or []:
        yield from iter_blocks(child)


def iter_sequences(block: Block):
    """Yield (parent, body_list) for every container that forms a sequence."""
    if block.body:
        yield block, list(block.body)
    for child in block.body or []:
        yield from iter_sequences(child)


def find_block(root: Block, name_fragment: str) -> Block:
    for b in iter_blocks(root):
        if name_fragment in (b.name or ""):
            return b
    raise AssertionError(f"no block whose name contains {name_fragment!r}")


def grants_of(block: Block):
    if block.scope is None:
        return []
    return list(block.scope.shell_commands or [])


def writes_of(block: Block):
    if block.scope is None:
        return []
    return [p.path for p in (block.scope.paths or []) if p.write]


# ── the predicates under test, expressed once so mutations can be graded ──

def commit_loops(root: Block):
    """Every repeat whose body is granted a mutating git command.

    Derived from the GRANTS rather than from the block's name, so renaming
    the loop cannot slip it past the serial requirement.
    """
    found = []
    for block in iter_blocks(root):
        if block.block_type != "repeat":
            continue
        for descendant in iter_blocks(block):
            if descendant is block:
                continue
            if any(g.startswith(("git add", "git rm", "git commit"))
                   for g in grants_of(descendant)):
                found.append(block)
                break
    return found


def serial_violations(root: Block):
    return [b.name for b in commit_loops(root) if b.repeat_parallel]


def roster_assertion_violations(root: Block):
    """A commit loop must assert completeness and be keyed and uncapped."""
    problems = []
    for loop in commit_loops(root):
        if not loop.repeat_require_complete:
            problems.append(f"{loop.name}: repeat_require_complete is not set")
        if not loop.repeat_item_key:
            problems.append(f"{loop.name}: repeat_item_key is not declared")
        if loop.repeat_max:
            problems.append(f"{loop.name}: repeat_max={loop.repeat_max} caps an asserted roster")
    return problems


def fanout_seam_violations(root: Block):
    """Every templated for_each roster must be produced by its prior sibling.

    Checks BOTH halves of the seam: that the loop names a precise
    ``previous_sibling.outputs.<name>.<key>`` reference, and that the block
    immediately before it actually emits an artifact by that name carrying
    that key.  Either half alone passes while the fan-out dispatches nothing.
    """
    problems = []
    for _parent, body in iter_sequences(root):
        for i, block in enumerate(body):
            if block.block_type != "repeat" or block.repeat_mode != "for_each":
                continue
            source = block.repeat_for_each_source or ""
            if "{{" not in source:
                continue
            if not _PRECISE_SOURCE_RE.match(source):
                problems.append(
                    f"{block.name}: source {source!r} is not a precise "
                    f"outputs reference, so it is parsed leniently"
                )
                continue
            m = re.match(
                r"^\s*\{\{\s*previous_sibling\.outputs\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\}\}\s*$",
                source,
            )
            if not m:
                problems.append(
                    f"{block.name}: source {source!r} does not read its "
                    f"immediately preceding sibling"
                )
                continue
            part_name, key = m.group(1), m.group(2)
            if i == 0:
                problems.append(f"{block.name}: reads previous_sibling but is first in its body")
                continue
            producer = body[i - 1]
            text = producer.instructions or ""
            if "emit_artifact" not in text:
                problems.append(
                    f"{producer.name}: must emit_artifact to feed {block.name}"
                )
            if f'"{part_name}"' not in text:
                problems.append(
                    f"{producer.name}: does not name artifact part "
                    f'"{part_name}" that {block.name} reads'
                )
            if f'"{key}"' not in text:
                problems.append(
                    f"{producer.name}: does not name key "
                    f'"{key}" inside part "{part_name}"'
                )
    return problems


def escalation_violations(root: Block):
    problems = []
    for block in iter_blocks(root):
        for grant in grants_of(block):
            low = grant.lower()
            for bad in FORBIDDEN_GIT:
                # Match on token boundaries so "git apply" is not read as
                # containing "-f", and a regex grant is checked as text.
                if re.search(rf"(?<![\w-]){re.escape(bad)}(?![\w-])", low):
                    problems.append(f"{block.name}: grant {grant!r} admits {bad!r}")
    return problems


def analysis_blocks(root: Block):
    """Every block in the analysis half, including fan-out leaves.

    Walks the SUBTREE of each read-only phase rather than matching names, so
    the leaf task inside a fan-out — which is where the grant actually lives,
    and therefore the only block worth checking — is included.  Selecting by
    name prefix silently excluded those leaves and made the mutation
    assertion vacuous for them.
    """
    collected = []
    for phase in root.body or []:
        name = phase.name or ""
        if name.startswith(("P0", "P1", "P2", "P3")):
            collected.extend(iter_blocks(phase))
    # The commit-plan reconcile is the last read-only stage, but it lives
    # inside the P4 group alongside the loop that does mutate.
    collected.append(find_block(root, "Reconcile the commit plan"))
    return collected


# ── 1. the card is real and clean ────────────────────────────────────────

def test_card_file_exists_and_parses():
    assert CARD_PATH.exists(), f"{CARD_PATH} is missing"
    data = json.loads(CARD_PATH.read_text())
    assert data["name"], "card has no name"
    assert data["description"], "card has no description"
    Block.model_validate(data["root"])


def test_card_passes_the_real_validator_with_no_errors_or_warnings():
    result = validate_card_tree(load_root())
    assert result.ok, result.summary()
    # Warnings matter here as much as errors: the wide-fan-out and
    # uncapped-until warnings are exactly the shapes this card has to get
    # right, so a clean run is the assertion.
    assert not result.warnings, result.summary()


def test_the_validator_is_actually_discriminating():
    """Positive control for the test above.

    If validate_card_tree approved anything, the clean-run assertion would
    certify nothing.  An asserted roster under a finite cap is a documented
    refusal, so it must come back as an error.
    """
    root = copy.deepcopy(load_root())
    loop = commit_loops(root)[0]
    loop.repeat_max = 60
    result = validate_card_tree(root)
    assert not result.ok, "validator accepted require_complete with a finite repeat_max"


# ── 2. the commit loop is serial ─────────────────────────────────────────

def test_a_commit_loop_exists_at_all():
    """Premise for every assertion below.

    Without this, an empty ``commit_loops`` set would make the serial and
    roster guards pass vacuously.
    """
    loops = commit_loops(load_root())
    assert loops, "no repeat block is granted a mutating git command"
    assert len(loops) == 1, f"expected exactly one commit loop, got {[b.name for b in loops]}"


def test_the_commit_loop_is_serial():
    assert serial_violations(load_root()) == []


def test_a_parallel_commit_loop_is_caught():
    """Mutation control: the git index is a single shared resource, so this
    is the one edit that must never pass silently."""
    root = copy.deepcopy(load_root())
    commit_loops(root)[0].repeat_parallel = True
    assert serial_violations(root), "a parallel commit loop was not detected"


def test_read_only_fanouts_are_still_allowed_to_be_parallel():
    """Negative control: the serial rule is scoped to mutating loops.

    Without this, a fix that simply forbade every parallel repeat would pass
    the test above while destroying the card's entire performance argument.
    """
    root = load_root()
    parallel = [b.name for b in iter_blocks(root)
                if b.block_type == "repeat" and b.repeat_parallel]
    assert parallel, "no analysis fan-out is parallel — the decomposition is gone"


# ── 3. roster completeness ───────────────────────────────────────────────

def test_commit_loop_asserts_roster_completeness():
    assert roster_assertion_violations(load_root()) == []


@pytest.mark.parametrize("field,value", [
    ("repeat_require_complete", False),
    ("repeat_item_key", None),
    ("repeat_max", 60),
])
def test_each_roster_guarantee_is_independently_detected(field, value):
    root = copy.deepcopy(load_root())
    setattr(commit_loops(root)[0], field, value)
    assert roster_assertion_violations(root), f"dropping {field} was not detected"


def test_roster_key_matches_the_field_the_plan_emits():
    """The loop keys iterations on ``entry_id``; the plan must supply it.

    A key naming a field the roster items do not carry is refused at plan
    time — after the analysis half has already been paid for.
    """
    root = load_root()
    loop = commit_loops(root)[0]
    plan = find_block(root, "Reconcile the commit plan")
    assert loop.repeat_item_key == "entry_id"
    assert f'"{loop.repeat_item_key}"' in (plan.instructions or ""), (
        f"the plan stage never names {loop.repeat_item_key!r}, so every "
        f"roster item would be unkeyed"
    )


# ── 4. the fan-out seams ─────────────────────────────────────────────────

def test_every_fanout_roster_reaches_its_loop():
    assert fanout_seam_violations(load_root()) == []


def test_all_three_fanouts_are_actually_checked():
    """Premise: the seam check must be looking at every fan-out.

    A regex that silently matched nothing would make the test above pass.
    """
    root = load_root()
    templated = [b for b in iter_blocks(root)
                 if b.block_type == "repeat"
                 and b.repeat_mode == "for_each"
                 and "{{" in (b.repeat_for_each_source or "")]
    assert len(templated) == 3, f"expected 3 templated fan-outs, found {len(templated)}"


def test_a_reordered_phase_breaks_the_seam_and_is_caught():
    """Mutation control: moving a producer away from its loop.

    This is the realistic edit — inserting a stage between a producer and its
    fan-out — and its failure mode is a loop that dispatches zero iterations
    while reporting success.
    """
    root = copy.deepcopy(load_root())
    for _parent, body in iter_sequences(root):
        for i, block in enumerate(body):
            if (block.block_type == "repeat"
                    and "{{" in (block.repeat_for_each_source or "")
                    and i > 0):
                spacer = Block(block_type="task", name="Inserted stage",
                               instructions="does nothing")
                _parent.body.insert(i, spacer)
                assert fanout_seam_violations(root), (
                    "a producer separated from its fan-out was not detected"
                )
                return
    pytest.fail("found no templated fan-out to mutate")


def test_a_producer_that_stops_emitting_its_part_is_caught():
    """Mutation control for the other half of the seam.

    The loop's reference stays valid-looking while the part it names is gone.
    """
    root = copy.deepcopy(load_root())
    producer = find_block(root, "Index the Unreleased entries")
    producer.instructions = (producer.instructions or "").replace('"contended"', '"renamed"')
    assert fanout_seam_violations(root), (
        "a renamed artifact part was not detected"
    )


# ── 5. failure policy ────────────────────────────────────────────────────

def test_dependent_containers_stop_on_failure():
    """Every container holding a dependent pipeline must set stop.

    Under the default ``continue`` a container reports its LAST child's
    result, so a failed early stage followed by a passing late one reports
    the whole container as SUCCEEDED — the failure is not tolerated, it is
    made invisible.
    """
    root = load_root()
    offenders = []
    for block in iter_blocks(root):
        if block.block_type not in ("group", "repeat", "until"):
            continue
        if not block.body:
            continue
        if "Announce" in (block.name or ""):
            continue  # best-effort by design, asserted separately
        # A loop body of one block has no sequence to halt.
        if block.block_type in ("repeat", "until") and len(block.body) == 1:
            continue
        if block.on_failure != "stop":
            offenders.append(f"{block.name}: on_failure={block.on_failure!r}")
    assert offenders == []


def test_the_announce_group_deliberately_continues():
    """The two announce stages are best-effort and ordered.

    A failed GitHub Release must not skip the Slack post, which is why this
    one group opts out of ``stop`` — the exception is deliberate, so it is
    pinned rather than left to look like an oversight.
    """
    root = load_root()
    announce = find_block(root, "Announce")
    assert announce.block_type == "group"
    assert announce.on_failure == "continue"
    assert len(announce.body) == 2, "announce group should hold the release and Slack stages"


def test_the_until_loop_is_capped():
    """An uncapped until-mode loop cannot terminate on its own.

    The stall breaker exists, but a stated ceiling is what keeps an
    unsatisfiable condition from being the only thing standing between the
    run and its iteration budget.
    """
    root = load_root()
    for block in iter_blocks(root):
        if block.block_type == "until":
            assert block.until_max and 1 < block.until_max <= 5, (
                f"{block.name}: until_max={block.until_max}"
            )
            assert block.until_condition, f"{block.name}: no until_condition"


# ── 6. escalation surface ────────────────────────────────────────────────

def test_no_grant_admits_a_destructive_git_operation():
    assert escalation_violations(load_root()) == []


def test_the_destructive_check_discriminates():
    """Mutation control.

    Without this, a typo'd forbidden list would let every grant through.
    """
    root = copy.deepcopy(load_root())
    loop_task = next(b for b in iter_blocks(root)
                     if any(g.startswith("git commit") for g in grants_of(b)))
    loop_task.scope.shell_commands.append("git reset --hard")
    assert escalation_violations(root), "a git reset --hard grant was not detected"


def test_the_analysis_half_cannot_mutate_the_repository():
    """The read-only phases ask for nothing that changes anything.

    Their one grant is ``git apply --cached --check``, whose multi-word
    token-prefix form matches only the validate-only invocation: a bare
    ``git apply --cached <patch>`` mismatches at the fourth token and is
    refused, which is what makes it safe to hand to six concurrent agents.
    """
    offenders = []
    for block in analysis_blocks(load_root()):
        for grant in grants_of(block):
            if grant.startswith("git apply --cached --check"):
                continue
            if any(grant.startswith(p) for p in MUTATING_GRANT_PREFIXES):
                offenders.append(f"{block.name}: {grant!r}")
        for path in writes_of(block):
            offenders.append(f"{block.name}: writes {path!r}")
    assert offenders == [], (
        "the analysis half is supposed to produce patch files and plans, "
        f"not change the repository: {offenders}"
    )


def test_the_analysis_half_is_not_simply_empty():
    """Positive control for the assertion above.

    An analysis half that had been deleted would satisfy it perfectly.
    """
    blocks = analysis_blocks(load_root())
    assert len(blocks) >= 6, f"only found {len(blocks)} analysis blocks"
    assert any("git apply --cached --check" in g
               for b in blocks for g in grants_of(b)), (
        "the hunk-attribution stage no longer validates its patches"
    )


def test_mutating_grants_appear_only_in_the_execution_half():
    root = load_root()
    granted = {}
    for block in iter_blocks(root):
        mutating = [g for g in grants_of(block)
                    if any(g.startswith(p) for p in MUTATING_GRANT_PREFIXES)
                    and not g.startswith("git apply --cached --check")]
        if mutating:
            granted[block.name] = sorted(mutating)
    # Named explicitly so widening the surface is a test change a reviewer
    # sees, rather than a silent addition.
    assert set(granted) == {
        "Commit one group",
        "Sweep the residue",
        "P7 - Bump the version and finalize the changelog",
        "P8 - Tag",
        "P9 - Push",
    }, f"unexpected mutating-grant surface: {sorted(granted)}"
    assert "git push" in granted["P9 - Push"]
    assert "git push" not in granted["Commit one group"], (
        "the commit loop must not be able to push"
    )
    # Tag creation is confined to the tag stage.  A commit-loop iteration that
    # could also tag would let a mid-loop failure leave a release tag pointing
    # at a partial commit set.
    assert granted["P8 - Tag"] == ["git tag"], (
        f"the tag stage should hold exactly the tag grant: {granted['P8 - Tag']}"
    )
    for name in ("Commit one group", "Sweep the residue", "P9 - Push"):
        assert "git tag" not in granted.get(name, []), (
            f"{name} can create tags, which only the tag stage should"
        )


def test_only_the_changelog_and_bump_stages_write_files():
    root = load_root()
    writers = {b.name: sorted(writes_of(b)) for b in iter_blocks(root) if writes_of(b)}
    assert set(writers) == {
        "P6 - Consolidate the changelog",
        "P7 - Bump the version and finalize the changelog",
    }, f"unexpected write surface: {sorted(writers)}"
    assert writers["P6 - Consolidate the changelog"] == ["CHANGELOG.md"], (
        "the changelog stage should write nothing but the changelog"
    )


# ── 7. the invariants the stages depend on are actually stated ───────────

def test_the_state_block_carries_the_deterministic_rules():
    """The rules that resolve ambiguity have to reach every stage.

    Each task runs in a sandboxed conversation, so a rule stated only in one
    stage's instructions is invisible to the others — which is how a linear
    prompt's "never halt, the rules decide" degenerates into a stage halting
    because it was never told the rule.
    """
    root = load_root()
    state = next(b for b in iter_blocks(root) if b.block_type == "state")
    ctx = state.state_context or ""
    for rule in ("VERSION RULE", "EXCLUSION RULE", "CHANGELOG RULE",
                 "GROUPING RULE", "ERRORS vs AMBIGUITY", "LEDGER"):
        assert rule in ctx, f"the state block never states the {rule}"
    assert state.state_variables, "no state variables declared"
    assert "ledger" in state.state_variables


def test_every_state_variable_is_referenced_by_some_stage():
    """An unreferenced variable is a rule nobody applies."""
    root = load_root()
    state = next(b for b in iter_blocks(root) if b.block_type == "state")
    all_text = " ".join((b.instructions or "") for b in iter_blocks(root))
    unused = [k for k in state.state_variables if f"{{{{var.{k}}}}}" not in all_text]
    assert unused == [], f"declared but never referenced: {unused}"


def test_the_serial_commit_stage_verifies_its_own_commit_landed():
    """Roster completeness is status-shaped, not output-shaped.

    ``repeat_require_complete`` counts an iteration that reported success
    while producing nothing.  The only thing closing that gap is the stage
    checking its own commit exists, so that check is pinned here.
    """
    root = load_root()
    task = next(b for b in iter_blocks(root)
                if any(g.startswith("git commit") for g in grants_of(b))
                and "Commit one group" in (b.name or ""))
    text = task.instructions or ""
    assert "git log -1" in text, "the commit stage never verifies the commit landed"
    assert "git diff --cached --name-only" in text, (
        "the commit stage never verifies the staged set matches its group"
    )
    assert "--check" in text, (
        "the commit stage applies patches without a dry run, and it has no "
        "unstage grant to recover with"
    )


# ── 8. the merge escape: grouping is permitted, collapsing is not ────────
#
# A contended file whose hunks cannot be confidently attributed may be
# committed whole, with the entries that share it recorded.  The whole risk
# of granting that is that it becomes the default and the release degenerates
# into the omnibus commits this card exists to replace, so every assertion
# here is about the escape being BOUNDED and ACCOUNTED, never about it
# existing.

MERGE_DIR_TOKEN = "_merges/"


def test_the_attribution_stage_offers_the_merge_escape():
    text = find_block(load_root(), "Attribute the hunks").instructions or ""
    assert "MERGE ESCAPE" in text, (
        "attribution has no escape, so an agent facing an unsplittable file "
        "must either guess a split or fail the block"
    )
    assert MERGE_DIR_TOKEN in text, (
        "the escape names no place to record the merge, so the entries that "
        "must share a commit are known only to the agent that found them"
    )


def test_the_merge_escape_demands_a_specific_obstacle():
    """A merge without a stated reason is indistinguishable from laziness.

    The reason is the only thing a later reader has to judge whether the
    merge was necessary, so an unreasoned merge has to be refused at BOTH
    ends: the stage that writes it and the stage that applies it.
    """
    root = load_root()
    attrib = find_block(root, "Attribute the hunks").instructions or ""
    plan = find_block(root, "P4a").instructions or ""
    assert "specific obstacle" in attrib, (
        "the escape does not require the agent to name what blocked the split"
    )
    assert "prefer a split" in attrib.lower(), (
        "the escape is offered without a stated preference for splitting, so "
        "it reads as an equally good option rather than a fallback"
    )
    assert "reason" in plan.lower() and "refuse" in plan.lower(), (
        "the plan stage accepts merge records without checking they carry a "
        "reason, so an unreasoned merge still lands"
    )


def test_the_plan_stage_actually_reads_what_attribution_writes():
    """The seam, and the reason this section exists.

    Attribution writing merge records that the plan stage never reads is the
    exact one-hop failure this codebase keeps hitting: both halves are
    individually correct, nothing errors, and every entry in a merge set
    except the one the file happened to land under silently does not ship.
    Neither half's own text can detect that, so it is asserted across them.
    """
    root = load_root()
    attrib = find_block(root, "Attribute the hunks").instructions or ""
    plan = find_block(root, "P4a").instructions or ""
    assert MERGE_DIR_TOKEN in attrib, "attribution writes no merge records"
    assert MERGE_DIR_TOKEN in plan, (
        "the plan stage never reads the merge records attribution writes, so "
        "a recorded merge is discarded and its entries lose their commit"
    )


def test_the_plan_gate_is_coverage_rather_than_a_count():
    """The count gate had to GO, and be REPLACED.

    Comparing group count to entry count is the wrong invariant once merges
    are legal — it fails a legitimately merged plan — but deleting it without
    a replacement would license exactly the collapse it was guarding.  So
    both halves are asserted: the count comparison is gone, and coverage of
    every entry has taken its place.
    """
    plan = find_block(load_root(), "P4a").instructions or ""
    assert "fewer groups than the Unreleased section has" not in plan, (
        "the count gate is still present and will fail any plan that merges, "
        "which is the whole thing being enabled here"
    )
    assert "no group's 'entry_ids'" in plan, (
        "nothing replaced the count gate: an entry belonging to no group can "
        "now vanish with no check catching it"
    )
    assert "more than one group's 'entry_ids'" in plan, (
        "two groups may both claim to satisfy one entry, so neither commit "
        "is the record for it"
    )


def test_the_merge_escape_is_bounded():
    """Without a ceiling the escape IS the omnibus commit, one step removed."""
    plan = find_block(load_root(), "P4a").instructions or ""
    assert "more than half the entries are in merged groups" in plan, (
        "the merge escape has no upper bound, so a run that merged every "
        "contended file would pass every gate and produce the coarse history "
        "this card was written to prevent"
    )


def test_a_merged_group_records_every_entry_it_satisfies():
    """Recording only the primary id sends the residue sweep after entries
    that are already committed, where it finds nothing to stage."""
    text = find_block(load_root(), "Commit one group").instructions or ""
    assert "Append EVERY id" in text, (
        "the commit stage records only the group's primary entry id"
    )
    assert "entry_ids" in text, (
        "the commit stage does not read the group's entry_ids at all"
    )


def test_the_roster_still_keys_on_a_field_every_group_carries():
    """Positive control for the roster assertion under the new shape.

    'entry_ids' is a list and cannot be a roster key; the primary scalar
    'entry_id' still has to be present on every group, or repeat_item_key
    resolves to nothing and the completeness assertion cannot name what is
    missing.
    """
    root = load_root()
    loop = find_block(root, "P4b")
    assert loop.repeat_item_key == "entry_id", (
        f"roster key is {loop.repeat_item_key!r}; a list-valued key cannot "
        f"identify an iteration"
    )
    plan = find_block(root, "P4a").instructions or ""
    assert "'entry_id' is the first of them" in plan, (
        "the plan never states that a group carries a scalar entry_id "
        "alongside the list, so a merged group may omit it"
    )
    assert loop.repeat_require_complete is True, (
        "roster completeness was relaxed along with the grouping rule, which "
        "is a different concession than the one being made here"
    )


# ---------------------------------------------------------------------------
# 7. The announce stages are gated on the push, independently of block status
# ---------------------------------------------------------------------------
#
# Observed on the linear ``sweep`` task: a run that never finished committing
# still posted its announcement to Slack.  The orchestrated card's root is
# ``on_failure: stop``, but that gate fires only if the push stage MARKED its
# artifact failed -- a stage that narrates "push failed" and ends its turn
# succeeded lets the announce group run.  So each announce stage must verify
# the push from git itself, before touching any external surface.

ANNOUNCE_STAGES = ("P10a - GitHub Release", "P10b - Announce in Slack")
GATE_CHECKS = ("git tag -l v<TARGET>", "git status -sb", "git log -1 --format=%s")


@pytest.mark.parametrize("stage", ANNOUNCE_STAGES)
def test_each_announce_stage_carries_its_own_push_gate(stage):
    text = find_block(load_root(), stage).instructions or ""
    assert "PUSH GATE" in text, f"{stage}: no push gate"
    for check in GATE_CHECKS:
        assert check in text, f"{stage}: gate does not verify with `{check}`"
    assert "mode == 'pushed'" in text, f"{stage}: gate ignores the ledger's pushed marker"
    assert "post NOTHING" in text, f"{stage}: gate failure does not forbid posting"
    assert "fail this block" in text, f"{stage}: a failed gate does not fail the block"


def test_the_gate_runs_before_the_announcement_not_after():
    """A gate placed after the posting steps would verify a release that was
    already announced.  'PUSH GATE' must precede the first external action."""
    for stage, action in ((ANNOUNCE_STAGES[0], "gh release create"),
                          (ANNOUNCE_STAGES[1], "Post, in order")):
        text = find_block(load_root(), stage).instructions or ""
        assert text.index("PUSH GATE") < text.index(action), (
            f"{stage}: the gate appears after the external action"
        )


def test_the_gate_is_stated_as_independent_of_upstream_prose():
    """The gate's whole point is to not trust the push stage's self-report."""
    for stage in ANNOUNCE_STAGES:
        text = find_block(load_root(), stage).instructions or ""
        assert "not from the ledger or any earlier stage's prose" in text, (
            f"{stage}: gate may be satisfied by what an earlier stage said"
        )
    state = next(b for b in iter_blocks(load_root()) if b.block_type == "state")
    assert "ANNOUNCE RULE" in (state.state_context or "")


def test_the_push_stage_fails_its_block_when_the_push_did_not_land():
    """Belt to the gate's braces: the structural stop only fires on a FAILED
    artifact, so the push stage must be told that narrating is not enough."""
    text = find_block(load_root(), "P9 - Push").instructions or ""
    assert "git tag -l v<TARGET>" in text
    assert "fail this block" in text
    assert "Do not merely report it" in text


# ---------------------------------------------------------------------------
# 8. Planning is mechanical where it can be; judgement is recorded as data
# ---------------------------------------------------------------------------
#
# The v0.8.9.0 sweep stalled on a tree with 86 contended files (app/server.py
# claimed by eleven entries).  Hand-writing per-entry diffs for that is where
# hunk-header and context errors come from, and the only tooling in the tree
# was scratch hardcoded for the previous release.  The card now drives two
# release-agnostic scripts and the agent's output shrinks to a {hunk: entry}
# mapping that the splitter validates with git apply --cached --check.

def test_the_index_stage_runs_the_release_agnostic_indexer():
    text = find_block(load_root(), "P2a").instructions or ""
    assert "scripts/release_plan.py index" in text
    assert "--ledger {{var.ledger}}" in text, "the index is not written to the ledger"
    assert "Do NOT re-derive the partition by hand" in text


def test_the_attribution_stage_records_a_mapping_and_lets_the_splitter_write_patches():
    text = find_block(load_root(), "Attribute the hunks of one file").instructions or ""
    assert "scripts/release_hunks.py dump {{item}}" in text
    assert "scripts/release_hunks.py split --file {{item}}" in text
    assert "{{var.attrib_dir}}" in text, "the attribution mapping has no declared home"
    assert "Do not hand-write patch files" in text
    # The merge escape goes through the same tool so a generic reason is refused.
    assert "scripts/release_hunks.py merge --file {{item}}" in text
    assert MERGE_DIR_TOKEN in text


def test_the_tooling_the_card_names_exists_and_is_release_agnostic():
    """The seam: a card that names a script which is not there, or which is
    scratch for one release, is the exact failure being fixed."""
    repo = CARD_PATH.parents[1]
    for rel in ("scripts/release_plan.py", "scripts/release_hunks.py"):
        src = (repo / rel).read_text(encoding="utf-8")
        for literal in ("AFFINITY", "EXCLUSIONS", "HUNK_CENSUS", "0.8.7.0"):
            assert literal not in src, f"{rel} carries release-specific literal {literal!r}"
    plan = (repo / "scripts/release_plan.py").read_text(encoding="utf-8")
    assert 'add_parser("index"' in plan
    hunks = (repo / "scripts/release_hunks.py").read_text(encoding="utf-8")
    for sub in ("dump", "split", "merge"):
        assert f'add_parser("{sub}"' in hunks, f"release_hunks.py has no {sub} mode"
    assert "--cached" in hunks and "--check" in hunks

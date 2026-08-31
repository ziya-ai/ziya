"""The structure_trees skill must exist and must not advertise a dead fence.

Two distinct failure modes are guarded here, and only the second is about the
skill's prose:

1. REGISTRATION.  A skill is reached only through the model-discoverable
   catalog, so a skill added to ``BUILT_IN_SKILLS`` with the wrong visibility
   or no ``catalog_description`` is invisible -- present in the file, never
   offered to the model.

2. FENCE PARITY -- the valuable one.  The skill's whole job is to tell the
   model which fence to type.  If it advertises ```forest while the routing
   registry does not map ``forest`` to a backend profile, the model follows the
   instruction and gets a plain code block: the exact ``chemfig`` defect that
   motivated constants/latexProfiles.ts, arriving from the other direction.
   The existing cross-layer guard (latexFenceRouting.test.ts) compares the
   Python registry against the TS registry; NOTHING compared either against
   what a skill actually tells the model to type.

   So this asserts the fence languages named in the skill prompt round-trip
   through BOTH registries -- Python ``PROFILES`` and the TS
   ``LATEX_LANG_TO_PROFILE`` -- rather than trusting that a skill author and a
   registry author agreed.

3. DEAD GUIDANCE (TestNoDeadGuidance).  Added after compiling every construct
   the first draft taught and finding FOUR that cannot work: ``\\def`` to set
   ``\\fCenter`` (the prescan rejects all macro definition), a ``phantom`` node
   as an arrow target (no shape to point at), ``roof`` alongside a claim that
   the library providing it is unavailable, and no warning that the wrapper
   already emits ``\\DisplayProof``.  A skill that teaches a construct which
   aborts the compile is worse than a missing skill: the model follows it
   confidently and the render fails.  Those assertions are NEGATIVE, and each
   was confirmed to fail against the original draft.

The prose assertions are deliberately thin: they check load-bearing CLAIMS
(the auto-wrap contract, the stack discipline that is bussproofs' one real
trap) rather than wording, so the skill can be reworded without going red.
The positive counterparts -- that each documented construct actually compiles
-- live in tests/test_latex_tree_profiles.py, which drives the real toolchain.
"""
import re

import pytest

from app.data.built_in_skills import (
    MODEL_DISCOVERABLE,
    get_model_discoverable_skills,
    get_skill_by_id,
)
from app.services.latex_profiles import PROFILES

SKILL_ID = "structure_trees"

#: Fence languages the skill is expected to teach, and the profile each must
#: resolve to.  Written out rather than scraped from the prompt so a skill that
#: silently STOPS mentioning a fence also fails, instead of the test quietly
#: shrinking to whatever the prompt happens to say.
EXPECTED_FENCES: dict[str, str] = {
    "forest": "forest",
    "bussproofs": "bussproofs",
}


@pytest.fixture(scope="module")
def skill() -> dict:
    s = get_skill_by_id(SKILL_ID)
    if s is None:
        pytest.fail(
            f"skill {SKILL_ID!r} is not registered in BUILT_IN_SKILLS; the "
            "labelled-tree/proof-tree profiles have no syntax reference, so "
            "the model must recall forest bracket syntax from memory"
        )
    return s


class TestRegistration:
    def test_skill_is_model_discoverable(self, skill):
        assert skill["visibility"] == MODEL_DISCOVERABLE

    def test_skill_appears_in_the_discoverable_catalog(self, skill):
        ids = [s["id"] for s in get_model_discoverable_skills()]
        assert SKILL_ID in ids

    def test_skill_has_a_catalog_description(self, skill):
        assert skill.get("catalog_description")

    def test_keywords_cover_both_notations(self, skill):
        kw = {k.lower() for k in skill.get("keywords", [])}
        # A keyword miss is a silent discovery failure, so assert the two
        # notation families are both reachable by name.
        assert kw & {"forest", "syntax-tree", "syntax tree", "tree"}, (
            "no labelled-tree keyword"
        )
        assert kw & {"bussproofs", "proof", "proof-tree", "prooftree",
                     "derivation", "natural-deduction"}, "no proof-tree keyword"


class TestFenceParity:
    """The seam: what the skill tells the model to type must actually route."""

    def test_prompt_names_every_expected_fence(self, skill):
        prompt = skill["prompt"]
        for lang in EXPECTED_FENCES:
            assert lang in prompt, (
                f"skill does not mention the {lang!r} fence, so the model has "
                f"no way to learn it exists"
            )

    @pytest.mark.parametrize("lang,profile_key", sorted(EXPECTED_FENCES.items()))
    def test_fence_resolves_to_a_backend_profile(self, lang, profile_key):
        assert profile_key in PROFILES, (
            f"skill advertises ```{lang} but the backend has no {profile_key!r} "
            f"profile; the fence compiles nothing"
        )

    @pytest.mark.parametrize("lang,profile_key", sorted(EXPECTED_FENCES.items()))
    def test_fence_is_routed_by_the_frontend_registry(self, lang, profile_key):
        """Guards the middle step that made ``chemfig`` ship broken.

        Read as SOURCE TEXT rather than imported, because the registry is
        TypeScript; the TS-side test asserts the same map behaviourally.  A
        substring check is enough: the map is a flat literal of
        ``'lang': 'profile',`` pairs.
        """
        import os

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "frontend", "src", "constants", "latexProfiles.ts")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()

        # Self-check: a truncated/moved file must not silently pass every case.
        assert "LATEX_LANG_TO_PROFILE" in src, f"unexpected contents at {path}"

        pattern = re.compile(
            r"^\s*'" + re.escape(lang) + r"'\s*:\s*'" + re.escape(profile_key) + r"'\s*,",
            re.MULTILINE,
        )
        assert pattern.search(src), (
            f"latexProfiles.ts does not map fence {lang!r} -> {profile_key!r}; "
            f"the fence falls through to a plain code block even though both "
            f"ends of the pipeline support it"
        )


class TestNoDeadGuidance:
    """Guards against the skill teaching constructs that do not compile.

    Every assertion here corresponds to a claim the FIRST draft of this skill
    made that was proven wrong by compiling it (see
    tests/test_latex_tree_profiles.py, which compiles the positive cases):

      * it told the model to write ``\\def\\fCenter{...}``, which the renderer's
        prescan rejects outright -- so the whole sequent section was unusable.
      * it aimed a movement arrow at a ``phantom`` node, which produces no
        shape, so the compile aborts with "No shape named ... is known".
      * it stated that forest's linguistics library is unavailable while also
        documenting ``roof``, which lives in that library -- self-contradictory,
        and the profile now loads it.

    These are NEGATIVE assertions, which are worth stating explicitly: a skill
    can be reworded freely, but reintroducing any of these would silently start
    producing bodies that fail to compile.
    """

    def test_does_not_tell_the_model_to_define_macros(self, skill):
        prompt = skill["prompt"]
        # The prescan rejects \def, \newcommand, \renewcommand, \let, \edef,
        # \gdef.  Any of them presented as usable guidance is a dead end.
        for prim in (r"\def\fCenter", r"\newcommand{\fCenter}",
                     r"\renewcommand{\fCenter}"):
            assert prim not in prompt, (
                f"skill instructs the model to write {prim!r}, which the "
                f"renderer rejects before compiling (macro definitions are "
                f"refused); the guidance cannot be followed"
            )

    def test_states_that_macro_definition_is_unavailable(self, skill):
        """The prohibition must be stated, not merely avoided.

        Without it a model reaches for \\newcommand on its own initiative --
        the natural LaTeX habit -- and gets a rejection it cannot diagnose.
        """
        prompt = skill["prompt"]
        assert re.search(r"newcommand", prompt), (
            "skill never mentions \\newcommand, so the model is not told that "
            "macro definition is rejected"
        )
        assert re.search(r"cannot define macros|are all rejected|not define",
                         prompt, re.IGNORECASE), (
            "skill mentions macro primitives but does not say they are refused"
        )

    def test_does_not_aim_arrows_at_phantom_nodes(self, skill):
        """``phantom`` occupies space but produces no shape to point at."""
        prompt = skill["prompt"]
        assert not re.search(r"name=\w+,\s*phantom|phantom,\s*name=", prompt), (
            "skill names a phantom node as an arrow target; TikZ aborts with "
            "'No shape named ... is known'"
        )

    def test_does_not_claim_the_forest_libraries_are_unavailable(self, skill):
        """The profile loads linguistics+edges, so `roof` and forked edges work."""
        prompt = skill["prompt"]
        assert not re.search(r"NOT AVAILABLE.*linguistics|linguistics.*not (be )?loaded",
                             prompt, re.IGNORECASE | re.DOTALL), (
            "skill says the forest linguistics library is unavailable, but the "
            "profile loads it -- the model will avoid `roof`, the main reason "
            "to use forest for a syntax tree"
        )

    def test_warns_against_a_model_supplied_displayproof(self, skill):
        """The wrap emits \\DisplayProof; a second one aborts.

        \\DisplayProof is documented bussproofs usage, so a model may well
        write it unprompted -- and gets "Proof tree badly specified".
        """
        prompt = skill["prompt"]
        assert "DisplayProof" in prompt, (
            "skill never mentions \\DisplayProof, which the wrapper supplies; a "
            "model writing it on its own gets an abort with no image"
        )
        m = re.search(r"(DO NOT WRITE|do not write|never write)\s*\\?DisplayProof",
                      prompt, re.IGNORECASE)
        assert m, (
            "skill mentions \\DisplayProof but does not tell the model not to "
            "emit it"
        )


class TestLoadBearingClaims:
    """Only claims whose absence would make the model emit a failing body."""

    def test_states_the_autowrap_contract(self, skill):
        """The profiles wrap the body, so a preamble in the body is fatal.

        Every sibling LaTeX skill states this; omitting it is the single most
        likely way for a first render to fail.
        """
        prompt = skill["prompt"].lower()
        assert "documentclass" in prompt, (
            "skill does not warn that \\documentclass is rejected"
        )

    def test_documents_the_bussproofs_stack_discipline(self, skill):
        """bussproofs' one genuine trap.

        \\BinaryInfC consumes the top TWO pending subproofs; emitting it with
        one pending is a hard 'bad proof tree' abort with no image.  A skill
        that lists the commands without this is materially incomplete.
        """
        prompt = skill["prompt"]
        assert "\\BinaryInfC" in prompt or "BinaryInfC" in prompt
        assert re.search(r"stack|pending|consume", prompt, re.IGNORECASE), (
            "skill lists the inference commands but never explains that they "
            "consume pending subproofs, which is the trap that aborts renders"
        )

    def test_warns_that_forest_brackets_are_structural(self, skill):
        """A literal [ or ] in a node label breaks forest's parser."""
        prompt = skill["prompt"]
        assert re.search(r"brace|\{|escap", prompt, re.IGNORECASE)

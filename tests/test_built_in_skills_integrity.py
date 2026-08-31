"""
Structural invariants for the built-in skill registry.

These exist because a real duplicate shipped: the `circuit_diagrams` entry was
applied twice, byte for byte.  Nothing failed loudly -- `BUILT_IN_SKILLS` is
just a list, so the registry happily held two copies, and the only symptom was
the model-discoverable catalog listing the skill twice in EVERY system prompt.
That is a silent per-request token cost, which is exactly the class of bug the
catalog design is meant to avoid.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _codebase_dir():
    os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", os.getcwd())


def _skills():
    from app.data.built_in_skills import BUILT_IN_SKILLS
    return BUILT_IN_SKILLS


class TestRegistryIntegrity:
    def test_ids_are_unique(self):
        """The defect that motivated this file.

        `get_skill_by_id` returns the FIRST match, so a duplicate is not merely
        wasteful -- it makes edits ambiguous.  Editing the second copy has no
        effect on lookups, which is a genuinely confusing failure to debug.
        """
        ids = [s["id"] for s in _skills()]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        assert not duplicates, (
            f"duplicate skill id(s) in BUILT_IN_SKILLS: {duplicates}. "
            "A duplicate is advertised twice in every system prompt, and "
            "get_skill_by_id only ever returns the first copy."
        )

    def test_required_fields_present(self):
        for skill in _skills():
            for field in ("id", "name", "description", "visibility", "prompt"):
                assert skill.get(field), (
                    f"skill {skill.get('id')!r} is missing required field {field!r}"
                )

    def test_visibility_is_a_known_value(self):
        from app.data.built_in_skills import MODEL_DISCOVERABLE, USER_SELECTABLE
        allowed = {MODEL_DISCOVERABLE, USER_SELECTABLE}
        for skill in _skills():
            assert skill["visibility"] in allowed, (
                f"skill {skill['id']!r} has unknown visibility "
                f"{skill['visibility']!r}; expected one of {allowed}"
            )

    def test_model_discoverable_skills_have_a_catalog_description(self):
        """Without one the catalog falls back to `description`, which is
        written for a different audience and reads badly in the one-line form."""
        from app.data.built_in_skills import get_model_discoverable_skills
        for skill in get_model_discoverable_skills():
            assert skill.get("catalog_description"), (
                f"model-discoverable skill {skill['id']!r} has no "
                "catalog_description"
            )


class TestCatalogHasNoDuplicates:
    def test_each_skill_appears_at_most_once_in_the_catalog(self):
        """Guards the observable symptom, not just the cause.

        Deliberately independent of test_ids_are_unique: this asserts on the
        rendered prompt text, so it also catches a duplicate introduced further
        down the pipeline (e.g. a file-discovered SKILL.md colliding with a
        built-in id).
        """
        from app.utils.skill_catalog_prompt import get_skill_catalog_section

        catalog = get_skill_catalog_section()
        if not catalog:
            pytest.skip("skill catalog disabled in this environment")

        bullets = [ln for ln in catalog.splitlines() if ln.strip().startswith("•")]
        ids = [ln.split("•", 1)[1].split("—", 1)[0].strip() for ln in bullets]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        assert not duplicates, (
            f"skill(s) listed more than once in the catalog: {duplicates}"
        )


class TestCircuitDiagramsSkill:
    """The circuit skill carries facts extracted from the circuitikz package
    source.  Most users cannot read that source, so if these notes are lost the
    knowledge is genuinely gone -- hence pinning the load-bearing ones."""

    @pytest.fixture
    def prompt(self):
        from app.data.built_in_skills import get_skill_by_id
        skill = get_skill_by_id("circuit_diagrams")
        assert skill, "circuit_diagrams skill is missing from the registry"
        return skill["prompt"]

    def test_documents_the_bipole_versus_shape_distinction(self, prompt):
        """The highest-cost mistake: it fails SILENTLY.  The label renders, the
        symbol does not, and the diagram looks plausible."""
        assert "BIPOLES" in prompt and "SHAPES" in prompt
        assert "to[amp]" in prompt, "no bipole usage example"
        assert "\\node[mixer]" in prompt, "no shape usage example"

    def test_documents_crossing_conventions(self, prompt):
        """A crossing that doesn't say whether it connects changes what the
        circuit does, so both markings must be present."""
        assert "node[circ]" in prompt, "connected-crossing marker undocumented"
        assert "arc (180:0:0.3)" in prompt, "horizontal hop form undocumented"
        assert "arc (270:90:0.3)" in prompt, "vertical hop form undocumented"

    def test_warns_that_jump_crossing_does_not_break_the_wire(self, prompt):
        """`jump crossing` looks like the right tool and silently isn't: it
        paints over a wire that is still drawn straight through the gap."""
        assert "jump crossing" in prompt
        assert "DECORATION" in prompt or "decoration" in prompt, (
            "the jump-crossing trap is named but not explained as a decoration"
        )

    def test_warns_about_mirrored_directional_symbols(self, prompt):
        """A `dac` drawn right-to-left renders as 'A/D' -- a factually wrong
        diagram that compiles cleanly."""
        assert "MIRROR" in prompt
        assert "A/D" in prompt, "the dac/adc mirroring example is missing"

    def test_documents_the_port_anchors(self, prompt):
        for anchor in (".1", ".2", ".3", ".4"):
            assert anchor in prompt, f"port anchor {anchor} undocumented"
        assert ".south" in prompt, "antenna feed anchor undocumented"

    def test_forbids_document_preamble(self, prompt):
        """The backend wraps the body and rejects a full document."""
        assert "documentclass" in prompt


class TestPgfplotsSkill:
    """The pgfplots skill records facts about the SERVER-SIDE PREAMBLE, which
    a model authoring a plot cannot see and cannot infer.

    Every pin below corresponds to a real fatal-abort or silent-wrong-output
    that was hit while probing the renderer.  Without the skill the next author
    rediscovers them the same way: by having a diagram fail to render at all.
    """

    @pytest.fixture
    def prompt(self):
        from app.data.built_in_skills import get_skill_by_id
        skill = get_skill_by_id("pgfplots")
        assert skill, "pgfplots skill is missing from the registry"
        return skill["prompt"]

    def test_documents_the_degrees_trap_for_trig(self, prompt):
        """pgfmath's sin/cos take DEGREES.  `sin(x)` over a 0..2pi domain
        compiles cleanly and draws a nearly-straight line -- wrong output with
        no error, which is worse than a crash."""
        assert "deg(" in prompt, "the deg() conversion idiom is undocumented"
        assert "degree" in prompt.lower()

    def test_documents_which_math_packages_are_preloaded(self, prompt):
        """The gap that motivated the skill: \\dfrac in a legend entry aborted
        fatally because no profile loaded amsmath.  Now that it IS loaded, the
        skill must say so -- otherwise an author avoids it defensively."""
        assert "amsmath" in prompt
        assert "\\dfrac" in prompt, "no concrete amsmath command named"

    def test_flags_siunitx_as_present_but_not_guaranteed(self, prompt):
        """siunitx is loaded with \\IfFileExists, so on a TeX install lacking
        it \\si{} degrades silently rather than aborting.  An author who does
        not know that cannot interpret a missing unit."""
        assert "siunitx" in prompt
        assert "\\si" in prompt

    def test_names_the_preloaded_pgfplots_libraries(self, prompt):
        """A library that is NOT preloaded needs an explicit
        \\usepgfplotslibrary line, so the preloaded set is load-bearing."""
        for lib in ("fillbetween", "statistics", "polar", "dateplot",
                    "groupplots"):
            assert lib in prompt, f"preloaded library {lib} undocumented"

    def test_documents_data_space_annotation_anchoring(self, prompt):
        """`at (2,3)` inside an axis is canvas space, not data space; the
        annotation lands in the wrong place and still renders."""
        assert "axis cs:" in prompt

    def test_forbids_document_preamble(self, prompt):
        """The backend wraps the body in tikzpicture and REJECTS a full
        document, so an author who adds one gets a hard rejection."""
        assert "documentclass" in prompt
        assert "tikzpicture" in prompt


class TestPromptsAreLoadedOnDemand:
    def test_bodies_are_substantial_enough_to_warrant_lazy_loading(self):
        """If a body were tiny, the on-demand indirection would cost more than
        it saves -- worth knowing if that ever changes."""
        from app.data.built_in_skills import get_model_discoverable_skills
        for skill in get_model_discoverable_skills():
            body = skill.get("prompt") or ""
            assert len(body) > 100, (
                f"skill {skill['id']!r} has a {len(body)}-char body; a body this "
                "small does not justify a get_skill_details round-trip"
            )

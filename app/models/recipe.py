"""
Recipe data models — design/recipe-store.md §2–§4.

A recipe is a skill directory (SKILL.md with ``kind: recipe``) plus two
machine-readable files:

  signature.json   the contract: what it takes, what comes back, what a
                   row means, time semantics, and (detectors) attribution
  provenance.json  where it came from and where it is in its lifecycle

and a ``procedure/`` directory holding the script or query template.  The
content address (``sha256``) covers signature + procedure only; see
app/recipes/store.py::compute_recipe_sha.

Models are lenient on read (``extra="allow"``) so a hand-edited or
harvested file with an unknown field still loads; the things a recipe needs
before it may leave probation are reported by
``RecipeSignature.promotion_blockers`` rather than refused at parse time.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .skill import Skill

# File layout inside a recipe directory.
SIGNATURE_FILE = "signature.json"
PROVENANCE_FILE = "provenance.json"
PROCEDURE_DIR = "procedure"
# SKILL.md frontmatter key that selects the recipe kind.
KIND_KEY = "kind"

RecipeKind = Literal["query", "script", "detector"]
OutputShape = Literal["rows", "scalar", "text", "chart", "file"]
RecipeStatus = Literal["probationary", "active", "archived"]
RecipeTier = Literal["project", "user"]

RECIPE_KINDS = ("query", "script", "detector")
OUTPUT_SHAPES = ("rows", "scalar", "text", "chart", "file")
RECIPE_STATUSES = ("probationary", "active", "archived")

# Decision 3 (§8): a tabular result must have a schema to leave probation;
# prose/chart/file results need only their row_means-equivalent prose.
SHAPES_REQUIRING_SCHEMA = frozenset({"rows", "scalar"})
# §3: time semantics are mandatory where a source can back-fill.
KINDS_REQUIRING_TIME = frozenset({"query", "detector"})


def _now_ms() -> int:
    return int(time.time() * 1000)


class RecipeInput(BaseModel):
    """One named parameter.  JSON-Schema-ish on purpose so recipe_run can
    validate without a second validator."""
    model_config = ConfigDict(extra="allow")

    type: str = "string"
    required: bool = False
    default: Any = None
    unit: Optional[str] = None
    description: Optional[str] = None


class RecipeTime(BaseModel):
    """How the result relates to time: which column, which zone, whether
    the source back-fills and how long it takes to settle."""
    model_config = ConfigDict(extra="allow")

    column: Optional[str] = None
    zone: str = "UTC"
    lookback_from: Optional[str] = None
    source_backfills: bool = False
    settle_hours: Optional[float] = None


class RecipeOutput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True,
                              serialize_by_alias=True)

    shape: OutputShape = "text"
    # A registry ref (``project:<name>``, ``ziya:<name>/v<n>``) or an inline
    # JSON Schema; resolved by app/utils/output_contracts.py.  Python name is
    # ``schema_`` because ``schema`` shadows a BaseModel attribute.
    schema_: Optional[Union[str, Dict[str, Any]]] = Field(default=None, alias="schema")
    row_means: Optional[str] = None
    units: Dict[str, str] = Field(default_factory=dict)
    time: Optional[RecipeTime] = None
    baked_filters: List[str] = Field(default_factory=list)


class RecipeRuntime(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Path relative to the recipe directory, under procedure/.
    entry: str = ""
    # Interpreter or wrapper that runs ``entry`` (e.g. "ch.py", "python3").
    via: Optional[str] = None
    # Data source the procedure reads ("grafana:clickhouse").
    source: Optional[str] = None


class RecipeAttribution(BaseModel):
    """What a detector's positive result means operationally."""
    model_config = ConfigDict(extra="allow")

    ticket: Optional[str] = None
    known_since: Optional[str] = None
    condition: Optional[str] = None


class RecipeSignature(BaseModel):
    """The ad-hoc contract (§3)."""
    model_config = ConfigDict(extra="allow")

    recipe_kind: RecipeKind = "script"
    intent: str = ""
    # Memory mind-map node ids plus free tags; one search vocabulary.
    domain: List[str] = Field(default_factory=list)
    inputs: Dict[str, RecipeInput] = Field(default_factory=dict)
    output: RecipeOutput = Field(default_factory=RecipeOutput)
    runtime: Optional[RecipeRuntime] = None
    attribution: Optional[RecipeAttribution] = None
    # Stamped by the store on write; excluded from its own hash.
    sha256: Optional[str] = None

    def promotion_blockers(self) -> List[str]:
        """Why this recipe may not leave probation yet (empty = eligible).

        Human-readable so the queue can show the reason next to the card.
        """
        reasons: List[str] = []
        if not self.intent.strip():
            reasons.append("intent is empty")
        if self.runtime is None or not (self.runtime.entry or "").strip():
            reasons.append("runtime.entry names no procedure")
        if self.output.shape in SHAPES_REQUIRING_SCHEMA and not self.output.schema_:
            reasons.append(
                f"output.shape '{self.output.shape}' needs an output schema")
        if self.recipe_kind in KINDS_REQUIRING_TIME and self.output.time is None:
            reasons.append(
                f"recipe_kind '{self.recipe_kind}' needs output.time semantics")
        if self.recipe_kind == "detector":
            if self.attribution is None or not (self.attribution.condition or "").strip():
                reasons.append("a detector needs attribution.condition")
        return reasons


class RecipeOrigin(BaseModel):
    model_config = ConfigDict(extra="allow")

    conversation_id: Optional[str] = None
    message_index: Optional[int] = None
    project_id: Optional[str] = None


class RecipeLedgerRefs(BaseModel):
    model_config = ConfigDict(extra="allow")

    namespace: Optional[str] = None
    obs_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)


class RecipeUse(BaseModel):
    """One cross-conversation use: the promotion signal (§4)."""
    model_config = ConfigDict(extra="allow")

    conversation_id: str
    at: int = Field(default_factory=_now_ms)
    # Content address at the time of use, so a later edit is visible.
    sha: Optional[str] = None


class RecipeProvenance(BaseModel):
    """Heritage and lifecycle (§4).  Not part of the content address."""
    model_config = ConfigDict(extra="allow")

    status: RecipeStatus = "probationary"
    created_by: Literal["harvest", "bank", "import"] = "bank"
    origin: RecipeOrigin = Field(default_factory=RecipeOrigin)
    ledger: RecipeLedgerRefs = Field(default_factory=RecipeLedgerRefs)
    datasets: List[str] = Field(default_factory=list)
    corroborations: int = 0
    corroborated_by: List[str] = Field(default_factory=list)
    used_by: List[RecipeUse] = Field(default_factory=list)
    # Memory lifecycle's global activity counter at creation; ages the
    # recipe on the same clock as proposals (app/memory/lifecycle.py).
    activity_count_at_creation: int = 0
    created_at: int = Field(default_factory=_now_ms)
    promoted_at: Optional[int] = None
    archived_at: Optional[int] = None
    archive_reason: Optional[str] = None
    # Repo path after "share to repo" (§7).
    shared_to: Optional[str] = None


class Recipe(BaseModel):
    """A recipe as loaded from disk: the skill view plus its two files."""
    model_config = ConfigDict(extra="allow")

    skill: Skill
    signature: RecipeSignature
    provenance: RecipeProvenance
    # Absolute directory path and which tier it was found in.
    path: str
    tier: RecipeTier
    # Current content address, recomputed on load (may differ from
    # signature.sha256 if the files were edited by hand).
    sha256: str

    @property
    def name(self) -> str:
        return self.skill.name

    @property
    def status(self) -> str:
        return self.provenance.status

    def ref(self) -> str:
        """The form a ledger claim's ``derivation.code_ref`` cites."""
        return f"recipe:{self.name}@{self.sha256}"

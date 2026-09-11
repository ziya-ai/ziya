# Cost Model

Status: phase 1 implemented (catalog, rate-plan seam, ledger, meter, rollup
API). UI surfaces pending. User documentation: `Docs/CostAccounting.md`.

Ziya currently counts tokens (`IterationUsage` in `app/streaming_tool_executor.py`)
but has no notion of what they cost. This document defines a cost layer that
works for every provider Ziya supports (public list prices), for internal
transfer-pricing schemes such as Amazon's IMR, for negotiated enterprise rates,
and for local models — without the core knowing which of those a given user is
on.

## Principles

1. **Tokens are the durable fact; cost is derived.** Usage records are stored
   once and never re-priced in place. Cost is always `counts × rate` at display
   or rollup time, tagged with the catalog version used.
2. **Never guess a price.** If a rate is unknown, cost is `null` with
   `provenance = unknown`. Zero is a real price (local models) and is never
   used as a placeholder.
3. **Price the resolved model, not the alias.** The same weights cost different
   amounts on Bedrock vs. Anthropic-direct. Keys are provider + resolved model
   id + region.
4. **Attribution below the billing account is Ziya's job.** No provider can
   tell a user which conversation or project spent what. Every record carries
   user / project / conversation / run attribution.
5. **Rate plans are a plugin seam.** Open-source ships public-list pricing;
   proprietary discount tables (e.g. IMR) load through the existing
   open-core / closed-plugin extension mechanism.

## Estimate vs. actual

Every usage record has a `status` describing where its token counts came from:

| Status | Rendered | Counts from | When |
|---|---|---|---|
| `estimate` | *italic* | Ziya's pre-flight context count | Written when the request is dispatched |
| `actual` | **bold** | Provider usage block on the response | Record updated in place when usage arrives |

A record that never receives provider usage (throttled, stream aborted,
provider returns no usage) remains `estimate` permanently. Rollups containing
any `estimate` line render italic, with the estimate/actual split available on
hover.

This axis is independent of rate provenance (below). A line can be **actual**
tokens priced at a *default-rule* rate; the bold/italic reflects the tokens,
the provenance flag reflects the rate.

Implementation: `app/cost/meter.py`. `open_iteration` writes the `estimate`
row immediately after `iteration_usage = IterationUsage()` in
`_stream_with_tools_impl`, using `chars / 4` of the submitted conversation as
the pre-flight figure (cheap by design — the status flag says it is an
estimate). `close_iteration` actualizes it where the executor appends to
`iteration_usages` after the provider's usage event has been folded in.

## Data model

### UsageRecord (immutable except `status` / `dims` on actualization)

```
id
ts
provider            bedrock | anthropic | openai | google | zai | ollama | ...
model_id            resolved id, e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0
region              provider region or null
tier                standard | priority | flex | batch      (always `standard` today)
status              estimate | actual
dims                { <dimension>: count, ... }
attribution         { user, project, conversation_id, run_id?, block_id? }
```

### Canonical dimensions

Providers map native usage fields onto these. Unmapped fields are preserved
under their native name and priced as `unknown`.

| Dimension | Notes |
|---|---|
| `input` | Uncached prompt tokens |
| `output` | Completion tokens |
| `cache_read` | Prompt-cache hits |
| `cache_write_5m` | Default-TTL cache creation (Anthropic/Bedrock) |
| `cache_write_1h` | Extended-TTL cache creation; distinct price |
| `cache_storage_token_hours` | Gemini explicit caching is billed on storage time |
| `reasoning` | Recorded separately; folded into `output` at pricing per provider rule |
| `image_input` | Where billed per-image rather than per-token |
| `provisioned_unit_hours` | Bedrock provisioned throughput |

### ListPriceCatalog

Ships with Ziya as data (not inside `MODEL_CONFIGS` — prices change on a
different cadence and by different people than capability flags, and one
catalog entry serves several aliases). User-overridable by file.

```
entries[]:
  provider, model_id, region?          (region null = all regions)
  effective_from, effective_to?
  tier_factors   { standard: 1.0, priority: 1.75?, flex: 0.5, batch: 0.5 }  per provider
  unit_prices    { <dimension>: usd_per_million }
catalog_version  (content hash or date)
```

### RatePlan (plugin interface)

```
id, effective_from
apply(record, list_rates) -> EffectiveRates { unit_prices, provenance }
provenance: custom | default_rule | list | unknown
```

Shipped implementations:

| Plan | Behavior | Who |
|---|---|---|
| `PublicPlan` | multiplier 1.0, provenance `list` | default for everyone |
| `OverridePlan` | absolute unit rates per model | negotiated / EDP / committed-use |
| `ZeroPlan` | all dimensions 0 | Ollama and local endpoints |
| `DiscountTablePlan` | `list × (1 − discount[model])`, with fallback default rules | closed plugin (e.g. IMR: per-model discount, new Claude/OpenAI → 0.50, other new → 0.0, unlisted service → 0.70) |

Cache and batch dimensions inherit the model's discount (matches IMR policy).

### CostLine (derived, not stored long-term)

```
record_id
list_cost, effective_cost     (null if unknown)
catalog_version, plan_id, provenance
```

### Ledger

- `append(UsageRecord)` and `actualize(record_id, dims)`.
- `rollup(by=user|project|conversation|model|tier|day)` → totals with
  estimate/actual split and provenance breakdown.
- Cost is computed at rollup time from current catalog + plan. Rollups can pin
  a `catalog_version` if a caller needs reproducibility.

## Service tiers

Ziya is currently unaware of provider service tiers: no provider file sets
Bedrock `performanceConfig`/`serviceTier`, Anthropic `service_tier`, or uses
any batch API. Every request — interactive, delegate, task card, memory
extraction — bills at standard on-demand.

Sequencing:

1. ~~Record `tier` on every `UsageRecord`~~ — done; the meter records
   `get_task_service_tier() or "standard"`.
2. ~~Plumb `service_tier` through Bedrock~~ — done for Bedrock (`app/context.py`
   `_task_service_tier`, set by `block_executor`, read by the Bedrock
   providers). Anthropic-direct `service_tier` and Bedrock Mantle still
   pending.
3. ~~Default task-card runs to `flex`~~ — done (`ZIYA_TASK_SERVICE_TIER`,
   `scope.service_tier`). Delegates still run at standard.

Tier factors are per catalog entry (`tier_factors`), so a provider that
prices Flex differently per model is representable.

True batch (Anthropic Batch API, Bedrock batch inference) is a separate
execution mode — submit/poll/fetch, hours-scale, no streaming — not a flag on
the sync path. Out of scope here.

## Integration points

| Concern | Location |
|---|---|
| Meter → `UsageRecord` | `app/cost/meter.py`, called from `_stream_with_tools_impl` (`open_iteration` after `IterationUsage()`, `close_iteration` where `iteration_usages` is appended). The executor keeps `self.endpoint` / `self.model_id` / `self.region`; attribution comes from the `usage_attribution` ContextVar (`app/context.py`; set by `task_executor` and the CLI), the `is_delegate` flag, and `project_root` → project id via the project index |
| Catalog | `app/config/pricing.py` (`BUILTIN_CATALOG`, `~/.ziya/pricing.json` overlay, dated lookup) |
| RatePlan seam | `app/cost/rate_plans.py`; plugins register a `RatePlanProvider` (`app/plugins/interfaces.py`, `register_rate_plan_provider`) |
| Pricer | `app/cost/pricer.py` — `price_record(record, catalog, plan) -> CostLine` |
| Ledger storage | `app/storage/usage_ledger.py`, **global** `~/.ziya/usage.db` (SQLite, WAL). Not per project: the rollups the ledger exists for cross project boundaries; project is an attribution column |
| API | `app/routes/usage_routes.py` — `/api/usage/{summary,rollup,records,catalog}` |
| Display | Pending. Italic/bold per status; hover shows estimate/actual split, provenance, catalog version |

Deviations from the original draft, and why:

- **Partial lines.** A dimension with a count but no rate makes the line
  `is_partial` with `unpriced_dims` named, and the known part is summed. A
  null that hides 95% of a bill because one cache dimension is unlisted is
  less honest than a labelled floor.
- **`long_context`** was added to the catalog entry: Anthropic's 1M-context
  and Gemini's >200k step-ups are request-size tiers, not service tiers,
  and the whole request reprices when the threshold is crossed.
- **`reasoning` is not billed additively** unless an entry opts in with
  `reasoning_additive`: every provider Ziya ships reports reasoning inside
  `output`, so pricing it again double counts.
- **Cache writes** are all recorded as `cache_write_5m`; no provider path
  yet distinguishes the 1-hour TTL.
- **Pre-flight estimate** is `chars / 4`, not the calibrated estimator —
  the calibrated path runs once per turn, not per iteration, and the row
  is replaced by actuals in the normal case anyway.

## Non-goals

- Reconciling to a provider invoice to the cent. Rates revise monthly and
  retroactively; credits post quarterly; unlisted models carry placeholder
  discounts. Costs are labeled with the catalog version they used.
- Period locking / "what finance booked that month." If needed later it is a
  rollup-level snapshot, not a per-line state.

## Open items

- Confirm public tier factors per provider. Shipped: OpenAI priority 2.0 /
  flex 0.5 / batch 0.5; Anthropic and Google batch 0.5; Bedrock flex is NOT
  in the built-in Claude card (Claude rejects Flex on Bedrock as of
  2026-09-04) and belongs in `pricing.json` for the models that accept it.
- Public list prices for the newer model generations are unknown to the
  built-in table (listed in `Docs/CostAccounting.md`); revise the table.
- Decide catalog update path for public users (ship with release vs. fetch).
- Storage retention for `UsageRecord` — unbounded today; likely follows
  conversation lifecycle.
- Memory extraction does not run through `StreamingToolExecutor` and is not
  metered.
- UI: global Usage tab, per-conversation total, per-turn figure in the
  stream (needs a new SSE event).

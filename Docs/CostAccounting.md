# Cost Accounting

Ziya records every model call it makes — chat, Task Card runs, delegates,
the CLI — in a local ledger and prices it against a catalog of list prices,
a user override file, or a plugin-supplied rate plan. Design rationale:
[design/CostModel.md](../design/CostModel.md).

## What is recorded

One row per provider call in `~/.ziya/usage.db` (SQLite; `ZIYA_HOME`
honoured). Each row carries:

| Field | Meaning |
|---|---|
| `provider`, `model_id`, `region` | The billing endpoint (`bedrock`, `anthropic`, `openai`, `google`, `zai`, …), the **resolved** model id (never the alias), and region. The same weights cost different amounts on different endpoints. |
| `tier` | Service tier: `standard`, or `flex`/`priority` when a Task Card run requested one. |
| `status` | `estimate` — Ziya's pre-flight count, written when the request is dispatched. `actual` — replaced in place when the provider's usage block arrives. A call that never returns usage (throttle, abort) stays `estimate`. |
| `dims` | Token counts per dimension: `input`, `output`, `cache_read`, `cache_write_5m`, plus `reasoning` where reported. |
| attribution | `user`, `project_id`, `conversation_id`, `run_id` / `block_id` (Task Cards), `source` (`chat`, `task`, `delegate`, `cli`). |

**Tokens are the durable fact; cost is derived.** Rows are never re-priced
in place. Cost is computed when you ask for it, against the catalog entry
that was in force at the row's timestamp, and every figure is tagged with
the catalog version and plan that produced it.

## How a figure is displayed

| Rendering | Means |
|---|---|
| *italic* | at least one row in the total is an `estimate` |
| **bold** | every row is provider-reported (`actual`) |
| *partial* / `unpriced_dims` | some tokens had no rate; the figure is a floor, not a guess |
| `null` / unknown | no rate at all for this model — **never shown as $0** |

Zero is a real price (local models via Ollama) and is displayed as `$0.00`.

Provenance of the **rate** is independent of the estimate/actual status of
the **tokens**:

| Provenance | Rate came from |
|---|---|
| `list` | the built-in public catalog |
| `custom` | your `pricing.json` entry, an override plan, or a discount-table hit |
| `default_rule` | a discount plan's fallback rule (e.g. "new Claude models → 50%") |
| `unknown` | nothing matched |

## `~/.ziya/pricing.json`

Your own prices. Entries here win over the built-in catalog when both
match, so this is where internal transfer prices, negotiated rates, and
prices for models newer than the shipped table go. The file is re-read
when its modification time changes — no restart.

```json
{
  "revision": "internal-2026-09",
  "entries": [
    {
      "provider": "bedrock",
      "model_pattern": "*anthropic.claude-opus-4-7*",
      "region": null,
      "effective_from": "2026-06-01",
      "unit_prices": {
        "input": 5.0, "output": 25.0,
        "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10.0
      },
      "tier_factors": {"standard": 1.0, "flex": 0.5, "batch": 0.5},
      "long_context": {
        "above_input_tokens": 200000,
        "unit_prices": {"input": 10.0, "output": 37.5}
      }
    }
  ],
  "plan": {"type": "public"}
}
```

- `unit_prices` are **USD per million tokens**. Omit a dimension (or set it
  to `null`) to leave it unpriced rather than guess.
- `model_pattern` is a shell glob against the resolved model id. Bedrock ids
  carry `us.` / `eu.` / `global.` prefixes and `-v1:0` suffixes; `*…*`
  absorbs both.
- Entries are **dated**. To change a price, add a new entry with a later
  `effective_from` rather than editing the old one, so history still prices
  at what it cost then.
- `tier_factors` must list every tier you use; a row whose tier has no
  factor is unknown, not standard.

### `plan` — how list prices become your prices

| `type` | Behaviour |
|---|---|
| `public` | list price × 1.0 (default) |
| `zero` | everything costs 0; optional `"providers": ["local"]` scopes it (the built-in catalog already prices the `local` endpoint at zero) |
| `override` | `"rates": [{provider, model_pattern, unit_prices, tier_factors?}]` — absolute rates; unmatched models fall through to list |
| `discount_table` | `"discounts": {"provider/model-glob": 0.40}`, ordered `"default_rules": [{"pattern": "anthropic/claude-*", "discount": 0.5}]`, `"unlisted_discount": 0.7` — `list × (1 − d)`; cache and batch inherit the model's discount |

A closed-source plugin can supply the plan instead by registering a
`RatePlanProvider` (`app/plugins/interfaces.py`); a plugin plan takes
precedence over the file's `plan`, which takes precedence over `public`.
This is the seam for organisational transfer-pricing tables that should not
be published.

## Built-in catalog

`app/config/pricing.py::BUILTIN_CATALOG`, revision `CATALOG_REVISION`.
Public on-demand list prices for the Claude 3.x–4.5 family (Anthropic and
Bedrock), Amazon Nova, DeepSeek R1, gpt-oss, Llama 3.3/4 and Qwen3-Coder on
Bedrock, GPT‑4o/4.1/5 and o‑series on OpenAI, Gemini 2.0/2.5/3 Pro, GLM‑4.5/4.6,
and Ollama at zero.

**Deliberately unpriced** (no confident public figure at the time the table
was written — add them to `pricing.json`): Claude Sonnet 4.6 / 5, Opus 4.6–5,
Fable, Mythos; GPT‑5.4 / 5.5; Gemini 3.1 / 3.5; GLM‑4.7 / 5.2; DeepSeek v3.x;
Kimi K2.x; MiniMax M2.1; Qwen3‑Next; Nova 2; Meta Muse Spark. Nova cache-read
rates are also omitted, so Nova totals show as partial when caching is in
play.

Rate cards change; treat the built-in table as a starting point and the
catalog version stamped on every figure as the thing that tells you which
table produced it.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/usage/summary` | catalog version, active plan, ledger path and row count |
| `GET /api/usage/rollup?by=model` | grouped totals — `by` ∈ `user`, `project`, `conversation`, `run`, `model`, `provider`, `tier`, `source`, `day`, `month`, `status` |
| `GET /api/usage/records?conversation_id=…` | raw rows, each with its computed cost line |
| `GET /api/usage/catalog` | the effective catalog entries |

Rollup and records accept `since` / `until` (unix seconds or ISO date),
`project_id`, `conversation_id`, `run_id`, `provider`, `source`.

## Not (yet) covered

- Background memory extraction does not go through the metered executor and
  is not yet in the ledger.
- Per-turn cost in the live chat stream and a Usage tab in the UI are
  pending; the ledger and API are the substrate for both.
- Reconciling to a provider invoice to the cent is a non-goal: rates revise
  retroactively and credits post later. Figures are labelled with the
  catalog version they used.

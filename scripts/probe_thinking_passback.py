#!/usr/bin/env python3
"""Live probe for extended-thinking passback across the three Claude paths.

Answers the three questions no unit test can settle, because each depends on
what a real endpoint accepts rather than on what our code emits:

  Q1  Does the endpoint ACCEPT a round-tripped signed thinking block inside a
      tool chain?  (bedrock-direct, bedrock-mantle, anthropic-direct)
  Q2  Is a SIGNED-BUT-EMPTY thinking block accepted?  On display="omitted"
      models the stream carries only signature_delta, so the captured block
      has a signature and no text -- our capture emits it, and if the API
      rejects it the whole feature breaks on those models.
  Q3  Is the redacted_thinking payload field really named ``data`` on the
      Bedrock raw JSON?  (assumed for parity with the Anthropic block shape)

Method: two real iterations against a live model with one tool advertised.
Iteration 1 asks a question that forces a tool call, so the response carries
thinking + tool_use.  We capture the thinking blocks exactly as the provider
emits them, build the assistant turn WITH them via the provider's own
build_assistant_message, append a tool_result, and issue iteration 2.  If the
endpoint rejects round-tripped thinking, iteration 2 errors; if it accepts,
iteration 2 streams normally.

A control run repeats iteration 2 WITHOUT the thinking blocks, so an
unrelated failure (throttle, bad creds, model outage) cannot be misread as a
passback rejection: only "probe fails AND control passes" is evidence.

Usage:
    python3 scripts/probe_thinking_passback.py --path bedrock --model opus5
    python3 scripts/probe_thinking_passback.py --path mantle  --model fable5
    python3 scripts/probe_thinking_passback.py --path anthropic --model sonnet4
    python3 scripts/probe_thinking_passback.py --all

Nothing here mutates the repo.  Raw payload/response detail is written under
.ziya/probe_thinking_passback/ for inspection.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import the real providers -- the whole point is to exercise shipped code.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.providers.base import (  # noqa: E402
    ErrorEvent,
    ProviderConfig,
    StreamEnd,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseStart,
    UsageEvent,
)

OUT_DIR = Path(".ziya/probe_thinking_passback")

# The prompt must do two things at once: force a tool call (so there IS a tool
# chain to probe) and be hard enough that ADAPTIVE thinking actually engages.
# A trivially easy tool call produced thinking_tokens=0 on opus5 at effort
# medium -- adaptive thinking spends nothing when nothing needs deciding, so
# there were no blocks to round-trip and the probe was inconclusive. The task
# below requires planning which register to read and non-trivial arithmetic on
# the result, which is what buys real reasoning.
# Third framing. The second ("probe register bank", set bits, XOR) tripped a
# cyber-content refusal on opus5 -- stop_reason=refusal, zero content blocks --
# so the domain is now deliberately mundane (a warehouse stock count) while
# keeping the two properties the probe needs: the answer is unknowable without
# the tool, and reaching it requires planning plus multi-step arithmetic that
# adaptive thinking will actually spend tokens on.
# Fourth framing, arrived at by bisection against the live endpoint rather
# than by guessing. Two earlier prompts drew stop_reason="refusal" with
# category "cyber" and ZERO content blocks -- including a wholly mundane
# warehouse one, which ruled content out as the cause. Isolating further:
#
#   math prompt + tool schema + adaptive thinking  -> end_turn, 150ch thinking
#   warehouse prompt, plain phrasing               -> tool_use, 174ch thinking
#   warehouse prompt, "Facts:"/numbered-rules      -> refusal, 0 blocks
#
# So the trigger is the adversarial-sounding SHAPE (an enumerated rule list
# asserting what the model "cannot know" and constraining its order of
# operations), not the subject matter. Plain imperative phrasing keeps both
# properties the probe needs -- a forced tool call and real spent reasoning --
# without resembling a jailbreak scaffold.
# The prompt must put REASONING BEFORE THE TOOL CALL. Adaptive thinking spends
# tokens only on what needs deciding, so a prompt whose first act is an obvious
# tool call yields thinking_tokens=0 and no block to round-trip -- measured
# repeatedly on opus5 at effort high and max. Front-loading a genuine
# calculation the model must perform *before* it can call anything is what
# reliably produces a signed thinking block AND a tool_use in one response
# (measured: 311ch thinking + signature + read_shelf_count at effort=high).
#
# opus5 rejects mode="enabled" outright ('"thinking.type.enabled" is not
# supported for this model'), so effort is the only lever available here.
PROMPT = (
    "Before using any tool, decide which is cheaper to ship: 7 crates at 13.40 "
    "each with a 6% surcharge, or 5 crates at 18.10 each with a flat 4.00 fee. "
    "Explain the comparison. Then read the primary shelf count with the tool "
    "and say how many full pallets of 48 units it makes, how many loose units "
    "remain, and whether that leftover is even."
)

TOOL_SCHEMA_ANTHROPIC = [{
    "name": "read_shelf_count",
    "description": (
        "Returns today's counted quantity for one warehouse shelf. The count "
        "is not knowable without calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "shelf": {
                "type": "string",
                "description": "Shelf label: 'primary', 'overflow', or 'returns'.",
            },
        },
        "required": ["shelf"],
    },
}]

# 419 = 8 full pallets of 48 (384) with 35 loose, which is odd -- so every part
# of step 3 has a checkable answer and none of it is guessable in advance.
TOOL_RESULT_TEXT = "shelf 'primary' counted quantity = 419 units"


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------

def _resolve_model_id(raw: Any, region: str) -> str:
    """model_id may be a dict keyed by geo (us/eu/global)."""
    if isinstance(raw, dict):
        if region.startswith("eu") and "eu" in raw:
            return raw["eu"]
        return raw.get("us") or raw.get("global") or next(iter(raw.values()))
    return raw


def build_provider(path: str, model_name: str, profile: Optional[str],
                   region: Optional[str]) -> Tuple[Any, Dict[str, Any], str]:
    """Construct the real provider for one of the three Claude paths."""
    import app.config.models_config as mc

    if path in ("bedrock", "mantle"):
        cfgs = mc.MODEL_CONFIGS["bedrock"]
    elif path == "anthropic":
        cfgs = mc.MODEL_CONFIGS["anthropic"]
    else:
        raise SystemExit(f"unknown path {path!r}")

    if model_name not in cfgs:
        raise SystemExit(
            f"model {model_name!r} not found for path {path!r}. "
            f"Available: {sorted(k for k, v in cfgs.items() if isinstance(v, dict))}"
        )

    model_config = copy.deepcopy(cfgs[model_name])
    override = model_config.get("endpoint_override", "")

    if path == "mantle":
        if override != "bedrock-mantle":
            raise SystemExit(
                f"model {model_name!r} is not a mantle model "
                f"(endpoint_override={override!r})"
            )
        if model_config.get("mantle_api") == "openai-responses":
            raise SystemExit(
                f"model {model_name!r} uses the OpenAI Responses path on mantle, "
                "which does not share the Anthropic thinking-block format."
            )
        from app.providers.bedrock_mantle import (
            BedrockMantleProvider, resolve_mantle_region,
        )
        _region = resolve_mantle_region(model_config, region)
        model_id = _resolve_model_id(model_config["model_id"], _region)

        # The mantle account/region switch must be on provider_data_share or
        # every request 400s -- normally done at startup, which we bypass.
        try:
            from app.utils.aws_utils import ensure_mantle_data_retention_mode
            ok, err = ensure_mantle_data_retention_mode(
                required_mode="provider_data_share",
                region=_region, profile_name=profile,
            )
            if not ok:
                print(f"  [warn] retention mode not set: {err}")
        except Exception as exc:  # noqa: BLE001 -- diagnostic only
            print(f"  [warn] retention setup raised: {exc}")

        provider = BedrockMantleProvider(
            model_id=model_id, model_config=model_config,
            region=_region, aws_profile=profile,
        )
        return provider, model_config, _region

    if path == "bedrock":
        if override == "bedrock-mantle":
            raise SystemExit(
                f"model {model_name!r} is mantle-only; use --path mantle"
            )
        _region = region or os.environ.get("AWS_REGION") or "us-west-2"
        model_id = _resolve_model_id(model_config["model_id"], _region)
        from app.providers.bedrock import BedrockProvider
        provider = BedrockProvider(
            model_id=model_id, model_config=model_config,
            aws_profile=profile, region=_region,
        )
        return provider, model_config, _region

    # anthropic direct
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set -- cannot probe this path.")
    _region = "n/a"
    model_id = _resolve_model_id(model_config["model_id"], "us")
    from app.providers.anthropic_direct import AnthropicDirectProvider
    provider = AnthropicDirectProvider(
        model_id=model_id, model_config=model_config, api_key=api_key,
    )
    return provider, model_config, _region


def make_config(model_config: Dict[str, Any]) -> ProviderConfig:
    """ProviderConfig with thinking enabled the way the executor would."""
    from app.providers.base import ThinkingConfig

    if model_config.get("supports_adaptive_thinking"):
        # Deliberately push effort ABOVE the model default. At effort=medium
        # opus5 spent zero thinking tokens on a tool-forcing prompt, leaving
        # nothing to round-trip; the probe needs blocks to exist before it can
        # ask whether they are accepted. Clamped to what the model advertises.
        _supported = model_config.get("supported_efforts") or []
        _want = os.environ.get("PROBE_EFFORT", "high")
        _effort = _want if (not _supported or _want in _supported) else \
            model_config.get("thinking_effort_default", "medium")
        thinking = ThinkingConfig(enabled=True, mode="adaptive", effort=_effort)
    elif model_config.get("supports_thinking"):
        thinking = ThinkingConfig(enabled=True, mode="enabled", budget_tokens=4000)
    else:
        raise SystemExit("model does not support thinking -- nothing to probe")

    unsupported = set(model_config.get("unsupported_parameters", []) or [])
    return ProviderConfig(
        max_output_tokens=min(model_config.get("max_output_tokens", 8192), 8192),
        temperature=None if "temperature" in unsupported else 1.0,
        iteration=0,
        thinking=thinking,
    )


# ---------------------------------------------------------------------------
# Stream collection
# ---------------------------------------------------------------------------

class Collected:
    def __init__(self) -> None:
        self.text: str = ""
        self.thinking_text: str = ""
        self.thinking_blocks: List[Dict[str, Any]] = []
        self.raw_blocks: List[ThinkingBlock] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.stop_reason: Optional[str] = None
        self.usage_thinking_tokens: int = 0

    def summary(self) -> str:
        sigs = [
            (b.get("signature") or "")[:12] + "..." if b.get("signature") else "NONE"
            for b in self.thinking_blocks
        ]
        return (
            f"text={len(self.text)}ch thinking={len(self.thinking_text)}ch "
            f"blocks={len(self.thinking_blocks)} sigs={sigs} "
            f"tools={[t['name'] for t in self.tool_calls]} "
            f"stop={self.stop_reason} thinking_tokens={self.usage_thinking_tokens} "
            f"errors={len(self.errors)}"
        )


async def run_stream(provider, messages, system, tools, config,
                     label: str, timeout: float = 300.0) -> Collected:
    """Drive one real streaming request, mirroring the executor's handling."""
    got = Collected()
    started = time.time()
    pending: Dict[int, Dict[str, Any]] = {}

    async def _drive() -> None:
        async for ev in provider.stream_response(messages, system, tools, config):
            if isinstance(ev, TextDelta):
                got.text += ev.content
            elif isinstance(ev, ThinkingDelta):
                got.thinking_text += ev.content
            elif isinstance(ev, ThinkingBlock):
                got.raw_blocks.append(ev)
                if ev.block_type == "redacted_thinking":
                    got.thinking_blocks.append(
                        {"type": "redacted_thinking", "data": ev.data or ""})
                else:
                    got.thinking_blocks.append({
                        "type": "thinking",
                        "thinking": ev.content,
                        "signature": ev.signature,
                    })
            elif isinstance(ev, ToolUseStart):
                pending[ev.index] = {"id": ev.id, "name": ev.name}
            elif isinstance(ev, ToolUseEnd):
                got.tool_calls.append(
                    {"id": ev.id, "name": ev.name, "input": ev.input})
            elif isinstance(ev, UsageEvent):
                if ev.thinking_tokens:
                    got.usage_thinking_tokens = ev.thinking_tokens
            elif isinstance(ev, ErrorEvent):
                got.errors.append(f"{ev.error_type.name}: {ev.message}")
            elif isinstance(ev, StreamEnd):
                got.stop_reason = ev.stop_reason

    try:
        await asyncio.wait_for(_drive(), timeout=timeout)
    except asyncio.TimeoutError:
        got.errors.append(f"probe timeout after {timeout}s")
    except Exception as exc:  # noqa: BLE001 -- an exception IS the finding
        got.errors.append(f"{type(exc).__name__}: {exc}")

    print(f"  [{label}] {time.time() - started:.1f}s  {got.summary()}")
    for e in got.errors:
        print(f"      ERROR: {e[:400]}")
    return got


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

async def probe_path(path: str, model_name: str, profile: Optional[str],
                     region: Optional[str]) -> Dict[str, Any]:
    print(f"\n{'=' * 78}\nPATH: {path}  MODEL: {model_name}\n{'=' * 78}")
    result: Dict[str, Any] = {
        "path": path, "model": model_name,
        "q1_passback_accepted": None,
        "q2_signed_empty_block": None,
        "q3_redacted_field": None,
        "notes": [],
    }

    try:
        provider, model_config, resolved_region = build_provider(
            path, model_name, profile, region)
    except SystemExit as exc:
        result["notes"].append(f"setup refused: {exc}")
        print(f"  SKIP: {exc}")
        return result
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"setup failed: {type(exc).__name__}: {exc}")
        print(f"  SETUP FAILED: {exc}")
        return result

    print(f"  provider={type(provider).__name__} model_id={provider.model_id} "
          f"region={resolved_region}")
    print(f"  supports_feature('thinking_passback')="
          f"{provider.supports_feature('thinking_passback')}")
    config = make_config(model_config)

    # ---- Iteration 1: force a tool call so we get thinking + tool_use ----
    messages: List[Dict[str, Any]] = [{"role": "user", "content": PROMPT}]
    it1 = await run_stream(provider, messages, None, TOOL_SCHEMA_ANTHROPIC,
                           config, "iter1")

    if it1.errors and not it1.tool_calls:
        result["notes"].append(f"iteration 1 failed: {it1.errors[:1]}")
        return result
    if not it1.thinking_blocks:
        result["notes"].append(
            "no thinking blocks captured -- cannot probe passback. "
            f"thinking_text={len(it1.thinking_text)}ch "
            f"billed_thinking_tokens={it1.usage_thinking_tokens}")
        print("  INCONCLUSIVE: no thinking blocks were captured.")
        return result
    if not it1.tool_calls:
        result["notes"].append("model did not call the tool; no tool chain to probe")
        return result

    # ---- Q2 / Q3: inspect what we actually captured ----
    readable = [b for b in it1.thinking_blocks if b["type"] == "thinking"]
    redacted = [b for b in it1.thinking_blocks if b["type"] == "redacted_thinking"]
    empty_signed = [b for b in readable if b.get("signature") and not b.get("thinking")]

    if empty_signed:
        result["q2_signed_empty_block"] = "present_in_capture"
        print(f"  Q2: {len(empty_signed)} signed-but-EMPTY block(s) captured "
              "-- acceptance decided by iteration 2 below")
    else:
        result["q2_signed_empty_block"] = "not_produced"
        print("  Q2: no signed-but-empty blocks produced by this model/config "
              "(all signed blocks carry text)")

    if redacted:
        result["q3_redacted_field"] = (
            "data_populated" if any(b.get("data") for b in redacted)
            else "data_EMPTY_field_name_likely_wrong")
        print(f"  Q3: {len(redacted)} redacted block(s); "
              f"data populated={[bool(b.get('data')) for b in redacted]}")
    else:
        result["q3_redacted_field"] = "no_redacted_blocks_seen"
        print("  Q3: no redacted_thinking blocks in this response (expected; "
              "they are rare) -- field name remains unverified")

    # ---- Build iteration 2 conversation, WITH the thinking blocks ----
    tool_uses = [{"id": tc["id"], "name": tc["name"], "input": tc["input"]}
                 for tc in it1.tool_calls]
    assistant_with = provider.build_assistant_message(
        it1.text, tool_uses, thinking_blocks=it1.thinking_blocks)
    assistant_without = provider.build_assistant_message(it1.text, tool_uses)
    tool_result_msg = provider.build_tool_result_message([
        {"tool_use_id": tc["id"], "content": TOOL_RESULT_TEXT}
        for tc in it1.tool_calls
    ])

    print(f"  block order WITH passback:    "
          f"{[b.get('type') for b in assistant_with['content']]}")
    print(f"  block order WITHOUT passback: "
          f"{[b.get('type') for b in assistant_without['content']]}")

    config2 = make_config(model_config)
    config2.iteration = 1

    convo_with = [messages[0], assistant_with, tool_result_msg]
    it2 = await run_stream(provider, convo_with, None, TOOL_SCHEMA_ANTHROPIC,
                           config2, "iter2-WITH-passback")

    probe_failed = bool(it2.errors) or (
        not it2.text.strip() and not it2.tool_calls)

    # ---- Control: same turn WITHOUT thinking, to isolate the cause ----
    convo_without = [messages[0], assistant_without, tool_result_msg]
    ctl = await run_stream(provider, convo_without, None, TOOL_SCHEMA_ANTHROPIC,
                           config2, "iter2-CONTROL-no-passback")
    control_failed = bool(ctl.errors) or (
        not ctl.text.strip() and not ctl.tool_calls)

    if not probe_failed:
        result["q1_passback_accepted"] = True
        print("  Q1: ACCEPTED -- round-tripped signed thinking blocks were "
              "accepted by this endpoint")
        if empty_signed:
            result["q2_signed_empty_block"] = "accepted"
            print("  Q2: signed-but-empty block(s) were part of the accepted "
                  "payload -> accepted")
    elif control_failed:
        result["q1_passback_accepted"] = "inconclusive"
        result["notes"].append(
            "both probe and control failed -- unrelated fault (throttle/creds/"
            f"outage), not a passback rejection. probe={it2.errors[:1]} "
            f"control={ctl.errors[:1]}")
        print("  Q1: INCONCLUSIVE -- control also failed, so the failure is "
              "not attributable to passback")
    else:
        result["q1_passback_accepted"] = False
        result["notes"].append(f"passback REJECTED: {it2.errors[:2]}")
        print("  Q1: REJECTED -- probe failed while control passed. "
              "Passback is not accepted on this path.")
        if empty_signed:
            result["q2_signed_empty_block"] = "possible_cause_of_rejection"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dump = OUT_DIR / f"{path}_{model_name}.json"
    dump.write_text(json.dumps({
        "path": path, "model": model_name, "model_id": provider.model_id,
        "region": resolved_region,
        "provider_class": type(provider).__name__,
        "iter1": {
            "thinking_chars": len(it1.thinking_text),
            "blocks": [
                {"type": b["type"],
                 "thinking_len": len(b.get("thinking", "")),
                 "has_signature": bool(b.get("signature")),
                 "signature_len": len(b.get("signature") or ""),
                 "data_len": len(b.get("data") or "")}
                for b in it1.thinking_blocks
            ],
            "tool_calls": it1.tool_calls,
            "stop_reason": it1.stop_reason,
            "billed_thinking_tokens": it1.usage_thinking_tokens,
        },
        "assistant_block_order_with": [b.get("type") for b in assistant_with["content"]],
        "assistant_block_order_without": [b.get("type") for b in assistant_without["content"]],
        "iter2_with_passback": {
            "text_len": len(it2.text), "errors": it2.errors,
            "stop_reason": it2.stop_reason,
            "thinking_chars": len(it2.thinking_text),
            "thinking_blocks": len(it2.thinking_blocks),
        },
        "iter2_control": {
            "text_len": len(ctl.text), "errors": ctl.errors,
            "stop_reason": ctl.stop_reason,
            "thinking_chars": len(ctl.thinking_text),
            "thinking_blocks": len(ctl.thinking_blocks),
        },
        "verdict": result,
    }, indent=2, default=str))
    print(f"  detail -> {dump}")

    # Continuity signal: with reasoning restored, iteration 2 should not need
    # to re-derive as much thinking as iteration 1 spent.
    if not probe_failed and it1.thinking_text and it2.thinking_text:
        ratio = len(it2.thinking_text) / max(len(it1.thinking_text), 1)
        print(f"  continuity: iter2 thinking is {ratio:.0%} of iter1 "
              f"({len(it2.thinking_text)}ch vs {len(it1.thinking_text)}ch); "
              f"control iter2={len(ctl.thinking_text)}ch")
        result["notes"].append(
            f"iter2_thinking={len(it2.thinking_text)}ch "
            f"control_thinking={len(ctl.thinking_text)}ch "
            f"iter1_thinking={len(it1.thinking_text)}ch")

    return result


def load_dotenv_key(name: str, path: str = "~/.env") -> bool:
    """Populate os.environ[name] from a dotenv file if it is not already set.

    Reads the value in-process rather than exporting it on a command line, so
    the secret never appears in shell history, a process listing, or this
    script's own output.  Returns True if the variable ended up set.
    """
    if os.environ.get(name):
        return True
    p = os.path.expanduser(path)
    if not os.path.isfile(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() != name:
                    continue
                v = v.strip().strip("'\"")
                if v:
                    os.environ[name] = v
                    return True
    except OSError as exc:  # noqa: BLE001 -- diagnostic only
        print(f"  [warn] could not read {path}: {exc}")
    return False


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", choices=["bedrock", "mantle", "anthropic"])
    ap.add_argument("--model")
    ap.add_argument("--profile", default=os.environ.get("ZIYA_AWS_PROFILE"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION"))
    ap.add_argument("--all", action="store_true",
                    help="probe bedrock/opus5, mantle/fable5, anthropic (if keyed)")
    ap.add_argument("--env-file", default="~/.env",
                    help="dotenv file to source ANTHROPIC_API_KEY from when unset")
    args = ap.parse_args()

    # The anthropic-direct path needs a key that is normally only in ~/.env.
    _keyed = load_dotenv_key("ANTHROPIC_API_KEY", args.env_file)

    targets: List[Tuple[str, str]] = []
    if args.all:
        targets = [("bedrock", "opus5"), ("mantle", "fable5")]
        if _keyed:
            targets.append(("anthropic", "claude-opus-5"))
        else:
            print("[note] ANTHROPIC_API_KEY unset -- skipping anthropic-direct")
    else:
        if not args.path or not args.model:
            ap.error("--path and --model are required unless --all is given")
        targets = [(args.path, args.model)]

    results = []
    for path, model in targets:
        results.append(await probe_path(path, model, args.profile, args.region))

    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    for r in results:
        print(f"{r['path']:>10} / {r['model']:<10} "
              f"Q1_passback={r['q1_passback_accepted']}  "
              f"Q2_signed_empty={r['q2_signed_empty_block']}  "
              f"Q3_redacted={r['q3_redacted_field']}")
        for n in r["notes"]:
            print(f"             note: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

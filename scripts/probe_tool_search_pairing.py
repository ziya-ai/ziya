#!/usr/bin/env python3
"""Live probe: which replayed shapes of a tool-search turn does the Bedrock
Invoke API accept?

Written for the 2026-11-16 report (conversation 27b0c90a): the fourth
request of a turn was rejected with ``messages.25: `tool_search_tool_regex`
tool use with id … was found without a corresponding
`tool_search_tool_result` block`` although the same assistant message had
been accepted in the two requests before it.

Each case sends one small request whose history holds a search turn
(``server_tool_use`` + ``tool_search_tool_result``), replayed the way
BedrockProvider replays it, and records accept / the error text.  The search
blocks come from a real first call, so their ids and references are genuine.

Rows go to .ziya/probe_native_tool_search/pairing-*.jsonl.  Makes live calls.
"""
from __future__ import annotations
import argparse, copy, json, os, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402
from app.utils import mcp_ondemand as od  # noqa: E402

FILLER = ("Reference notes for the assistant. " * 40 + "\n") * 40   # ~8K tokens: cacheable

TOOLS = [
    dict(od.TOOL_SEARCH_ENTRY),
    {"name": "mcp_file_read", "description": "Read a file from the project.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "mcp_ReadInternalWebsites", "description": "Read internal websites such as code reviews.",
     "input_schema": {"type": "object", "properties": {"inputs": {"type": "array", "items": {"type": "string"}}},
                      "required": ["inputs"]}, "defer_loading": True},
    {"name": "mcp_pippin_get_artifact", "description": "Fetch a Pippin design document by URL.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
     "defer_loading": True},
]


def body_for(messages, max_tokens=300):
    return {"anthropic_version": "bedrock-2023-05-31", "max_tokens": max_tokens,
            "system": [{"type": "text", "text": "You are a test assistant.\n" + FILLER,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": messages, "tools": TOOLS, "tool_choice": {"type": "auto"}}


def invoke(client, model_id, body):
    t0 = time.time()
    try:
        r = client.invoke_model(modelId=model_id, body=json.dumps(body))
        out = json.loads(r["body"].read())
        return True, out, round(time.time() - t0, 2)
    except Exception as e:  # noqa: BLE001 -- the error text is the result
        return False, str(e), round(time.time() - t0, 2)


def stamped(messages, at):
    """BedrockProvider.prepare_cache_control's marker, at message index ``at``
    (negative counts from the end), on the last non-thinking block."""
    m = copy.deepcopy(messages)
    if at is None:
        return m
    msg = m[at]
    if isinstance(msg["content"], str):
        msg["content"] = [{"type": "text", "text": msg["content"], "cache_control": {"type": "ephemeral"}}]
    else:
        for b in reversed(msg["content"]):
            if b.get("type") not in ("thinking", "redacted_thinking"):
                b["cache_control"] = {"type": "ephemeral"}
                break
    return m


def tr(tid, text="ok"):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


def tu(tid, name="mcp_file_read", inp=None):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {"path": "a.py"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("ZIYA_AWS_PROFILE"))
    ap.add_argument("--model-id", default="global.anthropic.claude-fable-5-2")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--repeat", type=int, default=1, help="send each case this many times")
    a = ap.parse_args()
    client = boto3.Session(profile_name=a.profile).client(
        "bedrock-runtime", region_name=a.region, config=Config(read_timeout=300, retries={"max_attempts": 1}))
    out_dir = Path(".ziya/probe_native_tool_search"); out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pairing-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl"

    def rec(**kw):
        with open(path, "a") as f:
            f.write(json.dumps(kw) + "\n")
        print(json.dumps(kw)[:600])

    # -- a genuine search turn -------------------------------------------------
    user0 = {"role": "user", "content": "Use tool search to find the tool that reads internal "
             "websites, then read https://code.example.com/reviews/CR-1 with it."}
    ok, out, lat = invoke(client, a.model_id, body_for([user0], max_tokens=600))
    if not ok:
        rec(case="seed", ok=False, error=out)
        return 1
    seed = out["content"]
    types = [b["type"] for b in seed]
    rec(case="seed", ok=True, blocks=types, stop=out.get("stop_reason"), latency_s=lat)
    srv = next((b for b in seed if b["type"] == "server_tool_use"), None)
    res = next((b for b in seed if b["type"] == "tool_search_tool_result"), None)
    if not srv or not res:
        rec(case="seed", ok=False, error="model did not search; nothing to replay")
        return 1
    found = [b for b in seed if b["type"] == "tool_use"]
    found_use = found[0] if found else tu("toolu_riw", "mcp_ReadInternalWebsites",
                                          {"inputs": ["https://code.example.com/reviews/CR-1"]})

    def later(n_iters, a0, a0_results):
        """user, A0(search turn), R0, then n_iters plain tool iterations; the
        last one calls the found (deferred) tool, as iteration 2 did."""
        msgs = [user0, {"role": "assistant", "content": a0},
                {"role": "user", "content": a0_results}]
        for i in range(n_iters):
            last = i == n_iters - 1
            use = (tu(f"toolu_l{i}", "mcp_ReadInternalWebsites",
                      {"inputs": ["https://code.example.com/reviews/CR-2"]}) if last
                   else tu(f"toolu_l{i}"))
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"Step {i}."}, use]})
            content = [tr(f"toolu_l{i}")]
            if last:
                content.append({"type": "text", "text": "[User feedback]: read it one more time"})
            msgs.append({"role": "user", "content": content})
        return msgs

    text = {"type": "text", "text": "Looking."}
    shapes = {
        # as the API returned it
        "verbatim": (seed, [tr(b["id"]) for b in seed if b["type"] == "tool_use"]),
        # the search between two client calls (the reported turn's likely shape)
        "between_tools": ([text, tu("toolu_a"), srv, res, tu("toolu_b")], [tr("toolu_a"), tr("toolu_b")]),
        "after_tools": ([text, tu("toolu_a"), srv, res], [tr("toolu_a")]),
        # controls: these must fail, and show what the error looks like
        "orphan_use": ([text, srv, tu("toolu_a")], [tr("toolu_a")]),
        "result_not_adjacent": ([srv, text, res, tu("toolu_a")], [tr("toolu_a")]),
    }
    for name, (a0, r0) in shapes.items():
        for n_iters in (1, 2, 3):
            msgs = later(n_iters, a0, r0)
            # BedrockProvider: marker 4 messages from the end once there are >= 6.
            at = (len(msgs) - 4) if len(msgs) >= 6 else None
            for rep in range(a.repeat):
                ok, out, lat = invoke(client, a.model_id, body_for(stamped(msgs, at), max_tokens=60))
                rec(case=name, iters_after=n_iters, n_messages=len(msgs), marker_at=at, rep=rep, ok=ok,
                    error=None if ok else out[:400],
                    usage=out.get("usage") if ok else None, latency_s=lat)
    # The marker ON the search blocks themselves.
    for name, idx in (("marker_on_result", 2), ("marker_on_use", 1)):
        a0 = copy.deepcopy([text, srv, res, tu("toolu_a")])
        a0[idx]["cache_control"] = {"type": "ephemeral"}
        msgs = later(2, a0, [tr("toolu_a")])
        ok, out, lat = invoke(client, a.model_id, body_for(msgs, max_tokens=60))
        rec(case=name, ok=ok, error=None if ok else out[:400], usage=out.get("usage") if ok else None)
    print("rows:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

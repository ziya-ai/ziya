#!/usr/bin/env python3
"""Live probe: does bedrock-runtime (Invoke API) accept deferred tools and the
tool-search entry for a Claude model, and under which beta name?

Phase 1 tries a small request (two deferred tools) with each candidate
``anthropic_beta`` value.  Phase 2 runs the six-turn conversation of
probe_native_tool_search.py (three first-use turns, three reuse turns) with
the first accepted variant, non-streaming, echoing the response content
verbatim.  Phase 3 makes one streaming call and records the raw event shapes
of the search blocks (what BedrockProvider's parser would have to handle).

Rows go to .ziya/probe_native_tool_search/invoke-*.jsonl.  Makes live calls.
"""
from __future__ import annotations
import argparse, json, os, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402
import probe_native_tool_search as pn  # noqa: E402
from app.utils import mcp_ondemand as od  # noqa: E402

BETAS = [None, "tool-search-tool-2025-10-19", "advanced-tool-use-2025-11-20"]


def body_for(messages, system, tools, beta, max_tokens=1500):
    b = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": max_tokens,
         "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
         "messages": messages, "tools": tools}
    if beta:
        b["anthropic_beta"] = [beta]
    return b


def invoke(client, model_id, body):
    t0 = time.time()
    r = client.invoke_model(modelId=model_id, body=json.dumps(body))
    out = json.loads(r["body"].read())
    return out, round(time.time() - t0, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("ZIYA_AWS_PROFILE"))
    ap.add_argument("--model-id", default="us.anthropic.claude-fable-5-2")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--turns", type=int, default=len(pn.TURNS))
    ap.add_argument("--tools-json", default=None)
    ap.add_argument("--conv-cache", default="none", choices=["none", "bedrock", "anthropic"],
                    help="conversation cache breakpoint: the shipped BedrockProvider's "
                         "(4 messages from the end, >=6 messages) or AnthropicDirectProvider's (messages[-2])")
    ap.add_argument("--arms", default="native,eager")
    a = ap.parse_args()
    client = boto3.Session(profile_name=a.profile).client(
        "bedrock-runtime", region_name=a.region, config=Config(read_timeout=300, retries={"max_attempts": 2}))
    out_dir = Path(".ziya/probe_native_tool_search"); out_dir.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:8]
    path = out_dir / f"invoke-{time.strftime('%Y%m%d-%H%M%S')}-{nonce}.jsonl"
    out = path.open("w")
    marker = None
    if a.conv_cache != "none":
        from app.providers.anthropic_direct import AnthropicDirectProvider
        from app.providers.bedrock import BedrockProvider
        cls = BedrockProvider if a.conv_cache == "bedrock" else AnthropicDirectProvider
        marker = cls.__new__(cls)          # prepare_cache_control uses no instance state

    def marked(messages):
        return marker.prepare_cache_control(messages, 0) if marker else messages

    def rec(**kw):
        out.write(json.dumps(kw, default=str) + "\n"); out.flush()

    # Phase 1: which variant is accepted
    small = [dict(od.TOOL_SEARCH_ENTRY),
             {"name": "mcp_get_jira_issue", "description": "Get a Jira issue by key",
              "input_schema": {"type": "object", "properties": {"issue_key": {"type": "string"}}, "required": ["issue_key"]},
              "defer_loading": True},
             {"name": "mcp_get_weather", "description": "Weather for a city",
              "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}, "defer_loading": True}]
    accepted = "none"
    for beta in BETAS:
        try:
            r, dt = invoke(client, a.model_id, body_for(
                [{"role": "user", "content": "Look up Jira issue RAD-3140."}], "Use tools.", small, beta, 600))
            types = [b.get("type") for b in r.get("content", [])]
            print(f"phase1 beta={beta}: OK {dt}s stop={r.get('stop_reason')} blocks={types}")
            rec(phase=1, beta=beta, ok=True, latency_s=dt, blocks=types, usage=r.get("usage"))
            if accepted == "none" and "server_tool_use" in types:
                accepted = beta
        except Exception as e:  # noqa: BLE001
            print(f"phase1 beta={beta}: FAIL {str(e)[:300]}")
            rec(phase=1, beta=beta, ok=False, error=str(e)[:600])
    if accepted == "none":
        print("no variant produced a search; stopping"); print(f"wrote {path}"); return 1
    print(f"using beta={accepted}")

    # Phase 2: the conversation, native vs eager
    tools = pn.fetch_tools(os.environ.get("ZIYA_PORT", "6969"), a.tools_json)
    reach = [s for s in pn.DEFAULT_REACH.split(",") if s]
    for arm_name in [x for x in a.arms.split(",") if x]:
        arm = pn.Arm(arm_name, tools, reach)
        system = f"Probe run {nonce}/{arm_name}.\n{pn.FILLER}{arm.system_extra}"
        messages = []
        for ti, user in enumerate(pn.TURNS[: a.turns], 1):
            messages.append({"role": "user", "content": user})
            for it in range(1, 9):
                try:
                    r, dt = invoke(client, a.model_id, body_for(
                        marked(messages), system, arm.tools(), accepted if arm_name == "native" else None))
                except Exception as e:  # noqa: BLE001
                    print(f"  {arm_name} t{ti}.{it} ERROR {str(e)[:400]}")
                    rec(phase=2, conv_cache=a.conv_cache, arm=arm_name, turn=ti, iteration=it, error=str(e)[:800]); break
                u = r.get("usage", {})
                content = r.get("content", [])
                types = [b.get("type") for b in content]
                searches = [b.get("input") for b in content if b.get("type") == "server_tool_use"]
                calls = [b["name"] for b in content if b.get("type") == "tool_use"]
                rec(phase=2, conv_cache=a.conv_cache, arm=arm_name, turn=ti, iteration=it, latency_s=dt,
                    input=u.get("input_tokens", 0), output=u.get("output_tokens", 0),
                    cache_read=u.get("cache_read_input_tokens", 0), cache_write=u.get("cache_creation_input_tokens", 0),
                    stop=r.get("stop_reason"), blocks=types, searches=len(searches), search_inputs=searches,
                    calls=calls, usage=u, error=None)
                print(f"  {arm_name} t{ti}.{it} in={u.get('input_tokens')} read={u.get('cache_read_input_tokens')} "
                      f"write={u.get('cache_creation_input_tokens')} out={u.get('output_tokens')} {dt}s "
                      f"stop={r.get('stop_reason')} searches={searches} calls={calls}", flush=True)
                messages.append({"role": "assistant", "content": content})
                tus = [b for b in content if b.get("type") == "tool_use"]
                if not tus:
                    break
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b["id"], "content": arm.run_tool(b["name"], b.get("input") or {})}
                    for b in tus]})
            else:
                continue

    # Phase 3: raw stream event shapes
    try:
        b = body_for([{"role": "user", "content": "Look up Jira issue RAD-3140."}], "Use tools.", small, accepted, 600)
        resp = client.invoke_model_with_response_stream(modelId=a.model_id, body=json.dumps(b))
        for ev in resp["body"]:
            ch = json.loads(ev["chunk"]["bytes"])
            t = ch.get("type")
            if t == "content_block_start":
                cb = ch.get("content_block", {})
                print(f"stream start idx={ch.get('index')} type={cb.get('type')} keys={sorted(cb)}")
                rec(phase=3, event=t, index=ch.get("index"), block=cb)
            elif t == "content_block_delta":
                d = ch.get("delta", {})
                rec(phase=3, event=t, index=ch.get("index"), delta_type=d.get("type"), delta=d)
            elif t in ("content_block_stop", "message_delta", "message_start"):
                rec(phase=3, event=t, index=ch.get("index"), raw=ch)
    except Exception as e:  # noqa: BLE001
        print(f"phase3 FAIL {str(e)[:400]}"); rec(phase=3, error=str(e)[:800])
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

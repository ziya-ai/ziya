#!/usr/bin/env python3
"""Live probe: does native tool search (deferred MCP tools) work through the
shipped provider, and what does it cost against the two alternatives?

Three arms run the SAME three-turn conversation against a live model with the
user's real MCP tool schemas (fetched from the running server; nothing is
executed -- every tool call gets a canned result):

  eager    every within-reach tool is in the tool list (what "On hand" costs)
  loader   Ziya's own path: catalog + search_server_tools / load_server_tools;
           a load appends tools to the array, which heads the cache prefix
  native   the tools array from ``native_tool_payload``: search entry first,
           within-reach tools last with defer_loading

Each request's usage (input, cache read, cache write, output), latency, stop
reason, tool calls and passback block types are appended to a JSONL file
under .ziya/probe_native_tool_search/.  The assistant turn is assembled the
way the executor does it (thinking + passback blocks with their original
index, through the provider's build_assistant_message), so a continuation
that the API rejects shows up as an error row for that arm.

Usage:
    python3 scripts/probe_native_tool_search.py --profile kuiper-ziya
    python3 scripts/probe_native_tool_search.py --arms native --turns 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.providers.base import (  # noqa: E402
    ErrorEvent, PassbackBlock, ProviderConfig, StreamEnd, TextDelta,
    ThinkingBlock, ThinkingConfig, ToolUseEnd, UsageEvent,
)
from app.utils import mcp_ondemand as od  # noqa: E402

DEFAULT_REACH = ("slack-mcp,pippin_mcp_server,quip_mcp,kuiperconfluencemcp,"
                 "kuiper-jira-tool,chorus_mcp")

TURNS = [
    "Look up Jira issue RAD-3140 and tell me its summary and status. One sentence.",
    "Add the comment 'probe ok' to that issue, then confirm in one sentence.",
    "Search Slack for messages about 'deploy freeze' and tell me how many matches came back.",
    # Reuse turns: tools the conversation already found/loaded (post-search cost).
    "Look up Jira issue RAD-3141 and tell me its status. One sentence.",
    "Search Slack for messages about 'rollback' and tell me how many matches came back.",
    "Add the comment 'second probe ok' to RAD-3141, then confirm in one sentence.",
]

CANNED = {
    "get_jira_issue": {"key": "RAD-3140", "summary": "Retune feeder link margin", "status": "In Progress"},
    "add_jira_comment": {"ok": True, "comment_id": "10042"},
    "search": {"matches": 3, "page": 1, "pages": 1, "messages": [{"text": "deploy freeze starts Friday"}] * 3},
}

# Stable filler so the system block clears the minimum cacheable size the
# same way in every arm.
FILLER = "\n".join(
    f"Guideline {i}: answer briefly, use tools when a request names an external system, "
    f"and never invent a tool result." for i in range(220))


def fetch_tools(port: str, path: Optional[str]) -> List[Dict[str, Any]]:
    if path:
        return json.loads(Path(path).read_text())["tools"]
    with urllib.request.urlopen(f"http://localhost:{port}/api/mcp/tools", timeout=30) as r:
        return json.loads(r.read())["tools"]


def to_schema(t: Dict[str, Any]) -> Dict[str, Any]:
    name = t["name"] if t["name"].startswith("mcp_") else f"mcp_{t['name']}"
    schema = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
    # The API rejects a top-level oneOf/allOf/anyOf (also on a deferred tool).
    for k in ("oneOf", "allOf", "anyOf"):
        schema.pop(k, None)
    schema.setdefault("type", "object")
    return {"name": name, "description": t.get("description") or "", "input_schema": schema}


def builtin_loader_schemas() -> List[Dict[str, Any]]:
    from app.mcp.tools.mcp_loader_tools import LoadServerToolsTool, SearchServerToolsTool
    out = []
    for cls in (SearchServerToolsTool, LoadServerToolsTool):
        tool = cls()
        out.append({"name": tool.name, "description": tool.description,
                    "input_schema": tool.InputSchema.model_json_schema()})
    return out


def catalog(reach: List[str], native: bool) -> str:
    rows = "\n".join(f"  • {n}" for n in sorted(reach))
    if native:
        return ("\n\n## MCP servers within reach\n\n"
                "These MCP servers are connected, but their tool definitions are deferred:\n"
                f"they are not in your context until you find them with `{od.TOOL_SEARCH_NAME}`\n"
                "(a regex over tool names, descriptions and argument names; tool names\n"
                "start with `mcp_`).  A found tool is callable at once and stays available\n"
                "for the rest of the conversation; searching costs nothing else.\n\n" + rows + "\n")
    return ("\n\n## MCP servers within reach\n\n"
            "These MCP servers are connected, but their tools are not in your tool\n"
            f"list.  `{od.SEARCH_TOOL}` finds the tools a request needs (it loads\n"
            f"nothing and is free to call); `{od.LOADER_TOOL}` makes them callable from\n"
            "your next step and for the rest of the conversation.\n\n" + rows
            + f"\n\nEach `{od.LOADER_TOOL}` call re-sends the tool list, so load every tool the\n"
            "task will need in ONE call: name the tools for a large server, or omit\n"
            "`tools` to load a small one whole.\n")


class Arm:
    def __init__(self, name: str, tools: List[Dict[str, Any]], reach: List[str]):
        self.name = name
        self.reach = reach
        self.by_server: Dict[str, List[Dict[str, Any]]] = {}
        for t in tools:
            self.by_server.setdefault(t["server"], []).append(t)
        hand = [to_schema(t) for t in tools if t["server"] not in reach]
        within = [(t["server"], to_schema(t)) for t in tools if t["server"] in reach]
        self.loaded: List[Dict[str, Any]] = []
        if name == "eager":
            self.base = hand + [s for _, s in within]
            self.system_extra = ""
        elif name == "loader":
            self.base = builtin_loader_schemas() + hand
            self.system_extra = catalog(reach, native=False)
        else:
            pairs = [(None, s) for s in hand] + [(srv, s) for srv, s in within]
            self.base = od.native_tool_payload(pairs, lambda srv: srv, reach)
            self.system_extra = catalog(reach, native=True)

    def tools(self) -> List[Dict[str, Any]]:
        return self.base + self.loaded

    def run_tool(self, name: str, args: Dict[str, Any]) -> str:
        bare = name[4:] if name.startswith("mcp_") else name
        if bare == od.SEARCH_TOOL:
            servers = [args["server"]] if args.get("server") in self.reach else self.reach
            pool = [(s, t["name"], t.get("description") or "") for s in servers for t in self.by_server.get(s, [])]
            rows = od.rank_tools(args.get("query") or "", pool, 10) or [(s, n, d, 0) for s, n, d in pool[:30]]
            return "\n".join(f"{s}: {n} — {(d or '').splitlines()[0][:120] if d else ''}" for s, n, d, _ in rows) \
                + "\nNothing loaded. Call load_server_tools with the server and tool names."
        if bare == od.LOADER_TOOL:
            server = args.get("name") or ""
            if server not in self.reach:
                return f"'{server}' is not within reach. Within reach: {', '.join(self.reach)}."
            want = {(n[4:] if n.startswith("mcp_") else n) for n in (args.get("tools") or [])}
            have = {s["name"] for s in self.tools()}
            added = []
            for t in self.by_server.get(server, []):
                if want and t["name"] not in want:
                    continue
                s = to_schema(t)
                if s["name"] not in have:
                    self.loaded.append(s)
                    added.append(s["name"])
            return f"[MCP server loaded: {server}]\n" + "\n".join(f"- {n}" for n in added)
        return json.dumps(CANNED.get(bare, {"ok": True}))


async def one_request(provider, messages, system, tools) -> Dict[str, Any]:
    cfg = ProviderConfig(max_output_tokens=2000, temperature=None,
                         thinking=ThinkingConfig(enabled=True, mode="adaptive", effort="low"),
                         model_config=provider.model_config)
    row: Dict[str, Any] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                           "stop": None, "error": None, "passback": [], "calls": []}
    text: Dict[int, str] = {}
    blocks: List[Dict[str, Any]] = []
    tool_uses: List[Dict[str, Any]] = []
    t0 = time.time()
    async for ev in provider.stream_response(messages, system, tools, cfg):
        if isinstance(ev, UsageEvent):
            row["input"] += ev.input_tokens
            row["output"] += ev.output_tokens
            row["cache_read"] += ev.cache_read_tokens
            row["cache_write"] += ev.cache_write_tokens
        elif isinstance(ev, TextDelta):
            text[ev.index] = text.get(ev.index, "") + ev.content
        elif isinstance(ev, PassbackBlock):
            blocks.append({**ev.block, "_index": ev.index})
            row["passback"].append(ev.block.get("type"))
            if ev.block.get("type") == "server_tool_use":
                row["calls"].append(f"<search {json.dumps(ev.block.get('input'))}>")
        elif isinstance(ev, ThinkingBlock):
            blocks.append({"type": "redacted_thinking", "data": ev.data or "", "_index": ev.index}
                          if ev.block_type == "redacted_thinking"
                          else {"type": "thinking", "thinking": ev.content,
                                "signature": ev.signature, "_index": ev.index})
        elif isinstance(ev, ToolUseEnd):
            tool_uses.append({"id": ev.id, "name": ev.name, "input": ev.input, "index": ev.index})
            row["calls"].append(ev.name)
        elif isinstance(ev, StreamEnd):
            row["stop"] = ev.stop_reason
        elif isinstance(ev, ErrorEvent):
            row["error"] = ev.message[:600]
    row["latency_s"] = round(time.time() - t0, 2)
    segs = sorted(text.items())
    row["_assistant"] = provider.build_assistant_message(
        "".join(t for _, t in segs), tool_uses, thinking_blocks=blocks or None,
        text_index=segs[0][0] if segs else None,
        text_segments=segs if len(segs) > 1 else None)
    row["_tool_uses"] = tool_uses
    row["text"] = "".join(t for _, t in segs)[:300]
    return row


async def run_arm(provider, arm: Arm, turns: List[str], nonce: str, out) -> None:
    system = f"Probe run {nonce}/{arm.name}.\n{FILLER}{arm.system_extra}"
    messages: List[Dict[str, Any]] = []
    for ti, user in enumerate(turns, 1):
        messages.append({"role": "user", "content": user})
        for it in range(1, 9):
            tools = arm.tools()
            n_tools = len(tools)
            row = await one_request(provider, messages, system, tools)
            assistant, tool_uses = row.pop("_assistant"), row.pop("_tool_uses")
            rec = {"arm": arm.name, "turn": ti, "iteration": it, "tools_sent": n_tools,
                   "deferred": sum(1 for t in tools if t.get("defer_loading")), **row}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(f"  {arm.name} t{ti}.{it} in={rec['input']} read={rec['cache_read']} write={rec['cache_write']} "
                  f"out={rec['output']} {rec['latency_s']}s stop={rec['stop']} calls={rec['calls']}"
                  + (f" ERROR {rec['error']}" if rec["error"] else ""), flush=True)
            if rec["error"]:
                return
            messages.append(assistant)
            if not tool_uses:
                break
            messages.append(provider.build_tool_result_message(
                [{"tool_use_id": tu["id"], "content": arm.run_tool(tu["name"], tu["input"])} for tu in tool_uses]))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("ZIYA_AWS_PROFILE"))
    ap.add_argument("--model", default="fable5")
    ap.add_argument("--reach", default=DEFAULT_REACH)
    ap.add_argument("--arms", default="eager,loader,native")
    ap.add_argument("--turns", type=int, default=len(TURNS))
    ap.add_argument("--tools-json", default=None)
    args = ap.parse_args()

    from app.agents.models import ModelManager
    from app.providers.bedrock_mantle import BedrockMantleProvider, resolve_mantle_region
    cfg = ModelManager.get_model_config("bedrock", args.model)
    provider = BedrockMantleProvider(cfg["model_id"], cfg, region=resolve_mantle_region(cfg),
                                     aws_profile=args.profile)
    print(f"model={cfg['model_id']} supports_feature(tool_search)={provider.supports_feature('tool_search')}")
    tools = fetch_tools(os.environ.get("ZIYA_PORT", "6969"), args.tools_json)
    reach = [s for s in args.reach.split(",") if s]
    print(f"{len(tools)} tools; within reach: {sum(1 for t in tools if t['server'] in reach)} on {len(reach)} servers")
    out_dir = Path(".ziya/probe_native_tool_search")
    out_dir.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:8]
    path = out_dir / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{nonce}.jsonl"
    with path.open("w") as out:
        for name in args.arms.split(","):
            print(f"arm {name}")
            await run_arm(provider, Arm(name, tools, reach), TURNS[: args.turns], nonce, out)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

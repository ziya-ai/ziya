"""Auto-bisect a conversation that Bedrock refuses.

A Bedrock/Anthropic refusal arrives as ``stop_reason: "refusal"`` with an
empty content array and ~3-8 output tokens.  ``stop_details.category`` and
``.explanation`` are null, so the API does not say *which* content tripped
the safety classifier.  The refusal is deterministic on a given payload,
which makes it bisectable.

This script loads a stored conversation, confirms the refusal reproduces
(the control probe), then binary-searches for the shortest message prefix
that still refuses.  The messages newly admitted at that boundary are the
suspects.  An optional leave-one-out pass distinguishes "this message
alone triggers it" from "this message in combination with earlier
context".

Usage:
    python scripts/bisect_refusal.py <chat_id_or_substring>
    python scripts/bisect_refusal.py 255ff468 --model opus4.6
    python scripts/bisect_refusal.py 255ff468 --system-file sys.txt --loo

Cost warning: every probe re-sends a large prefix.  Bisection over N
messages costs about ceil(log2(N)) + 2 probes -- roughly 8 for a 47-turn
conversation.  Prompt caching is deliberately NOT used (cache writes cost
more than plain input and the payload changes every probe), so budget
accordingly.  Token usage is reported per probe and in total.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REFUSAL = "refusal"


# ---------------------------------------------------------------- loading


@dataclass
class ApiMessage:
    """One outgoing message plus the original indices it came from."""
    role: str
    content: str
    origin: List[int] = field(default_factory=list)


def _iter_chat_files() -> List[Path]:
    projects = Path(os.environ.get("ZIYA_HOME") or (Path.home() / ".ziya")) / "projects"
    if not projects.exists():
        return []
    return [
        p for p in projects.glob("*/chats/*.json")
        if not p.name.startswith("_") and not p.name.endswith(".bindings.json")
    ]


def _bootstrap_plugins() -> None:
    """Load the plugin providers that supply the encryption KEK.

    Chat files are AES-GCM encrypted under a DEK wrapped by a KEK that a
    registered encryption provider supplies.  Providers only exist after
    ``app.plugins.initialize()`` runs, and that call only loads internal
    plugins when ZIYA_LOAD_INTERNAL_PLUGINS is set -- which the server
    does at startup but a bare script does not.  Without this, the
    encryptor reports is_enabled() == False and every read fails, which
    looks like a missing key rather than an uninitialized plugin system.

    Must run before the first get_encryptor() call: the encryptor is a
    lazy singleton that snapshots policy and KEK on construction.
    """
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    try:
        import app.plugins as plugins
        plugins.initialize()
    except Exception as exc:  # noqa: BLE001 - diagnostic, not fatal yet
        print(f"warning: plugin bootstrap failed ({type(exc).__name__}: {exc})",
              file=sys.stderr)


def _read_chat(path: Path) -> Optional[dict]:
    """Read a chat file, transparently decrypting an ALE envelope."""
    from app.utils.encryption import is_encrypted, get_encryptor

    raw = path.read_bytes()
    if not raw:
        return None
    if is_encrypted(raw):
        encryptor = get_encryptor()
        if not encryptor.is_enabled():
            raise SystemExit(
                f"{path.name} is encrypted but no encryption key is available.\n"
                "The KEK comes from a registered encryption provider, which "
                "requires ZIYA_LOAD_INTERNAL_PLUGINS=1 and a successful\n"
                "app.plugins.initialize().  Check the bootstrap warning above."
            )
        raw = encryptor.decrypt(raw)
    data = json.loads(raw)
    return data if isinstance(data, dict) else None


def resolve_chat(needle: str) -> Tuple[Path, dict]:
    """Find exactly one chat whose id contains ``needle``."""
    matches = [p for p in _iter_chat_files() if needle in p.stem]
    if not matches:
        raise SystemExit(f"No chat id matching {needle!r} under ~/.ziya/projects")
    if len(matches) > 1:
        listing = "\n".join(f"  {p.parent.parent.name}/{p.stem}" for p in matches[:10])
        raise SystemExit(
            f"Ambiguous: {len(matches)} chats matched {needle!r}. First 10:\n{listing}"
        )
    path = matches[0]
    data = _read_chat(path)
    if not data:
        raise SystemExit(f"Could not read {path}")
    return path, data


# ------------------------------------------------------------ normalizing


def to_api_messages(raw_messages: List[dict], include_muted: bool = False) -> List[ApiMessage]:
    """Map stored messages onto a valid Anthropic message array.

    Mirrors the constraints the real send path enforces: role names are
    normalized, empty and muted turns are dropped, consecutive same-role
    turns are coalesced, and any leading assistant turn is discarded so the
    array starts on a user turn.  Original indices are preserved so results
    can be reported against the conversation the user actually sees.
    """
    out: List[ApiMessage] = []
    for idx, msg in enumerate(raw_messages):
        role = (msg.get("role") or msg.get("type") or "").strip()
        if role in ("human", "user"):
            role = "user"
        elif role in ("assistant", "ai"):
            role = "assistant"
        else:
            continue  # system/other turns are not part of the messages array
        if msg.get("muted") and not include_muted:
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            continue
        if out and out[-1].role == role:
            out[-1].content = out[-1].content + "\n\n" + content
            out[-1].origin.append(idx)
        else:
            out.append(ApiMessage(role=role, content=content, origin=[idx]))

    while out and out[0].role != "user":
        out.pop(0)
    return out


def sanitize_slice(msgs: List[ApiMessage]) -> List[ApiMessage]:
    """Make an arbitrary slice sendable: starts on user, ends on user."""
    s = list(msgs)
    while s and s[0].role != "user":
        s.pop(0)
    while s and s[-1].role != "user":
        s.pop()
    return s


# ----------------------------------------------------------------- probing


@dataclass
class ProbeResult:
    stop_reason: Optional[str]
    stop_details: Any
    input_tokens: int
    output_tokens: int
    n_messages: int
    last_origin: Optional[int]
    error: Optional[str] = None

    @property
    def refused(self) -> bool:
        return self.stop_reason == REFUSAL


def resolve_model_id(alias: str, region: str, prefix: Optional[str]) -> str:
    from app.config.models_config import MODEL_CONFIGS, MODEL_ALIASES

    bedrock = MODEL_CONFIGS["bedrock"]
    alias = MODEL_ALIASES.get("bedrock", {}).get(alias, alias)
    if alias not in bedrock:
        raise SystemExit(f"Unknown bedrock model alias {alias!r}")
    model_id = bedrock[alias].get("model_id")
    if isinstance(model_id, str):
        return model_id
    if not prefix:
        prefix = "us" if region.startswith("us-") else "eu" if region.startswith("eu-") else "global"
    if prefix not in model_id:
        prefix = next(iter(model_id))
    return model_id[prefix]


class Prober:
    """Issues one Bedrock invocation per probe and reports stop_reason."""

    def __init__(
        self,
        model_id: str,
        region: str,
        profile: Optional[str],
        system_text: Optional[str],
        tools: Optional[List[dict]],
        max_tokens: int,
        stream: bool,
        pause: float,
    ):
        import boto3

        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self.client = session.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.system_text = system_text
        self.tools = tools
        self.max_tokens = max_tokens
        self.stream = stream
        self.pause = pause
        self.probes = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _body(self, msgs: List[ApiMessage]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }
        if self.system_text:
            body["system"] = [{"type": "text", "text": self.system_text}]
        if self.tools:
            body["tools"] = self.tools
            body["tool_choice"] = {"type": "auto"}
        return body

    def _invoke(self, body: Dict[str, Any]) -> Tuple[Optional[str], Any, int, int]:
        payload = json.dumps(body)
        if not self.stream:
            resp = self.client.invoke_model(modelId=self.model_id, body=payload)
            data = json.loads(resp["body"].read())
            usage = data.get("usage", {}) or {}
            return (
                data.get("stop_reason"),
                data.get("stop_details"),
                usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
                usage.get("output_tokens", 0),
            )

        resp = self.client.invoke_model_with_response_stream(
            modelId=self.model_id, body=payload
        )
        stop_reason, stop_details, in_tok, out_tok = None, None, 0, 0
        for event in resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                continue
            parsed = json.loads(chunk["bytes"].decode("utf-8"))
            if parsed.get("type") == "message_delta":
                delta = parsed.get("delta", {}) or {}
                stop_reason = delta.get("stop_reason") or stop_reason
                stop_details = delta.get("stop_details") or stop_details
                usage = parsed.get("usage", {}) or {}
                in_tok = usage.get("input_tokens", in_tok)
                out_tok = usage.get("output_tokens", out_tok)
        return stop_reason, stop_details, in_tok, out_tok

    def probe(self, msgs: List[ApiMessage], label: str) -> ProbeResult:
        msgs = sanitize_slice(msgs)
        if not msgs:
            return ProbeResult(None, None, 0, 0, 0, None, error="empty slice")

        last_origin = msgs[-1].origin[-1] if msgs[-1].origin else None
        attempt, backoff = 0, 4.0
        while True:
            try:
                stop_reason, stop_details, in_tok, out_tok = self._invoke(self._body(msgs))
                break
            except Exception as exc:  # noqa: BLE001 - surface, then decide
                name = type(exc).__name__
                text = str(exc)
                throttled = "Throttl" in text or "TooManyRequests" in text
                if throttled and attempt < 4:
                    attempt += 1
                    print(f"    throttled, retrying in {backoff:.0f}s", flush=True)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                print(f"  {label}: ERROR {name}: {text[:200]}", flush=True)
                return ProbeResult(None, None, 0, 0, len(msgs), last_origin,
                                   error=f"{name}: {text[:400]}")

        self.probes += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        verdict = "REFUSED" if stop_reason == REFUSAL else f"ok ({stop_reason})"
        print(
            f"  {label}: {len(msgs)} msgs, last=orig#{last_origin}, "
            f"{in_tok:,} in / {out_tok} out -> {verdict}",
            flush=True,
        )
        if self.pause:
            time.sleep(self.pause)
        return ProbeResult(stop_reason, stop_details, in_tok, out_tok,
                           len(msgs), last_origin)


# --------------------------------------------------------------- bisection


def bisect_prefix(prober: Prober, msgs: List[ApiMessage],
                  trace: List[dict]) -> Optional[int]:
    """Smallest prefix length whose slice still refuses.

    Assumes refusal is monotonic in prefix length -- adding context never
    un-refuses.  That assumption is verified at the boundary once the
    search lands, and a violation is reported rather than hidden.
    """
    lo, hi = 1, len(msgs)  # hi is known-refusing (control probe established it)
    seen: Dict[int, bool] = {}
    while lo < hi:
        mid = (lo + hi) // 2
        # Distinct prefix lengths can sanitize to the SAME payload (a
        # trailing assistant turn is trimmed), so key the probe cache on
        # the sanitized length to avoid paying for a duplicate request.
        key = len(sanitize_slice(msgs[:mid]))
        if key in seen:
            refused = seen[key]
        else:
            res = prober.probe(msgs[:mid], f"prefix[:{mid}]")
            trace.append({"kind": "prefix", "n": mid, **asdict(res)})
            if res.error:
                return None
            refused = res.refused
            seen[key] = refused
        if refused:
            hi = mid
        else:
            lo = mid + 1
    return lo


def leave_one_out(prober: Prober, msgs: List[ApiMessage], upto: int,
                  band: int, trace: List[dict]) -> List[int]:
    """Drop one message at a time from the refusing prefix.

    A message whose removal makes the refusal disappear is load-bearing.
    Only the last ``band`` messages of the prefix are tested, since the
    prefix search already localized the trigger to its tail.
    """
    culprits: List[int] = []
    start = max(0, upto - band)
    for i in range(start, upto):
        subset = msgs[:i] + msgs[i + 1:upto]
        origin = msgs[i].origin[-1] if msgs[i].origin else "?"
        res = prober.probe(subset, f"minus orig#{origin}")
        trace.append({"kind": "loo", "removed_index": i,
                      "removed_origin": msgs[i].origin, **asdict(res)})
        if res.error:
            continue
        if not res.refused:
            culprits.append(i)
    return culprits


# -------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bisect a stored conversation to find what Bedrock refuses."
    )
    ap.add_argument("chat", help="chat id or unique substring of one")
    ap.add_argument("--model", default="opus4.6",
                    help="bedrock model alias (default: opus4.6)")
    ap.add_argument("--model-id", default=None,
                    help="explicit Bedrock modelId, overrides --model")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    ap.add_argument("--prefix", default=None, choices=["us", "eu", "global"],
                    help="inference-profile prefix (default: inferred from region)")
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--max-tokens", type=int, default=16,
                    help="output cap; small keeps probes cheap (default: 16)")
    ap.add_argument("--system-file", default=None,
                    help="file whose text is sent as the system prompt")
    ap.add_argument("--tools-file", default=None,
                    help="JSON file containing the Anthropic tools array")
    ap.add_argument("--stream", action="store_true",
                    help="probe via streaming API instead of invoke_model")
    ap.add_argument("--loo", action="store_true",
                    help="after bisection, run a leave-one-out pass")
    ap.add_argument("--loo-band", type=int, default=4,
                    help="how many tail messages to leave-one-out (default: 4)")
    ap.add_argument("--include-muted", action="store_true")
    ap.add_argument("--pause", type=float, default=1.0,
                    help="seconds between probes (default: 1.0)")
    ap.add_argument("--out", default=None, help="write JSON trace here")
    args = ap.parse_args()

    _bootstrap_plugins()
    path, chat = resolve_chat(args.chat)
    raw_messages = chat.get("messages", []) or []
    msgs = to_api_messages(raw_messages, include_muted=args.include_muted)
    if not msgs:
        raise SystemExit("Conversation has no sendable messages")

    model_id = args.model_id or resolve_model_id(args.model, args.region, args.prefix)
    system_text = Path(args.system_file).read_text() if args.system_file else None
    tools = json.loads(Path(args.tools_file).read_text()) if args.tools_file else None

    print(f"chat      {path.stem}  ({chat.get('title', '')!r})")
    print(f"project   {path.parent.parent.name}")
    print(f"messages  {len(raw_messages)} stored -> {len(msgs)} sendable")
    print(f"model     {model_id}  region={args.region}  profile={args.profile}")
    print(f"payload   system={'yes' if system_text else 'no'} "
          f"tools={len(tools) if tools else 0} max_tokens={args.max_tokens} "
          f"stream={args.stream}")
    print()

    prober = Prober(model_id, args.region, args.profile, system_text, tools,
                    args.max_tokens, args.stream, args.pause)
    trace: List[dict] = []

    print("control probe (full conversation):")
    control = prober.probe(msgs, "full")
    trace.append({"kind": "control", **asdict(control)})
    if control.error:
        print("\nControl probe errored -- cannot bisect. Fix the error above first.")
        return 2
    if not control.refused:
        print(f"\nFull conversation did NOT refuse (stop_reason={control.stop_reason!r}).")
        print("Nothing to bisect. The refusal likely depends on payload components")
        print("this probe omitted -- retry with --system-file and/or --tools-file")
        print("to reproduce the real request more closely.")
        return 1
    if control.stop_details:
        print(f"  stop_details: {control.stop_details}")

    print("\nbisecting shortest refusing prefix:")
    n = bisect_prefix(prober, msgs, trace)
    if n is None:
        print("\nBisection aborted on a probe error.")
        return 2

    # The trigger is whatever the shortest refusing payload contains that
    # the largest non-refusing payload did not.  That is NOT always
    # msgs[n-1]: sanitize_slice trims a trailing assistant turn, so
    # prefix[:n] can newly admit BOTH an assistant turn and the user turn
    # after it.  Reporting msgs[n-1] alone silently blames the user turn
    # for an assistant turn's content.
    kept = sanitize_slice(msgs[:n])
    prev_kept = sanitize_slice(msgs[:n - 1]) if n > 1 else []
    newly = kept[len(prev_kept):] if len(kept) > len(prev_kept) else kept[-1:]
    suspect = newly[0]
    origin = [i for m in newly for i in m.origin]
    print(f"\nShortest refusing prefix: {n} sendable messages "
          f"({len(kept)} after sanitizing).")
    if len(newly) == 1:
        print(f"Primary suspect: role={suspect.role}, stored index "
              f"{suspect.origin}, {len(suspect.content):,} chars")
    else:
        print(f"Primary suspects ({len(newly)} messages newly admitted at "
              f"this boundary — the trigger is in one of them):")
        for m in newly:
            print(f"  role={m.role} stored={m.origin} {len(m.content):,} chars")

    if n > 1:
        check = prober.probe(msgs[:n - 1], f"verify prefix[:{n - 1}]")
        trace.append({"kind": "verify", "n": n - 1, **asdict(check)})
        if check.refused:
            print("  WARNING: prefix one shorter ALSO refuses -- refusal is not")
            print("  monotonic in prefix length, so this boundary is unreliable.")

    solo = prober.probe(newly, "suspect(s) alone")
    trace.append({"kind": "solo", **asdict(solo)})
    if solo.error:
        print(f"  (solo probe not usable: {solo.error})")
    elif solo.refused:
        print("  Suspect refuses ON ITS OWN -- the trigger is inside this message.")
    else:
        print("  Suspect alone does NOT refuse -- the trigger needs earlier context.")

    culprits: List[int] = []
    if args.loo:
        print(f"\nleave-one-out over last {args.loo_band} messages of the prefix:")
        culprits = leave_one_out(prober, msgs, n, args.loo_band, trace)
        if culprits:
            for i in culprits:
                print(f"  load-bearing: sendable #{i} (stored {msgs[i].origin})")
        else:
            print("  No single removal cleared the refusal -- likely cumulative.")

    print(f"\nprobes {prober.probes}  input tokens {prober.input_tokens:,}  "
          f"output tokens {prober.output_tokens:,}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "chat_id": path.stem,
            "project_id": path.parent.parent.name,
            "model_id": model_id,
            "region": args.region,
            "stored_message_count": len(raw_messages),
            "sendable_message_count": len(msgs),
            "shortest_refusing_prefix": n,
            "suspect_origin": origin,
            "loo_culprits": [msgs[i].origin for i in culprits],
            "probes": prober.probes,
            "input_tokens": prober.input_tokens,
            "trace": trace,
        }, indent=2))
        print(f"trace written to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    # Not necessarily str: a captured payload carries content BLOCK LISTS
    # (text/tool_use/tool_result/image).  Flattening those to text would
    # change the bytes being tested, which defeats the purpose of probing a
    # captured payload, so the original shape is passed through verbatim.
    content: Any
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


def content_len(content: Any) -> int:
    """Reportable size for either a string or a content-block list."""
    return len(content) if isinstance(content, str) else len(json.dumps(content))


def _strip_cache_control(content: Any) -> Any:
    """Drop cache_control breadcrumbs from a captured content list.

    Prompt caching is deliberately unused here (see module docstring), and a
    captured payload arrives with the server's cache_control markers still on
    it.  Left in place, every probe would pay for a cache WRITE of a payload
    that changes each probe -- strictly more expensive than plain input.
    Removing them alters only cache metadata, never the text the classifier
    sees, so refusal behaviour is unaffected.
    """
    if isinstance(content, list):
        return [
            {k: v for k, v in b.items() if k != "cache_control"}
            if isinstance(b, dict) else b
            for b in content
        ]
    return content


def load_dump_messages(path: Path, keep_cache_control: bool = False) -> List[ApiMessage]:
    """Load a messages array captured by ZIYA_DUMP_REQUEST_PARTS.

    Bisecting the STORED conversation tests a payload that was never sent.
    The send path rewrites content on the way out -- redact_garbled replaces
    flagged spans with placeholders and normalize_paste_artifacts substitutes
    private-use codepoints -- and prepare_cache_control restructures blocks.
    Since the suspect content in a refusal can be a *product* of those
    rewrites, reconstructing from the chat file can both miss a real trigger
    and invent one that was never transmitted.  This loads the transmitted
    bytes instead, bypassing to_api_messages entirely.
    """
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise SystemExit(f"{path}: expected a JSON messages array")
    out: List[ApiMessage] = []
    for idx, msg in enumerate(raw):
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "").strip()
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if not keep_cache_control:
            content = _strip_cache_control(content)
        if not content:
            continue
        out.append(ApiMessage(role=role, content=content, origin=[idx]))
    while out and out[0].role != "user":
        out.pop(0)
    return out


def parts_files(parts_dir: str) -> Dict[str, Optional[Path]]:
    """Map a dump directory onto the three payload components."""
    d = Path(os.path.expanduser(parts_dir))
    if not d.is_dir():
        raise SystemExit(f"{d} is not a directory")
    return {
        name: (d / fname if (d / fname).exists() else None)
        for name, fname in (("system", "system.txt"),
                            ("tools", "tools.json"),
                            ("messages", "messages.json"),
                            ("params", "params.json"))
    }


THINKING_TYPES = ("thinking", "redacted_thinking")


def has_thinking_blocks(msgs: List[ApiMessage]) -> bool:
    """True if any captured message carries an echoed reasoning block."""
    for m in msgs:
        if isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, dict) and b.get("type") in THINKING_TYPES:
                    return True
    return False


def strip_thinking_blocks(msgs: List[ApiMessage]) -> List[ApiMessage]:
    """Copy of ``msgs`` with thinking/redacted_thinking blocks removed.

    This is the passback A/B lever: the live executor echoes signed
    reasoning blocks into assistant turns during a tool chain
    (thinking_passback), and this produces the otherwise-identical payload
    WITHOUT them.  A message left with no blocks at all is dropped rather
    than sent empty (the API rejects empty content arrays).
    """
    out: List[ApiMessage] = []
    for m in msgs:
        if not isinstance(m.content, list):
            out.append(m)
            continue
        kept = [b for b in m.content
                if not (isinstance(b, dict) and b.get("type") in THINKING_TYPES)]
        if kept:
            out.append(ApiMessage(role=m.role, content=kept, origin=list(m.origin)))
    return out


def resolve_thinking(args, parts: Dict[str, Optional[Path]]) -> Optional[dict]:
    """Thinking config for the probe request body, or None for off.

    ``auto`` replays whatever the captured request used (params.json from
    --parts-dir); without a capture it falls back to off, preserving the
    script's historical behaviour.  The live fable5/opus4.7+ path always
    runs adaptive thinking, so probing a captured payload from those models
    without this reproduces a request that was never sent.
    """
    if args.thinking == "off":
        return None
    if args.thinking == "adaptive":
        return {"type": "adaptive", "display": "summarized"}
    if args.thinking == "enabled":
        return {"type": "enabled", "budget_tokens": args.thinking_budget}
    # auto
    params_file = parts.get("params")
    if params_file:
        try:
            captured = json.loads(Path(params_file).read_text()).get("thinking")
            if isinstance(captured, dict):
                return captured
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: could not read thinking config from {params_file}: {e}")
    return None


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
        thinking: Optional[dict] = None,
    ):
        self.model_id = model_id
        self.system_text = system_text
        self.tools = tools
        self.max_tokens = max_tokens
        self.stream = stream
        self.pause = pause
        self.thinking = thinking
        self.probes = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._connect(region, profile)

    def _connect(self, region: str, profile: Optional[str]) -> None:
        """Open the transport. Overridden to probe a different endpoint."""
        import boto3

        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self.client = session.client("bedrock-runtime", region_name=region)

    def _body(self, msgs: List[ApiMessage]) -> Dict[str, Any]:
        max_tokens = self.max_tokens
        if self.thinking and self.thinking.get("type") == "enabled":
            # The API requires max_tokens > budget_tokens; a refusal still
            # arrives within a few output tokens, so the headroom is cheap.
            max_tokens = max(max_tokens,
                             int(self.thinking.get("budget_tokens", 0)) + 64)
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }
        if self.thinking:
            body["thinking"] = self.thinking
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


_MANTLE_BASE_URL = "https://bedrock-mantle.{region}.api.aws/anthropic"


class MantleProber(Prober):
    """Probes Bedrock Mantle rather than bedrock-runtime.

    The two endpoints do not apply the same safety classifier: a payload
    that mantle refuses can pass on bedrock-runtime.  Probing
    bedrock-runtime for a mantle-only refusal therefore reports "did not
    reproduce" and finds nothing -- which is what this script did for every
    mantle-routed model (fable5, mythos5), the exact models whose refusals
    prompted writing it.

    Mantle speaks the Anthropic Messages API over SigV4 rather than the
    Bedrock invoke_model shape, so ``model`` moves from the request envelope
    into the body.  ``anthropic_version`` is REQUIRED and stays: omitting it
    returns 400 "anthropic_version: Field required" (verified live).
    """

    def _connect(self, region: str, profile: Optional[str]) -> None:
        import boto3
        import httpx
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            raise SystemExit("No AWS credentials available for Bedrock Mantle")
        # Hold the resolver, not a frozen snapshot: bisecting a large
        # conversation can outlive short-lived STS/SSO credentials, and
        # get_frozen_credentials() refreshes when due.  Mirrors the
        # reasoning in _AsyncSigV4Transport.
        self._creds = creds
        self._sigv4 = SigV4Auth
        self._aws_request = AWSRequest
        self._region = region
        self._url = _MANTLE_BASE_URL.format(region=region) + "/v1/messages"
        self.client = None
        # A refusal returns in ~1s, but a NON-refusing probe on a 100k-token
        # prefix legitimately takes minutes; a short read timeout would look
        # like an error and abort the bisection mid-search.
        self._http = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, write=120.0, read=600.0, pool=60.0)
        )

    def _signed_headers(self, payload: str) -> Dict[str, str]:
        req = self._aws_request(
            method="POST", url=self._url,
            headers={"content-type": "application/json"},
            data=payload.encode("utf-8"),
        )
        self._sigv4(self._creds.get_frozen_credentials(), "bedrock",
                    self._region).add_auth(req)
        return dict(req.headers)

    @staticmethod
    def _usage(data: dict) -> Tuple[int, int]:
        u = data.get("usage", {}) or {}
        return (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                u.get("output_tokens", 0))

    def _invoke(self, body: Dict[str, Any]) -> Tuple[Optional[str], Any, int, int]:
        body = dict(body)
        body["model"] = self.model_id
        if self.stream:
            body["stream"] = True
        payload = json.dumps(body)
        headers = self._signed_headers(payload)

        if not self.stream:
            resp = self._http.post(self._url, content=payload, headers=headers)
            if resp.status_code != 200:
                # Name the exception ThrottlingException on 429 so Prober.probe's
                # existing "Throttl" backoff recognises it; mantle's 429 body
                # does not reliably carry that string.
                kind = "ThrottlingException" if resp.status_code == 429 else "HTTPError"
                raise RuntimeError(f"{kind}: HTTP {resp.status_code} {resp.text[:300]}")
            data = resp.json()
            in_tok, out_tok = self._usage(data)
            return data.get("stop_reason"), data.get("stop_details"), in_tok, out_tok

        stop_reason, stop_details, in_tok, out_tok = None, None, 0, 0
        with self._http.stream("POST", self._url, content=payload,
                               headers=headers) as resp:
            if resp.status_code != 200:
                resp.read()
                kind = "ThrottlingException" if resp.status_code == 429 else "HTTPError"
                raise RuntimeError(f"{kind}: HTTP {resp.status_code} {resp.text[:300]}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if parsed.get("type") == "message_start":
                    in_tok, out_tok = self._usage(parsed.get("message", {}) or {})
                elif parsed.get("type") == "message_delta":
                    delta = parsed.get("delta", {}) or {}
                    stop_reason = delta.get("stop_reason") or stop_reason
                    stop_details = delta.get("stop_details") or stop_details
                    d_in, d_out = self._usage(parsed)
                    in_tok = d_in or in_tok
                    out_tok = d_out or out_tok
        return stop_reason, stop_details, in_tok, out_tok


# --------------------------------------------------------- thinking A/B


def ab_thinking(prober: "Prober", msgs: List[ApiMessage],
                thinking_cfg: Optional[dict], blocks_present: bool,
                args) -> int:
    """Probe the full conversation across the thinking dimensions.

    Isolates WHICH thinking-related aspect of the payload draws the
    refusal, if any: the thinking request parameter itself, or the signed
    reasoning blocks echoed back into assistant turns (thinking_passback).
    Uses full-conversation probes only -- combine with the bisector
    afterwards once the refusing dimension is known.

    The off/kept combination is a request shape the live path never emits
    and the API may reject outright; its row reports the error rather than
    skipping it, since a 4xx there is itself evidence that the captured
    payload depends on thinking being enabled.
    """
    on_cfg = thinking_cfg or {"type": "adaptive", "display": "summarized"}
    stripped = strip_thinking_blocks(msgs) if blocks_present else msgs
    combos: List[Tuple[str, Optional[dict], List[ApiMessage]]] = [
        ("thinking=on  blocks=kept    ", on_cfg, msgs),
    ]
    if blocks_present:
        combos.append(("thinking=on  blocks=stripped", on_cfg, stripped))
    combos.append(("thinking=off blocks=stripped", None, stripped))
    if blocks_present:
        combos.append(("thinking=off blocks=kept    ", None, msgs))

    print("thinking A/B (full conversation, one probe per combination):")
    rows: List[Tuple[str, "ProbeResult"]] = []
    for label, cfg, use_msgs in combos:
        prober.thinking = cfg
        res = prober.probe(use_msgs, label)
        rows.append((label, res))
    prober.thinking = thinking_cfg

    print()
    verdicts = {}
    for label, res in rows:
        outcome = (f"error: {res.error[:120]}" if res.error
                   else res.stop_reason)
        verdicts[label.strip()] = outcome
        print(f"  {label}  ->  {outcome}")
        if res.stop_details:
            print(f"      stop_details: {res.stop_details}")

    refusing = [l for l, r in rows if r.refused]
    passing = [l for l, r in rows if not r.refused and not r.error]
    print()
    if not refusing:
        print("No combination refused. The refusal is not reproducible on this "
              "payload as captured -- it may depend on components this probe "
              "omitted (system prompt, tools) or on the endpoint state.")
    elif not passing:
        print("Every combination refused. The trigger is not thinking-related; "
              "bisect the messages instead (drop --ab-thinking).")
    else:
        print("Refusal depends on the thinking dimension:")
        for l in refusing:
            print(f"  refuses: {l.strip()}")
        for l in passing:
            print(f"  passes:  {l.strip()}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"mode": "ab_thinking", "verdicts": verdicts}, indent=2))
    print(f"\ntokens: {prober.input_tokens} in / {prober.output_tokens} out "
          f"across {prober.probes} probes")
    return 0 if passing and refusing else 1


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
    ap.add_argument("chat", nargs="?", default=None,
                    help="chat id or unique substring of one; omit when "
                         "supplying --messages-file/--parts-dir")
    ap.add_argument("--model", default="opus4.6",
                    help="bedrock model alias (default: opus4.6)")
    ap.add_argument("--model-id", default=None,
                    help="explicit Bedrock modelId, overrides --model")
    ap.add_argument("--region", default=None,
                    help="AWS region (default: AWS_REGION, or us-east-1 with --mantle)")
    ap.add_argument("--prefix", default=None, choices=["us", "eu", "global"],
                    help="inference-profile prefix (default: inferred from region)")
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--max-tokens", type=int, default=16,
                    help="output cap; small keeps probes cheap (default: 16)")
    ap.add_argument("--system-file", default=None,
                    help="file whose text is sent as the system prompt")
    ap.add_argument("--tools-file", default=None,
                    help="JSON file containing the Anthropic tools array")
    ap.add_argument("--messages-file", default=None,
                    help="messages.json captured by ZIYA_DUMP_REQUEST_PARTS; "
                         "probes the TRANSMITTED payload instead of rebuilding "
                         "it from the stored chat")
    ap.add_argument("--parts-dir", default=None,
                    help="ZIYA_DUMP_REQUEST_PARTS directory; supplies system, "
                         "tools and messages together")
    ap.add_argument("--keep-cache-control", action="store_true",
                    help="keep cache_control markers from a captured payload")
    ap.add_argument("--thinking", default="auto",
                    choices=["auto", "off", "adaptive", "enabled"],
                    help="thinking config for probe requests. auto replays the "
                         "captured config from --parts-dir params.json (off "
                         "when absent); adaptive matches the live fable5/"
                         "opus4.7+ path; enabled uses --thinking-budget")
    ap.add_argument("--thinking-budget", type=int, default=4096,
                    help="budget_tokens for --thinking enabled (default 4096)")
    ap.add_argument("--strip-thinking", action="store_true",
                    help="remove echoed thinking/redacted_thinking blocks from "
                         "captured assistant turns before probing (the "
                         "thinking-passback A/B lever)")
    ap.add_argument("--ab-thinking", action="store_true",
                    help="instead of bisecting, probe the full conversation "
                         "across the thinking dimensions (config on/off x "
                         "echoed blocks kept/stripped) and report which "
                         "combination refuses")
    ap.add_argument("--mantle", action="store_true",
                    help="probe Bedrock Mantle instead of bedrock-runtime; "
                         "required to reproduce mantle-only refusals")
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

    if args.region:
        region = args.region
    elif args.mantle:
        # The mantle anthropic models are served from us-east-1 only, so a
        # stale AWS_REGION would sign for a host that does not exist and the
        # connection error would surface as a probe failure rather than as a
        # misconfiguration.
        region = "us-east-1"
    else:
        region = os.environ.get("AWS_REGION", "us-west-2")

    _bootstrap_plugins()

    parts = parts_files(args.parts_dir) if args.parts_dir else {}
    messages_file = args.messages_file or parts.get("messages")
    system_file = args.system_file or parts.get("system")
    tools_file = args.tools_file or parts.get("tools")

    path = chat = None
    raw_messages: List[Any] = []
    if messages_file:
        msgs = load_dump_messages(Path(messages_file), args.keep_cache_control)
        source = str(messages_file)
    else:
        if not args.chat:
            raise SystemExit("Give a chat id, or --messages-file/--parts-dir")
        path, chat = resolve_chat(args.chat)
        raw_messages = chat.get("messages", []) or []
        msgs = to_api_messages(raw_messages, include_muted=args.include_muted)
        source = f"{path.stem} ({chat.get('title', '')!r})"
    if not msgs:
        raise SystemExit("No sendable messages")

    model_id = args.model_id or resolve_model_id(args.model, region, args.prefix)
    system_text = Path(system_file).read_text() if system_file else None
    tools = json.loads(Path(tools_file).read_text()) if tools_file else None

    thinking_cfg = resolve_thinking(args, parts)
    # thinking "enabled" requires max_tokens > budget_tokens; the cheap
    # probe default (16) would 400 on every probe and read as an error.
    if thinking_cfg and thinking_cfg.get("type") == "enabled":
        _budget = int(thinking_cfg.get("budget_tokens") or 0)
        if args.max_tokens <= _budget:
            args.max_tokens = _budget + 64
            print(f"note: raised --max-tokens to {args.max_tokens} "
                  f"(thinking budget_tokens={_budget} must be < max_tokens)")
    blocks_present = has_thinking_blocks(msgs)
    if blocks_present and not thinking_cfg and not args.strip_thinking:
        # Thinking blocks in messages with thinking disabled is a request
        # shape the live path never sends (and the API may reject) --
        # warn rather than silently probe an unrepresentative payload.
        print("warning: captured messages carry thinking blocks but the probe "
              "request has thinking off; pass --thinking adaptive (or use "
              "--parts-dir with params.json) or --strip-thinking")
    if args.strip_thinking:
        msgs = strip_thinking_blocks(msgs)

    print(f"source    {source}")
    if path is not None:
        print(f"project   {path.parent.parent.name}")
        print(f"messages  {len(raw_messages)} stored -> {len(msgs)} sendable")
    else:
        print(f"messages  {len(msgs)} captured (transmitted payload)")
    print(f"model     {model_id}  region={region}  profile={args.profile}")
    print(f"endpoint  {'bedrock-mantle' if args.mantle else 'bedrock-runtime'}")
    print(f"payload   system={'yes' if system_text else 'no'} "
          f"tools={len(tools) if tools else 0} max_tokens={args.max_tokens} "
          f"stream={args.stream}")
    print(f"thinking  config={json.dumps(thinking_cfg) if thinking_cfg else 'off'} "
          f"echoed_blocks={'stripped' if args.strip_thinking else ('present' if blocks_present else 'none')}")
    print()

    prober_cls = MantleProber if args.mantle else Prober
    prober = prober_cls(model_id, region, args.profile, system_text, tools,
                        args.max_tokens, args.stream, args.pause,
                        thinking=thinking_cfg)
    trace: List[dict] = []

    if args.ab_thinking:
        return ab_thinking(prober, msgs, thinking_cfg, blocks_present, args)

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
              f"{suspect.origin}, {content_len(suspect.content):,} chars")
    else:
        print(f"Primary suspects ({len(newly)} messages newly admitted at "
              f"this boundary — the trigger is in one of them):")
        for m in newly:
            print(f"  role={m.role} stored={m.origin} {content_len(m.content):,} chars")

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
            "chat_id": path.stem if path is not None else None,
            "project_id": path.parent.parent.name if path is not None else None,
            "source": source,
            "model_id": model_id,
            "region": region,
            "endpoint": "bedrock-mantle" if args.mantle else "bedrock-runtime",
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

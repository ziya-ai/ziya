"""Record evidence-ledger entries from a script (design/evidence-ledger.md, step 5).

Standard library only, so an analysis script can ``import`` it (or copy it)
without Ziya's dependencies.  It reads the two variables the shell server
puts in a child's environment and POSTs to the running server:

    from app.utils.ziya_ledger_client import observe, claim
    obs = observe("sql", query_text=open("q.sql").read(), result_path="out.csv")
    claim("audit.s1.error_rate", 0.1872, unit="ratio", from_obs=obs["id"],
          population={"description": "retried requests", "n": 120000},
          derivation={"column": "errors", "expression": "sum(errors)/sum(requests)"})

Provenance (conversation, run, block) is carried by the scope token the
server issued; the script cannot set it.  ``LedgerUnavailable`` is raised
when the variables are absent (not started by the shell server) or the
server refuses the request.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

ENV_URL = "ZIYA_LEDGER_URL"
ENV_SCOPE = "ZIYA_LEDGER_SCOPE"
HEADER = "X-Ziya-Ledger-Scope"


class LedgerUnavailable(RuntimeError):
    """No ledger endpoint in the environment, or the server refused the call."""


def available() -> bool:
    return bool(os.environ.get(ENV_URL) and os.environ.get(ENV_SCOPE))


def _post(path: str, body: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
    url, token = os.environ.get(ENV_URL), os.environ.get(ENV_SCOPE)
    if not url or not token:
        raise LedgerUnavailable(
            f"{ENV_URL} / {ENV_SCOPE} are not set; run this from a shell the Ziya "
            f"shell server started, or record with the ledger_record tool.")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/{path}", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", HEADER: token}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- localhost only
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            msg = payload.get("message") or payload.get("error") or str(payload)
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise LedgerUnavailable(f"ledger {path}: HTTP {e.code}: {msg}") from None
    except (urllib.error.URLError, OSError) as e:
        raise LedgerUnavailable(f"ledger {path}: {e}") from None


def record(observation: Optional[Dict[str, Any]] = None,
           claim: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Raw ``ledger_record``: an observation and/or a claim."""
    body: Dict[str, Any] = {}
    if observation:
        body["observation"] = observation
    if claim:
        body["claim"] = claim
    return _post("record", body)


def observe(source: str, *, query_text: Optional[str] = None, path: Optional[str] = None,
            argv: Optional[List[str]] = None, connector: Optional[str] = None,
            tool: Optional[str] = None, result_path: Optional[str] = None,
            dataset_id: Optional[str] = None, inline: Any = None, has_inline: bool = False,
            volatile: bool = False, as_of: Optional[str] = None) -> Dict[str, Any]:
    """Record one observation; returns ``{id, sha256, attested}``.

    ``result_path`` is hashed by the server (attested).  Relative paths are
    resolved against the project root the scope token names.
    """
    obs: Dict[str, Any] = {"source": source}
    for k, v in (("query_text", query_text), ("path", path), ("argv", argv),
                 ("connector", connector), ("tool", tool), ("result_path", result_path),
                 ("dataset_id", dataset_id), ("as_of", as_of)):
        if v is not None:
            obs[k] = v
    if has_inline or inline is not None:
        obs["inline"] = inline
    if volatile:
        obs["volatile"] = True
    return record(observation=obs)["observation"]


def claim(key: str, value: Any, *, namespace: Optional[str] = None, unit: Optional[str] = None,
          fmt: Optional[str] = None, from_obs: Optional[List[str] | str] = None,
          inputs: Optional[List[str] | Dict[str, str]] = None,
          population: Optional[Dict[str, Any]] = None,
          distribution: Optional[Dict[str, Any]] = None,
          derivation: Optional[Dict[str, Any]] = None,
          tolerance: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Record a claim version; returns the stored claim view.

    ``from_obs`` (observation id or ids) and/or ``inputs`` (claim refs) are
    the claim's sources; ``derivation`` may add ``column``, ``expression``,
    ``code_ref``.
    """
    der = dict(derivation or {})
    if from_obs:
        der["obs_ids"] = [from_obs] if isinstance(from_obs, str) else list(from_obs)
    if inputs:
        der["inputs"] = inputs
    c: Dict[str, Any] = {"key": key, "value": value, "derivation": der}
    for k, v in (("namespace", namespace), ("unit", unit), ("format", fmt),
                 ("population", population), ("distribution", distribution),
                 ("tolerance", tolerance)):
        if v is not None:
            c[k] = v
    return record(claim=c)["claim"]


def query(ref: Optional[str] = None, *, prefix: Optional[str] = None,
          namespace: Optional[str] = None, history: bool = False) -> Dict[str, Any]:
    return _post("query", {"ref": ref, "prefix": prefix, "namespace": namespace,
                           "history": history})


def verify(refs: Optional[List[str]] = None, *, prefix: Optional[str] = None,
           method: str = "rehash") -> Dict[str, Any]:
    return _post("verify", {"refs": refs, "prefix": prefix, "method": method})

"""Importing ``app.agents.models`` must not rewrite the process environment.

Until 2026-11-09 the module loaded ``.env`` (found by walking up from the
package, so typically ``~/.env``) at import time with ``override=True``.
Any import -- a test, a tool module, a script -- therefore replaced the
process environment with the file's copy: a key a test had removed came
back (tests/test_credential_preflight.py hit this), and a key the shell had
exported was overwritten by a stale value in the file.  The load now
happens once, in ``app.config.environment.setup_environment``, which every
entry point calls at startup.

The import half runs in a fresh interpreter: in this process the module is
already imported, so re-importing it proves nothing.
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent

_PROBE = r"""
import json, os, sys
import dotenv, dotenv.main
calls = []
_real = dotenv.main.load_dotenv
def _spy(*a, **k):
    calls.append(k.get("override"))
    return _real(*a, **k)
dotenv.load_dotenv = dotenv.main.load_dotenv = _spy
before = dict(os.environ)
import app.agents.models  # noqa: F401
after = dict(os.environ)
changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
print(json.dumps({"load_dotenv_calls": calls, "changed": changed}))
"""


def test_importing_models_loads_no_dotenv_and_changes_no_env():
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=_ROOT, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    import json
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["load_dotenv_calls"] == [], "import still loads .env"
    # Other import-time writes (ZIYA_* defaults) are allowed; provider keys
    # and AWS settings are the ones that must not move.
    leaked = [k for k in res["changed"]
              if k.endswith("_API_KEY") or k.endswith("_API_TOKEN") or k.startswith("AWS_")]
    assert leaked == [], f"import changed credential env: {leaked}"


def test_setup_environment_still_loads_the_dotenv_file(tmp_path, monkeypatch):
    """Positive control: the load moved, it did not disappear."""
    envfile = tmp_path / ".env"
    envfile.write_text("ZIYA_TEST_DOTENV_MARKER=from-file\n")
    monkeypatch.delenv("ZIYA_TEST_DOTENV_MARKER", raising=False)
    from app.config import environment as env_mod
    from types import SimpleNamespace
    args = SimpleNamespace(root=str(tmp_path), directory=None, include=None, exclude=None,
                           profile=None, region=None, model=None, endpoint=None,
                           model_id=None, max_tokens=None, temperature=None,
                           top_k=None, thinking_mode=False, no_memory=True,
                           max_depth=15, ast=False, mcp=False, ephemeral=False)
    with patch("dotenv.main.find_dotenv", return_value=str(envfile)), \
         patch.object(env_mod, "_apply_model_args", lambda *a, **k: None, create=True):
        try:
            env_mod.setup_environment(args)
        except (SystemExit, Exception):  # noqa: BLE001 -- later steps need a real model config
            pass
    assert os.environ.get("ZIYA_TEST_DOTENV_MARKER") == "from-file"
    monkeypatch.delenv("ZIYA_TEST_DOTENV_MARKER", raising=False)

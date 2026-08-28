"""
ASR DP-02 — no wildcard CORS anywhere in the app.

``ErrorHandlingMiddleware`` set ``Access-Control-Allow-Origin: *`` on the SSE
error response. That was not cosmetic: the error handler is registered OUTSIDE
``CORSMiddleware`` (app/server.py), so its header is the one that actually
ships, and OriginGuard does not cover the read side. Any page the developer
visited could read sanitized exception text and internal paths.

Three more sites set the same wildcard on *success* streaming paths (the chat
response itself), which the finding did not list -- a strictly larger leak.
All four were dropped in favour of the single loopback ``allow_origin_regex``
already configured on ``CORSMiddleware``.

Rather than pin those four locations, this asserts the invariant: no code in
``app/`` emits that header at all. That also catches the next route to
reintroduce it.
"""

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.middleware.error_handling import ErrorHandlingMiddleware

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ACAO = "Access-Control-Allow-Origin"


# ---------------------------------------------------------------------------
# Functional: the error path the finding named
# ---------------------------------------------------------------------------

@pytest.fixture
def erroring_client():
    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("internal detail /Users/someone/secret/path")

    @app.get("/fine")
    async def fine():
        async def gen():
            yield "data: ok\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    app.add_middleware(ErrorHandlingMiddleware)
    return TestClient(app, raise_server_exceptions=False)


class TestErrorPathHeaders:
    def test_streaming_error_response_has_no_wildcard_origin(self, erroring_client):
        resp = erroring_client.get(
            "/boom", headers={"Accept": "text/event-stream"}
        )
        assert resp.status_code == 500
        assert resp.headers.get(ACAO) is None

    def test_streaming_error_response_still_streams(self, erroring_client):
        """Positive control: the handler still produces an SSE error body, so
        the header removal did not break error delivery."""
        resp = erroring_client.get(
            "/boom", headers={"Accept": "text/event-stream"}
        )
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "Error:" in resp.text

    def test_streaming_error_response_has_no_wildcard_headers_either(
        self, erroring_client
    ):
        assert erroring_client.get(
            "/boom", headers={"Accept": "text/event-stream"}
        ).headers.get("Access-Control-Allow-Headers") is None

    def test_json_error_response_has_no_wildcard_origin(self, erroring_client):
        resp = erroring_client.get("/boom")
        assert resp.status_code == 500
        assert resp.headers.get(ACAO) is None

    def test_error_detail_is_still_sanitized(self, erroring_client):
        """Guards the neighbouring control (CWE-209): dropping the CORS header
        must not have disturbed error sanitization on the same response."""
        resp = erroring_client.get("/boom")
        assert "/Users/someone/secret/path" not in resp.text

    def test_success_path_unaffected(self, erroring_client):
        resp = erroring_client.get("/fine")
        assert resp.status_code == 200
        assert resp.headers.get(ACAO) is None


# ---------------------------------------------------------------------------
# Invariant: nothing in app/ emits the header
# ---------------------------------------------------------------------------

def _acao_string_sites(path: Path):
    """Locations where the ACAO header name appears as a string *constant*.

    AST-based rather than textual so the explanatory comment left at the fix
    site is not mistaken for a reintroduction -- comments are not in the tree.
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []
    try:
        label = str(path.relative_to(APP_ROOT))
    except ValueError:
        label = path.name  # scanning a tmp file in the scanner's own tests
    return [
        f"{label}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == ACAO
    ]


class TestNoWildcardCorsInApp:
    def test_no_module_sets_the_header(self):
        offenders = []
        for path in sorted(APP_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            offenders.extend(_acao_string_sites(path))
        assert not offenders, (
            "CORS is owned by CORSMiddleware's loopback allow_origin_regex; a "
            "route or middleware setting Access-Control-Allow-Origin directly "
            "overrides it (ASR DP-02). Sites:\n  " + "\n  ".join(offenders)
        )

    def test_the_scan_detects_a_reintroduction(self, tmp_path):
        """Negative control -- an invariant that cannot fail proves nothing."""
        bad = tmp_path / "regression.py"
        bad.write_text(
            "def handler(response):\n"
            '    response.headers["Access-Control-Allow-Origin"] = "*"\n'
        )
        assert len(_acao_string_sites(bad)) == 1

    def test_comment_mentioning_the_header_is_not_flagged(self, tmp_path):
        ok = tmp_path / "commented.py"
        ok.write_text(
            "def handler():\n"
            "    # No Access-Control-Allow-Origin here: see ASR DP-02.\n"
            "    return None\n"
        )
        assert _acao_string_sites(ok) == []


class TestCorsPolicyStillConfigured:
    """The wildcards were removed *in favour of* the loopback regex.

    If CORS were deleted outright the tests above would also pass while the
    bundled UI and a dev server on another localhost port broke -- so pin that
    the replacement policy is present and is loopback-scoped.
    """

    @pytest.fixture(scope="class")
    def server_tree(self):
        return ast.parse((APP_ROOT / "server.py").read_text())

    def test_cors_middleware_uses_an_origin_regex(self, server_tree):
        regexes = [
            kw.value.value
            for node in ast.walk(server_tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "allow_origin_regex"
            and isinstance(kw.value, ast.Constant)
        ]
        assert regexes, "CORSMiddleware must still be configured with a regex"
        joined = " ".join(regexes)
        assert "localhost" in joined
        assert "127" in joined

    def test_credentials_are_not_allowed(self, server_tree):
        """A loopback regex plus allow_credentials=True would be worse than the
        wildcard it replaced."""
        values = [
            kw.value.value
            for node in ast.walk(server_tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "allow_credentials"
            and isinstance(kw.value, ast.Constant)
        ]
        assert values, "allow_credentials should be set explicitly"
        assert all(v is False for v in values)

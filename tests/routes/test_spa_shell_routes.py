"""Guard: client-side SPA routes must be served the app shell, not a 404.

React Router owns /print, /render etc., but the browser (and Playwright)
issues a real HTTP GET for those paths first.  app/routes/page_routes.py has
no catch-all, so every client-side route needs its own explicit server-side
shell passthrough.  When one is missing the backend returns 404 and the SPA
never boots.

For /print that surfaced as the badly misleading downstream error
"window.__renderConversation is not a function": the PDF exporter navigated
to /print, got a 404 JSON body (which reaches networkidle immediately, so the
wait looked successful), and then evaluated an injector that PrintRenderPage
had never been given the chance to define.

The default suite never caught it because the only test that drives the real
/print route is marked @pytest.mark.integration, which pytest.ini deselects
via addopts = -m "not integration".  This test is browser-free and always
runs, so adding a React route without its server passthrough fails here
instead of at PDF-export time.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# Client-side SPA routes that MUST be served the app shell by the backend.
# Keep in sync with the <Route path=...> list in frontend/src/index.tsx.
ROUTED_PATHS = [
    "/",
    "/render",        # DiagramRenderPage
    "/print",         # PrintRenderPage  (PDF export drives this headlessly)
    "/print-spike",   # PrintFeasibilitySpike
    "/info",          # SystemInfo
    "/debug",         # Debug  (NOT the legacy /debug1 or /debug2 routes)
]

# The subset that delegates to root().  root() catches a template-render
# failure and falls back to hardcoded HTML, so these are 200 even in a source
# checkout where app/templates/ exists but is empty (the built frontend is only
# copied in at package time).  /info renders the template through its own
# handler whose except returns a 500 JSONResponse with no such fallback, so it
# is 200 only when real templates are installed -- excluded from the strict
# check to keep this test environment-independent.
ROOT_DELEGATING_PATHS = [p for p in ROUTED_PATHS if p != "/info"]


@pytest.fixture
def client():
    from app.routes.page_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("path", ROUTED_PATHS)
def test_client_side_route_is_registered(client, path):
    """Each client-side route resolves server-side (never 404).

    This is the actual bug class: a missing passthrough yields 404, the SPA
    never boots, and the failure surfaces somewhere unrelated.  Asserting only
    "not 404" keeps this independent of whether a built frontend is present.
    """
    resp = client.get(path)
    assert resp.status_code != 404, (
        f"{path} returned 404 -- no server-side shell passthrough is "
        f"registered, so React Router never gets a chance to route it."
    )


@pytest.mark.parametrize("path", ROOT_DELEGATING_PATHS)
def test_shell_route_serves_a_page(client, path):
    """Routes that delegate to root() must return a page, not an error.

    root() has a hardcoded-HTML fallback, so a non-200 here means the route
    itself is broken rather than the templates being absent.
    """
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_print_route_serves_same_shell_as_render(client):
    """/print must serve the identical shell as the proven /render route.

    /render is the working precedent (DiagramRenderPage).  Asserting parity
    catches a /print handler that resolves but serves the wrong body -- a 200
    alone is not enough, since the exporter needs the full app shell for
    PrintRenderPage to mount and define window.__renderConversation.
    """
    render_body = client.get("/render").text
    print_body = client.get("/print").text
    assert print_body == render_body, (
        "/print served a different document than /render; the PDF exporter "
        "needs the full app shell for PrintRenderPage to mount."
    )

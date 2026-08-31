"""Seam guard: /api/export/to-target hands an INT server_port to every consumer.

The handler reads ``ziya_env("ZIYA_PORT")`` (registry-typed int, so already
coerced) and re-derives a fallback int.  It previously kept TWO names for the
value (``port`` raw, ``port_int`` derived) and only one of its three
downstream calls used the derived one — the other two passed ``port``, which
was only an int by virtue of the registry coercion.  The fix unified the
naming (rebind ``port``, matching /rendered, /pdf and /document); this guard
pins the resulting invariant so a future edit to the plumbing (a registry
type change, a new call site, a reintroduced second variable) cannot silently
hand a str/None port to the headless renderer's ``http://localhost:{port}``
base URL.

NOTE (attribution): this is a GUARD, not a bug certification — the registry
coercion means the pre-fix code produced identical runtime values, so this
test passes both before and after the naming fix.  Its value is catching the
class of future break, exercised through the worst input (garbage ZIYA_PORT,
forcing the fallback chain end-to-end through the real handler).
"""
import pytest

from app.routes.export_routes import export_to_target, PluginExportRequest


class _FakeProvider:
    provider_id = "fake"

    def get_target_info(self):
        return {"id": "fake-target"}

    async def export(self, content, format_type, metadata, images=None):
        return {"success": True}


@pytest.fixture
def plumbing(monkeypatch):
    """Stub every downstream consumer, capturing the server_port each receives."""
    import app.plugins as plugins
    import app.utils.conversation_exporter as ce
    import app.services.html_exporter as he
    from app.agents.models import ModelManager

    seen = {}

    async def fake_rendered(**kw):
        seen["rendered"] = kw.get("server_port")
        return {"content": "x", "diagrams_count": 0}

    async def fake_diagrams(messages, theme="light", format="png", server_port=None):
        seen["diagrams"] = server_port
        return {}

    async def fake_html(messages, **kw):
        seen["html"] = kw.get("server_port")
        return {"content": "<html></html>", "mode": "python", "fidelity": "fallback"}

    monkeypatch.setattr(ce, "export_conversation_rendered", fake_rendered)
    monkeypatch.setattr(ce, "render_diagrams_server_side", fake_diagrams)
    monkeypatch.setattr(he, "export_conversation_html", fake_html)
    monkeypatch.setattr(plugins, "get_export_providers", lambda: [_FakeProvider()])
    monkeypatch.setattr(ModelManager, "get_model_alias", lambda: "test-model")
    # Garbage port forces the int-fallback chain end-to-end (ziya_env fallback
    # AND the handler's own int() guard) rather than testing the happy path.
    monkeypatch.setenv("ZIYA_PORT", "not-a-number")
    return seen


async def test_markdown_branch_passes_int_port_everywhere(plumbing):
    resp = await export_to_target(PluginExportRequest(
        messages=[{"role": "human", "content": "hi"}],
        target_id="fake-target", format="markdown",
    ))
    # Positive control: the path actually ran (provider export succeeded).
    assert isinstance(resp, dict) and resp.get("success") is True
    assert plumbing["rendered"] == 6969
    assert type(plumbing["rendered"]) is int
    assert plumbing["diagrams"] == 6969
    assert type(plumbing["diagrams"]) is int


async def test_html_branch_passes_int_port_everywhere(plumbing):
    resp = await export_to_target(PluginExportRequest(
        messages=[{"role": "human", "content": "hi"}],
        target_id="fake-target", format="html",
    ))
    assert isinstance(resp, dict) and resp.get("success") is True
    assert plumbing["html"] == 6969
    assert type(plumbing["html"]) is int
    # The separate diagram-image render also runs on the html branch.
    assert plumbing["diagrams"] == 6969
    assert type(plumbing["diagrams"]) is int

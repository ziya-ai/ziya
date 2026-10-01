"""
A host without Playwright's Chromium must TELL the model so, and must say
whether the gap is fixable.

Observed on an AL2 dev desktop (glibc 2.26): builtin_tools correctly
registered no render_diagram, yet the model called it session after session;
asked for a PDF, it hit a 500 from the exporter (package-only gate), spent six
tool calls diagnosing it, and then advised ``playwright install chromium`` and
``install-deps`` -- which can never succeed on that glibc.  Three surfaces now
resolve through optional_features, the same probe as the registration gate and
the startup banner: the chat prompt, the task-scope floor, and the exporter.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.utils import optional_features as of
from app.utils.task_tool_floor import (
    ALWAYS_AVAILABLE_TOOLS, always_available_tools, effective_tool_names,
)

ROOT = Path(__file__).resolve().parents[1]
MISSING = "Chromium browser for Playwright"
BLOCKED = "glibc 2.26 (Playwright's Chromium needs glibc 2.28 or newer)"


def _missing():
    return patch.object(of, "playwright_status", return_value=MISSING)


def _present():
    return patch.object(of, "playwright_status", return_value=None)


def _blocked(reason=BLOCKED):
    return patch.object(of, "chromium_blocked_reason", return_value=reason)


class TestBlockedReason:
    @pytest.mark.parametrize("ver,expect", [
        ("2.26", True), ("2.27", True), ("2.28", False), ("2.34", False),
    ])
    def test_glibc_threshold_matches_the_installer(self, ver, expect):
        with patch("sys.platform", "linux"), \
             patch("platform.libc_ver", return_value=("glibc", ver)):
            reason = of.chromium_blocked_reason()
        assert (reason is not None) is expect, reason
        if expect:
            assert ver in reason and "2.28" in reason

    def test_non_linux_is_never_blocked(self):
        with patch("sys.platform", "darwin"):
            assert of.chromium_blocked_reason() is None

    def test_rule_mirrors_the_shell_script(self):
        # Seam: install_extras.sh decides PW_BLOCKED with the same threshold.
        src = (ROOT / "app" / "scripts" / "install_extras.sh").read_text()
        assert 'GLIBC_MIN" -lt 28' in src


class TestPromptStatement:
    def test_silent_when_chromium_present(self):
        with _present():
            assert of.install_limitations_prompt() == ""

    def test_names_render_tools_pdf_export_and_forbids_calling(self):
        with _missing(), _blocked(None):
            text = of.install_limitations_prompt()
        assert MISSING in text
        for tool in of.RENDER_TOOLS:
            assert f"`{tool}`" in text, tool
        assert "do not call them" in text
        assert "PDF export" in text and "/api/export/document" in text
        assert "skill" in text and "memory" in text          # the sources it must override

    def test_installable_host_names_the_installer(self):
        with _missing(), _blocked(None):
            text = of.install_limitations_prompt()
        assert f"{of.INSTALL_CMD} --browser" in text
        assert "platform limit" not in text

    def test_blocked_host_forbids_every_install_suggestion(self):
        # The AL2 transcript: the model recommended playwright install
        # chromium + install-deps. The prompt must rule those out by name.
        with _missing(), _blocked():
            text = of.install_limitations_prompt()
        assert "platform limit" in text and BLOCKED in text
        assert "Do not suggest `playwright install chromium`" in text
        assert "install-deps" in text
        assert "newer host" in text
        # The installer command appears ONLY inside the "do not suggest" clause.
        assert text.count(f"{of.INSTALL_CMD} --browser") == 1
        assert "Do not suggest" in text.split(f"{of.INSTALL_CMD} --browser")[0]

    def test_no_braces_for_the_chat_prompt_template(self):
        for blocked in (None, BLOCKED):
            with _missing(), _blocked(blocked):
                assert not re.search(r"[{}]", of.install_limitations_prompt())

    def test_render_tools_constant_matches_registration(self):
        src = (ROOT / "app" / "mcp" / "builtin_tools.py").read_text()
        for tool_cls in ("RenderDiagramTool", "RecallImageTool", "ViewImageTool"):
            assert tool_cls in src
        assert set(of.RENDER_TOOLS) == {"render_diagram", "recall_image", "view_image"}

    def test_chat_prompt_appends_it(self):
        src = (ROOT / "app" / "agents" / "prompts.py").read_text()
        assert "template += install_limitations_prompt()" in src
        assert src.index("install_limitations_prompt()") < src.index("original_template = template")


class TestTaskFloorTracksTheInstall:
    def test_floor_drops_render_tools_when_chromium_missing(self):
        with _missing():
            floor = always_available_tools()
        assert not (floor & set(of.RENDER_TOOLS)), floor
        assert "emit_artifact" in floor and "bead_create" in floor

    def test_floor_is_complete_when_chromium_present(self):
        with _present():
            assert always_available_tools() == set(ALWAYS_AVAILABLE_TOOLS)

    def test_effective_names_follow_the_floor(self):
        with _missing():
            eff = effective_tool_names(["file_read"])
        assert "file_read" in eff and "emit_artifact" in eff
        assert not (eff & set(of.RENDER_TOOLS))

    def test_task_prompt_no_longer_claims_render_diagram(self):
        from app.utils.session_context_prompt import _format_tools_section
        scope = MagicMock()
        scope.tools, scope.skills, scope.paths, scope.shell_commands = ["file_read"], [], [], []
        with _missing():
            text = "\n".join(_format_tools_section(scope))
        assert "Always available regardless of scope" in text     # positive: section rendered
        assert "emit_artifact" in text
        assert "render_diagram" not in text and "recall_image" not in text


class TestPdfExporterGate:
    def _reset(self):
        import app.services.pdf_exporter as pe
        pe._playwright_available = None
        return pe

    @pytest.mark.asyncio
    async def test_package_without_browser_is_a_501_not_a_500(self):
        # The observed failure: driver launched, browser absent, "Connection
        # closed while reading from the driver" as a 500. Must be the
        # ImportError the routes map to 501, with the real remedy.
        pe = self._reset()
        with patch.object(of, "playwright_package_available", return_value=True), \
             patch.object(of, "chromium_browser_available", return_value=False), \
             _blocked(None), pytest.raises(ImportError) as exc:
            await pe.ConversationRenderSession.create()
        assert f"{of.INSTALL_CMD} --browser" in str(exc.value)
        assert "pip install playwright" not in str(exc.value)
        self._reset()

    @pytest.mark.asyncio
    async def test_blocked_host_says_so_instead_of_an_install_recipe(self):
        pe = self._reset()
        with patch.object(of, "playwright_package_available", return_value=True), \
             patch.object(of, "chromium_browser_available", return_value=False), \
             _blocked(), pytest.raises(ImportError) as exc:
            await pe.ConversationRenderSession.create()
        assert BLOCKED in str(exc.value)
        assert "install" not in str(exc.value).split(BLOCKED)[1]   # no remedy after the limit
        self._reset()

    def test_route_maps_import_error_to_501(self):
        # Seam: the exporter raises ImportError; both export routes must still
        # translate it to 501 (a refactor to another exception type would
        # silently demote this to the generic 500 again).
        src = (ROOT / "app" / "routes" / "export_routes.py").read_text()
        assert src.count("except ImportError as exc:") >= 2
        assert src.count("status_code=501") >= 2

"""The macOS drag helper's decision logic (app/utils/drag_pasteboard_helper.py).

The AppKit poll loop cannot run under pytest; what matters for correctness
is *which* drags get rewritten and *what* the rewritten item contains, and
that is all pure.  The pasteboard shapes below are copied from the probe
output in the design conversation (scripts/drag_pasteboard_probe.swift):
a Ziya DownloadURL drag carries public.html + utf8 text and the renderer
origin, and no public.png or public.file-url.
"""
import base64
import os
import time
from pathlib import Path

import pytest

from app.utils import drag_pasteboard_helper as h

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
ORIGIN = "http://localhost:6969"


def ziya_drag(html: str, **extra) -> dict:
    types = {
        "org.chromium.chromium-initiated-drag": b"",
        "org.chromium.chromium-renderer-initiated-drag": ORIGIN.encode(),
        "com.apple.pasteboard.promised-file-url": b"",
        "com.apple.pasteboard.promised-file-content-type": b"public.png",
        "public.html": html.encode(),
        "public.utf8-plain-text": b"| a | b |",
        "org.chromium.web-custom-data": b"pchromium/x-drag-id 7A1B",
        "Apple HTML pasteboard type": b"<p>legacy</p>",
    }
    types.update(extra)
    return types


# ── extract / strip ────────────────────────────────────────────────────

def test_extract_prefers_carrier_comment_then_img_src():
    html = f"<!--ziya-drag-png:{DATA_URL}--><img src=\"data:image/png;base64,QUJD\">"
    assert h.extract_png_data_url(html) == DATA_URL
    assert h.extract_png_data_url(f'<img alt="x" src="{DATA_URL}">') == DATA_URL
    assert h.extract_png_data_url(html.encode()) == DATA_URL
    assert h.extract_png_data_url("<table><td>1</td></table>") is None
    assert h.extract_png_data_url(None) is None
    assert h.decode_data_url(DATA_URL) == PNG


def test_strip_png_comment_leaves_the_table():
    html = f"<meta charset='utf-8'><!--ziya-drag-png:{DATA_URL}--><table><td>1</td></table>"
    assert h.strip_png_comment(html) == "<meta charset='utf-8'><table><td>1</td></table>"


# ── plan_rewrite ───────────────────────────────────────────────────────

def test_rewrites_a_ziya_table_drag_into_a_file_drag(tmp_path):
    png_path = tmp_path / "out.png"
    html = f"<!--ziya-drag-png:{DATA_URL}--><table><td>1</td></table>"
    plan = h.plan_rewrite(ziya_drag(html), ORIGIN, png_path)
    assert plan is not None
    assert plan["png"] == PNG
    t = plan["types"]
    # The two things Slack needs: a real file URL, and no Chromium markers
    # (a Chromium receiver strips filenames when it sees them).
    assert t["public.file-url"] == png_path.resolve().as_uri().encode()
    assert t["public.png"] == PNG
    assert not any(k.startswith("org.chromium.") for k in t)
    # Legacy non-UTI names are dropped (setData:forType: rejects them).
    assert "Apple HTML pasteboard type" not in t
    # Everything else survives, and the html is the table alone.
    assert t["public.utf8-plain-text"] == b"| a | b |"
    assert t["public.html"] == b"<table><td>1</td></table>"
    assert t["com.apple.pasteboard.promised-file-content-type"] == b"public.png"


def test_accepts_a_set_of_armed_origins_and_trailing_slash(tmp_path):
    html = f"<!--ziya-drag-png:{DATA_URL}-->"
    assert h.plan_rewrite(ziya_drag(html), {"http://127.0.0.1:1", ORIGIN + "/"}, tmp_path / "p.png")
    assert h.plan_rewrite(ziya_drag(html), [], tmp_path / "p.png") is None


def test_uses_existing_public_png_bytes_when_chromium_already_has_them(tmp_path):
    # An <img> drag already carries public.png; no html carrier needed.
    plan = h.plan_rewrite(ziya_drag("<img src='x'>", **{"public.png": PNG}), ORIGIN, tmp_path / "p.png")
    assert plan and plan["png"] == PNG


@pytest.mark.parametrize("mutate", [
    # Not a Chromium renderer drag at all (Finder).
    lambda t: {"public.file-url": b"file:///x.png", "com.apple.finder.node": b""},
    # Another site's drag.
    lambda t: {**t, "org.chromium.chromium-renderer-initiated-drag": b"chrome://new-tab-page"},
    # Already a file drag: nothing to do.
    lambda t: {**t, "public.file-url": b"file:///already.png"},
    # Ours, but the PNG was not ready (SVG fallback): no file to offer.
    lambda t: {**t, "public.html": b"<table></table>"},
])
def test_leaves_other_drags_alone(tmp_path, mutate):
    html = f"<!--ziya-drag-png:{DATA_URL}-->"
    assert h.plan_rewrite(mutate(ziya_drag(html)), ORIGIN, tmp_path / "p.png") is None


# ── named files (a .md dragged from a file-operation header) ────────────

MD = "# Notes\n\nhello\n".encode()
MD_URL = "data:text/markdown;base64," + base64.b64encode(MD).decode()


def test_file_carrier_rewrites_into_a_named_file_drag(tmp_path):
    html = f"<!--ziya-drag-file:design/notes.md:{MD_URL}--><span>notes.md</span>"
    payload = h.extract_drag_payload(ziya_drag(html))
    assert payload == {"data": MD, "filename": "notes.md", "mime": "text/markdown"}
    dest = h.dest_path_for(tmp_path, payload)
    assert dest.name == "notes.md" and dest.parent.parent == tmp_path
    assert dest.parent.name.startswith("ziya-drag-")
    plan = h.plan_rewrite(ziya_drag(html), ORIGIN, dest)
    assert plan is not None and plan["data"] == MD and plan["filename"] == "notes.md"
    t = plan["types"]
    assert t["public.file-url"] == dest.resolve().as_uri().encode()
    assert "public.png" not in t                       # not an image
    assert not any(k.startswith("org.chromium.") for k in t)
    assert t["public.html"] == b"<span>notes.md</span>"  # carrier stripped
    # Two drags of the same name never collide.
    assert h.dest_path_for(tmp_path, payload) != dest


@pytest.mark.parametrize("name,expected", [
    ("../../etc/passwd", "passwd"), ("a/b/c.md", "c.md"), ("..", "file"),
    ('we<ird>:"name".md', "weirdname.md"),
])
def test_carrier_filename_is_a_safe_basename(name, expected):
    assert h.safe_filename(name) == expected


def test_png_payload_still_takes_the_png_shape(tmp_path):
    html = f"<!--ziya-drag-png:{DATA_URL}-->"
    payload = h.extract_drag_payload(ziya_drag(html))
    assert payload == {"data": PNG, "filename": None, "mime": "image/png"}
    assert h.dest_path_for(tmp_path, payload).suffix == ".png"
    assert h.extract_drag_payload(ziya_drag("<table></table>")) is None


def test_cleanup_removes_stale_named_file_dirs_too(tmp_path):
    d = h.dest_path_for(tmp_path, {"filename": "x.md"})
    d.write_bytes(MD)
    os.utime(d.parent, (time.time() - 3600, time.time() - 3600))
    removed = h.cleanup_stale(tmp_path, max_age_s=600)
    assert removed == [d.parent] and not d.parent.exists()


# ── two servers, two helpers, one system pasteboard ─────────────────────

def test_two_servers_helpers_do_not_interfere(tmp_path):
    """Each Ziya server spawns its own helper (one uvicorn.run, no workers),
    armed only with its own origin.  A drag from server A must be rewritten
    by A's helper alone, and the REWRITTEN item must be left alone by both
    helpers -- no second rewrite, no ping-pong between the two pollers."""
    html = f"<!--ziya-drag-png:{DATA_URL}-->"
    drag_from_a = ziya_drag(html)                     # origin :6969
    a_origins = {ORIGIN}
    b_origins = {"http://localhost:7000"}

    assert h.plan_rewrite(drag_from_a, b_origins, tmp_path / "b.png") is None
    plan_a = h.plan_rewrite(drag_from_a, a_origins, tmp_path / "a.png")
    assert plan_a is not None

    # What is on the pasteboard after A's helper writes it back.
    rewritten = dict(plan_a["types"])
    assert h.plan_rewrite(rewritten, a_origins, tmp_path / "a2.png") is None
    assert h.plan_rewrite(rewritten, b_origins, tmp_path / "b2.png") is None

    # And B's helper, mid-write of A's clearContents, sees an empty board.
    assert h.plan_rewrite({}, a_origins | b_origins, tmp_path / "x.png") is None


# ── scratch files ──────────────────────────────────────────────────────

def test_cleanup_removes_only_stale_helper_pngs(tmp_path):
    old = h.new_png_path(tmp_path)
    old.write_bytes(PNG)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    fresh = h.new_png_path(tmp_path)
    fresh.write_bytes(PNG)
    other = tmp_path / "keep.png"
    other.write_bytes(PNG)
    os.utime(other, (time.time() - 3600, time.time() - 3600))
    removed = h.cleanup_stale(tmp_path, max_age_s=600)
    assert removed == [old]
    assert fresh.exists() and other.exists()
    assert h.cleanup_stale(tmp_path / "missing") == []


# ── availability + supervisor ──────────────────────────────────────────

def test_not_supported_off_mac_or_when_disabled(monkeypatch):
    monkeypatch.setattr(h.sys, "platform", "linux")
    assert h.is_supported() is False
    monkeypatch.setattr(h.sys, "platform", "darwin")
    monkeypatch.setenv("ZIYA_NATIVE_DRAG", "0")
    assert h.is_supported() is False


def test_supervisor_arm_writes_a_line_and_refuses_injection(monkeypatch):
    sup = h.HelperSupervisor()

    class FakeStdin:
        def __init__(self): self.buf = ""
        def write(self, s): self.buf += s
        def flush(self): pass
        def close(self): pass

    class FakeProc:
        stdin = FakeStdin()
        def poll(self): return None
        def wait(self, timeout=None): return 0

    sup._proc = FakeProc()
    assert sup.arm(ORIGIN) is True
    assert sup._proc.stdin.buf == f"arm {ORIGIN}\n"
    assert sup.arm("http://x\nquit") is False
    assert sup.arm("") is False
    sup.stop()
    assert sup.running is False


def test_supervisor_does_not_start_when_unsupported(monkeypatch):
    monkeypatch.setattr(h, "is_supported", lambda: False)
    sup = h.HelperSupervisor()
    assert sup.start() is False
    assert sup.arm(ORIGIN) is False
    assert sup.running is False

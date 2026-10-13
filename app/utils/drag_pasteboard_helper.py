"""
macOS native drag helper: make a Ziya diagram/table drag land in Slack.

THE PROBLEM
-----------
A web page cannot put a file on a drag.  Chromium publishes a diagram drag
as ``DownloadURL`` (a file *promise*), ``public.png`` bytes and
``text/html``; Finder accepts the promise, but Slack desktop -- an Electron
(Chromium) receiver -- accepts a drop only when the pasteboard carries a real
``public.file-url``, and Chromium refuses to let a page write one.  Worse,
when a Chromium receiver sees ``org.chromium.chromium-renderer-initiated-drag``
on the pasteboard it clears every filename before the page sees the drop
(``RenderWidgetHostImpl::FilterDropData``), so a file URL added alongside
Chrome's own types is discarded on arrival.

THE ROUTE (confirmed by scripts/drag_pasteboard_inject.swift)
-------------------------------------------------------------
The drag pasteboard is system-wide, so a second local process can rewrite
it.  While a drag from Ziya's own origin is live, this helper re-declares
the pasteboard item WITHOUT the ``org.chromium.*`` marker types and WITH a
``public.file-url`` pointing at a PNG it wrote under ``~/.ziya/tmp/drag/``.
Slack then uploads that file.

The PNG itself is read off the pasteboard: the frontend embeds the data URL
in the ``text/html`` payload as an ``<!--ziya-drag-png:...-->`` comment (and
Chromium hands that over as ``public.html``), so the helper needs no
side-channel with the browser beyond one "arm" message.

PROCESS SHAPE
-------------
* ``main()`` is the helper process: a pyobjc ``NSPasteboard`` poll loop,
  spawned by the server on macOS when ``pyobjc-framework-Cocoa`` is
  installed (``ziya-install-extras --drag`` / ``pip install ziya[drag]``).
  It idles at 50 ms and tightens to 2 ms for a few seconds after an
  ``arm <origin>`` line on stdin, which the frontend triggers on pointerdown.
  It exits when stdin closes (the server went away).
* ``HelperSupervisor`` is the parent-side handle: spawn, arm, stop.
* ``plan_rewrite`` / ``extract_png_data_url`` are the pure decision
  functions and are what the tests exercise; nothing in them imports AppKit.

Only drags whose renderer origin equals an origin the frontend armed are
touched; every other drag on the machine passes through untouched.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Union

from app.utils.logging_utils import logger

RENDERER_ORIGIN_TYPE = "org.chromium.chromium-renderer-initiated-drag"
CHROMIUM_TYPE_PREFIX = "org.chromium."
FILE_URL_TYPE = "public.file-url"
PNG_TYPE = "public.png"
HTML_TYPE = "public.html"

# How long one ``arm`` keeps the fast poll going.  A drag starts within a
# few hundred ms of the pointerdown that armed it; 5 s is generous.
ARM_WINDOW_S = 5.0
FAST_POLL_S = 0.002
IDLE_POLL_S = 0.05
# PNGs the helper wrote are only needed for the duration of one drag.
STALE_PNG_S = 600

TypeMap = Dict[str, Union[bytes, str]]

_PNG_COMMENT_RE = re.compile(
    r"<!--ziya-drag-png:(data:image/png;base64,[A-Za-z0-9+/=]+)-->")
# A named file (a .md the model wrote, a .csv it read): the frontend's
# fileDrag.ts carrier.  Name first so the helper can keep it — Slack shows
# the dropped file's name.
_FILE_COMMENT_RE = re.compile(
    r"<!--ziya-drag-file:([^:<>]{1,255}):(data:[A-Za-z0-9.+/-]+;base64,[A-Za-z0-9+/=]+)-->")
_IMG_SRC_RE = re.compile(
    r"""<img[^>]*\ssrc=["'](data:image/png;base64,[A-Za-z0-9+/=]+)["']""")


def _text(v: Union[bytes, str, None]) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def extract_png_data_url(html: Union[bytes, str, None]) -> Optional[str]:
    """The PNG data URL the frontend embedded in a drag's text/html.

    Prefers the explicit ``<!--ziya-drag-png:...-->`` carrier; falls back to
    the ``<img src="data:image/png;base64,...">`` the diagram drag already
    publishes for rich-text targets.  None when neither is present.
    """
    text = _text(html)
    if not text:
        return None
    m = _PNG_COMMENT_RE.search(text) or _IMG_SRC_RE.search(text)
    return m.group(1) if m else None


def safe_filename(name: str) -> str:
    """The basename of a carrier filename, with path separators and control
    characters removed; ``file`` when nothing usable is left."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable() and c not in '<>:"|?*')
    base = base.strip(" .")
    return base[:200] or "file"


def extract_drag_payload(types: TypeMap) -> Optional[dict]:
    """What the drag wants delivered as a file.

    Returns ``{"data": bytes, "filename": str | None, "mime": str}`` from, in
    order: a ``<!--ziya-drag-file:name:data:...-->`` carrier, ``public.png``
    bytes Chromium already put on the item, or the PNG carrier / ``<img>``
    the diagram and table drags publish.  None when there is nothing.
    """
    html = _text(types.get(HTML_TYPE))
    m = _FILE_COMMENT_RE.search(html) if html else None
    if m:
        data_url = m.group(2)
        try:
            data = decode_data_url(data_url)
        except (ValueError, TypeError):
            data = b""
        if data:
            mime = data_url[5:data_url.index(";")]
            return {"data": data, "filename": safe_filename(m.group(1)), "mime": mime}
    raw_png = types.get(PNG_TYPE)
    if isinstance(raw_png, (bytes, bytearray)) and len(raw_png) > 0:
        return {"data": bytes(raw_png), "filename": None, "mime": "image/png"}
    data_url = extract_png_data_url(html)
    if data_url:
        try:
            data = decode_data_url(data_url)
        except (ValueError, TypeError):
            return None
        if data:
            return {"data": data, "filename": None, "mime": "image/png"}
    return None


def strip_png_comment(html: Union[bytes, str]) -> str:
    """text/html with the carrier comments removed, so the rewritten item
    hands rich-text targets the same markup they would have had anyway."""
    return _FILE_COMMENT_RE.sub("", _PNG_COMMENT_RE.sub("", _text(html)))


def decode_data_url(data_url: str) -> bytes:
    """Bytes of a base64 ``data:`` URL."""
    comma = data_url.find(",")
    if comma < 0:
        raise ValueError("not a data URL")
    return base64.b64decode(data_url[comma + 1:])


def _is_droppable_type(name: str) -> bool:
    """Types that must NOT be copied onto the rewritten item.

    ``org.chromium.*`` are the markers that make a Chromium receiver strip
    filenames.  Names containing a space are legacy pre-UTI pasteboard types
    (``Apple HTML pasteboard type``, ``NSStringPboardType`` has none but the
    others do); ``NSPasteboardItem.setData:forType:`` rejects them and they
    carry nothing a modern receiver reads.
    """
    return name.startswith(CHROMIUM_TYPE_PREFIX) or " " in name


def _origin_of(types: TypeMap) -> Optional[str]:
    if RENDERER_ORIGIN_TYPE not in types:
        return None
    return _text(types[RENDERER_ORIGIN_TYPE]).strip().rstrip("/")


def plan_rewrite(types: TypeMap, own_origins: Union[str, Iterable[str]],
                 png_path: Union[str, Path]) -> Optional[dict]:
    """Decide whether, and how, to re-declare a drag pasteboard.

    ``types`` is the live item's ``{uti: data}``.  Returns None when the
    drag is not ours (no renderer origin, or an origin we did not arm),
    already carries a file URL, or carries no PNG we can extract.
    Otherwise returns ``{"types": {uti: bytes}, "png": bytes}``: the item to
    write (Chromium markers and legacy names dropped, ``public.file-url`` and
    ``public.png`` added) and the PNG bytes to store at ``png_path`` first.
    """
    if isinstance(own_origins, str):
        own_origins = [own_origins]
    allowed = {o.strip().rstrip("/") for o in own_origins if o}
    origin = _origin_of(types)
    if not origin or origin not in allowed:
        return None
    if FILE_URL_TYPE in types:
        return None

    payload = extract_drag_payload(types)
    if payload is None:
        return None
    data: bytes = payload["data"]

    out: Dict[str, bytes] = {}
    for name, value in types.items():
        if _is_droppable_type(name):
            continue
        if name == HTML_TYPE:
            out[name] = strip_png_comment(value).encode("utf-8")
        elif isinstance(value, str):
            out[name] = value.encode("utf-8")
        else:
            out[name] = bytes(value)
    if payload["mime"] == "image/png":
        out[PNG_TYPE] = data
    out[FILE_URL_TYPE] = Path(png_path).resolve().as_uri().encode("utf-8")
    # ``png`` kept as an alias of ``data`` for callers written against the
    # PNG-only shape.
    return {"types": out, "data": data, "png": data,
            "filename": payload["filename"], "mime": payload["mime"]}


def dest_path_for(directory: Union[str, Path], payload: Optional[dict]) -> Path:
    """Where a drag's file goes: a named carrier keeps its name inside a
    fresh per-drag subdirectory (two drags of README.md must not collide,
    and Slack shows the name); anything else is a uuid PNG."""
    d = Path(directory)
    name = payload.get("filename") if payload else None
    if name:
        sub = d / f"ziya-drag-{uuid.uuid4().hex}"
        sub.mkdir(parents=True, exist_ok=True)
        return sub / name
    return new_png_path(d)


# ── Scratch files ───────────────────────────────────────────────────────

def drag_dir() -> Path:
    from app.utils.paths import get_ziya_home
    return Path(get_ziya_home()) / "tmp" / "drag"


def new_png_path(directory: Union[str, Path]) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"ziya-drag-{uuid.uuid4().hex}.png"


def cleanup_stale(directory: Union[str, Path], max_age_s: float = STALE_PNG_S,
                  now: Optional[float] = None) -> List[Path]:
    """Delete helper PNGs older than ``max_age_s``; returns what was removed."""
    d = Path(directory)
    if not d.is_dir():
        return []
    now = time.time() if now is None else now
    removed: List[Path] = []
    for p in d.glob("ziya-drag-*"):
        try:
            if now - p.stat().st_mtime <= max_age_s:
                continue
            if p.is_dir():
                for child in p.iterdir():
                    child.unlink()
                p.rmdir()
            else:
                p.unlink()
            removed.append(p)
        except OSError:
            continue
    return removed


# ── Availability ────────────────────────────────────────────────────────

def is_supported() -> bool:
    """macOS, pyobjc installed, not disabled with ZIYA_NATIVE_DRAG=0."""
    if sys.platform != "darwin":
        return False
    if os.environ.get("ZIYA_NATIVE_DRAG", "").strip() == "0":
        return False
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return False
    return True


# ── Helper process (runs under pyobjc) ──────────────────────────────────

def _read_types(pb) -> TypeMap:
    out: TypeMap = {}
    for t in pb.types() or []:
        name = str(t)
        data = pb.dataForType_(name)
        out[name] = bytes(data) if data is not None else b""
    return out


def _write_item(pb, types: Dict[str, bytes]) -> bool:
    import AppKit
    from Foundation import NSData
    item = AppKit.NSPasteboardItem.alloc().init()
    for name, data in types.items():
        try:
            item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), name)
        except Exception as e:  # noqa: BLE001 -- one bad type must not sink the drag
            logger.debug(f"drag helper: could not set {name}: {e}")
    pb.clearContents()
    return bool(pb.writeObjects_([item]))


def _stdin_reader(lines: "list[str]", lock: threading.Lock, closed: threading.Event) -> None:
    for line in sys.stdin:
        with lock:
            lines.append(line.rstrip("\n"))
    closed.set()


def main(argv: Optional[List[str]] = None) -> int:
    """Poll the drag pasteboard and rewrite Ziya's own drags.  Exits when
    stdin closes."""
    import AppKit
    pb = AppKit.NSPasteboard.pasteboardWithName_(AppKit.NSPasteboardNameDrag)
    scratch = drag_dir()
    origins: Set[str] = set()
    armed_until = 0.0
    last = pb.changeCount()

    lines: List[str] = []
    lock = threading.Lock()
    closed = threading.Event()
    threading.Thread(target=_stdin_reader, args=(lines, lock, closed), daemon=True).start()
    print("ready", flush=True)

    while not closed.is_set():
        with lock:
            pending, lines[:] = list(lines), []
        for line in pending:
            cmd, _, arg = line.strip().partition(" ")
            if cmd == "arm" and arg:
                origins.add(arg.strip().rstrip("/"))
                armed_until = time.time() + ARM_WINDOW_S
                cleanup_stale(scratch)
            elif cmd == "quit":
                return 0

        count = pb.changeCount()
        if count != last:
            last = count
            try:
                types = _read_types(pb)
                path = dest_path_for(scratch, extract_drag_payload(types))
                plan = plan_rewrite(types, origins, path)
                if plan:
                    path.write_bytes(plan["data"])
                    ok = _write_item(pb, plan["types"])
                    last = pb.changeCount()
                    print(f"rewrote {path.name} ok={ok}", flush=True)
            except Exception as e:  # noqa: BLE001 -- never die mid-drag
                logger.debug(f"drag helper: rewrite failed: {e}")

        time.sleep(FAST_POLL_S if time.time() < armed_until else IDLE_POLL_S)
    return 0


# ── Parent side ─────────────────────────────────────────────────────────

class HelperSupervisor:
    """Spawn, arm and stop the helper process from the server."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if not is_supported():
            return False
        with self._lock:
            if self.running:
                return True
            try:
                self._proc = subprocess.Popen(
                    [sys.executable, "-m", "app.utils.drag_pasteboard_helper"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, text=True,
                )
            except OSError as e:
                logger.warning(f"native drag helper not started: {e}")
                self._proc = None
                return False
        logger.info("🖼  native drag helper started (macOS; drops into Slack desktop)")
        return True

    def arm(self, origin: str) -> bool:
        """Tell the helper a drag from ``origin`` is about to start."""
        if not origin or "\n" in origin:
            return False
        if not self.running and not self.start():
            return False
        try:
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(f"arm {origin}\n")
            self._proc.stdin.flush()
            return True
        except (OSError, ValueError, AssertionError) as e:
            logger.debug(f"native drag helper arm failed: {e}")
            return False

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()


_supervisor: Optional[HelperSupervisor] = None


def get_supervisor() -> HelperSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = HelperSupervisor()
    return _supervisor


if __name__ == "__main__":
    sys.exit(main())

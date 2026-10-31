#!/usr/bin/env python3
"""Make the diagram fix-up passes report rewrites through console.info.

The production bundle strips console.log (frontend/craco.config.js).  The
headless renderer behind ``render_diagram`` loads /render from that bundle
and reads ``🔧 TAG: ...`` console lines to tell the model how preprocessing
changed its spec (app/utils/chart_rewrites.py, which accepts [log], [info]
and [debug]).  Stripping console.log silenced those notes; console.info
survives the strip.

Only calls whose first argument is a string literal starting with 🔧 are
rewritten, and only in the diagram code that runs in /render.  Other 🔧
logs (chat, sync, sidebar) are not rewrite notes and stay stripped.

Usage:
    python3 scripts/console_info_rewrites.py            # dry run: counts per file
    python3 scripts/console_info_rewrites.py --apply    # rewrite in place
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCOPE = (
    "frontend/src/plugins/d3",
    "frontend/src/components/D3Renderer.tsx",
    "frontend/src/utils/diagramUtils.ts",
)
# console.log( + optional whitespace (newlines included) + a quote + 🔧
PATTERN = re.compile(r"console\.log(\(\s*[`'\"]🔧)")


def scope_files(root: str = ROOT, scope=SCOPE):
    out = []
    for rel in scope:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            out.append(p)
            continue
        for d, dirs, files in os.walk(p):
            dirs[:] = [x for x in dirs if x not in ("__tests__", "node_modules")]
            for f in files:
                if f.endswith((".ts", ".tsx")) and ".test." not in f:
                    out.append(os.path.join(d, f))
    return sorted(out)


def rewrite(text: str):
    """Return (new_text, count)."""
    return PATTERN.subn(r"console.info\1", text)


def main(argv):
    apply = "--apply" in argv
    total = 0
    for path in scope_files():
        with open(path, encoding="utf8") as fh:
            text = fh.read()
        new, n = rewrite(text)
        if not n:
            continue
        total += n
        print(f"{n:4d}  {os.path.relpath(path, ROOT)}")
        if apply:
            with open(path, "w", encoding="utf8") as fh:
                fh.write(new)
    print(f"{total:4d}  total ({'rewritten' if apply else 'dry run; pass --apply to write'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

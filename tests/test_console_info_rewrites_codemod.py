"""The codemod that keeps render_diagram's rewrite notes alive in production."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "console_info_rewrites",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "console_info_rewrites.py"),
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_rewrites_only_wrench_prefixed_literals():
    src = (
        "console.log('🔧 VEGA-PREPROCESS: a');\n"
        'console.log("🔧 X: b", obj);\n'
        "console.log(`🔧 LOG-SCALE-FIX: ${m}`);\n"
        "console.log(\n    `🔧 MULTI: c`\n);\n"
        "console.log('plain trace');\n"
        "console.log(msg);\n"
        "console.warn('🔧 kept as warn');\n"
    )
    out, n = mod.rewrite(src)
    assert n == 4
    assert out.count("console.info(") == 4
    # Untouched: non-wrench logs, variables, other levels.
    assert "console.log('plain trace');" in out
    assert "console.log(msg);" in out
    assert "console.warn('🔧 kept as warn');" in out
    # The multi-line call keeps its layout.
    assert "console.info(\n    `🔧 MULTI: c`\n);" in out


def test_idempotent():
    once, _ = mod.rewrite("console.log('🔧 A: x');")
    twice, n = mod.rewrite(once)
    assert n == 0 and twice == once


def test_scope_is_diagram_code_only(tmp_path):
    (tmp_path / "frontend/src/plugins/d3/__tests__").mkdir(parents=True)
    (tmp_path / "frontend/src/components").mkdir(parents=True)
    (tmp_path / "frontend/src/utils").mkdir(parents=True)
    for rel in ("frontend/src/plugins/d3/a.ts",
                "frontend/src/plugins/d3/__tests__/a.test.ts",
                "frontend/src/components/D3Renderer.tsx",
                "frontend/src/components/MUIChatHistory.tsx",
                "frontend/src/utils/diagramUtils.ts"):
        (tmp_path / rel).write_text("x")
    got = {os.path.relpath(p, tmp_path) for p in mod.scope_files(str(tmp_path))}
    assert got == {"frontend/src/plugins/d3/a.ts",
                   "frontend/src/components/D3Renderer.tsx",
                   "frontend/src/utils/diagramUtils.ts"}


def test_real_scope_contains_the_plugins():
    names = {os.path.basename(p) for p in mod.scope_files()}
    assert {"vegaLitePlugin.ts", "D3Renderer.tsx", "diagramUtils.ts"} <= names
    assert "MUIChatHistory.tsx" not in names

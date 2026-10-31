"""
Registry PyPI installs go into a private venv per service, never Ziya's env.

open-mcp, github and official-mcp used to run a bare ``['pip', 'install',
pkg]``. That is the first pip on PATH -- for a pip-installed Ziya, Ziya's own
environment -- so a server's requirements could replace Ziya's dependencies.
Observed: a registry install put mcp 2.x next to a Ziya pinned to mcp 1.x and
every remote MCP connection failed with "'InitializeResult' object has no
attribute 'protocolVersion'".

The install tests are real and offline: a wheel built here is installed into a
fresh venv by the production helper, and the persisted command is executed.
"""

import ast
import asyncio
import importlib.util
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.mcp.registry.command_policy import validate_run_command
from app.mcp.registry.installation_helper import InstallationHelper

REGISTRY = Path(__file__).resolve().parents[1] / "app" / "mcp" / "registry"


def _wheel(dirpath: Path, dist: str, module: str, script: str = None) -> Path:
    name = dist.replace("-", "_")
    info = f"{name}-0.1.dist-info"
    files = {
        f"{module}/__init__.py": f"def main():\n    print('{module} ok')\n",
        f"{module}/__main__.py": "from . import main\nmain()\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.1\n",
        f"{info}/WHEEL": ("Wheel-Version: 1.0\nGenerator: test\n"
                          "Root-Is-Purelib: true\nTag: py3-none-any\n"),
    }
    if script:
        files[f"{info}/entry_points.txt"] = f"[console_scripts]\n{script} = {module}:main\n"
    files[f"{info}/RECORD"] = "".join(f"{n},,\n" for n in files) + f"{info}/RECORD,,\n"
    whl = dirpath / f"{name}-0.1-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for n, text in files.items():
            z.writestr(n, text)
    return whl


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("pypi_iso")
    install_dir = root / "mcp_services" / "zfake"
    prev = os.environ.get("PIP_DISABLE_PIP_VERSION_CHECK")
    os.environ["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    try:
        with_script = InstallationHelper.install_pypi_package(
            str(_wheel(root, "zfake-mcp", "zfake_mcp", script="zfake-mcp")),
            install_dir, run_name="zfake-mcp")
        module_only = InstallationHelper.install_pypi_package(
            str(_wheel(root, "zfake-mod", "zfake_mod")),
            install_dir, run_name="zfake-mod")
    finally:
        if prev is None:
            os.environ.pop("PIP_DISABLE_PIP_VERSION_CHECK", None)
        else:
            os.environ["PIP_DISABLE_PIP_VERSION_CHECK"] = prev
    return install_dir, with_script, module_only


def _inside(path: str, root: Path) -> bool:
    real, base = os.path.realpath(path), os.path.realpath(root)
    return real == base or real.startswith(base + os.sep)


# --------------------------------------------------------------------------
# The helper: real venv, real pip, real launch
# --------------------------------------------------------------------------

def test_console_script_runs_from_the_private_venv(installed):
    install_dir, res, _ = installed
    assert res["success"], res
    cmd = res["command"]
    assert _inside(cmd[0], install_dir / ".venv"), cmd
    # The persisted command passes the run policy unchanged ...
    assert validate_run_command(cmd, source="t", install_dir=install_dir) == cmd
    # ... and actually starts the installed server.
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "zfake_mcp ok", out.stderr


def test_module_fallback_uses_the_venv_interpreter(installed):
    install_dir, _, res = installed
    assert res["success"], res
    _, python = InstallationHelper.venv_paths(install_dir)
    assert res["command"] == [str(python), "-m", "zfake_mod"]
    out = subprocess.run(res["command"], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "zfake_mod ok", out.stderr


def test_ziyas_interpreter_is_untouched(installed):
    install_dir, res, _ = installed
    # Positive: the venv has the package.
    probe = subprocess.run([res["python"], "-c", "import zfake_mcp"],
                           capture_output=True, text=True, timeout=60)
    assert probe.returncode == 0, probe.stderr
    # Negative: the interpreter running Ziya (and this test) does not.
    assert importlib.util.find_spec("zfake_mcp") is None


def test_failed_install_is_reported_not_raised(tmp_path):
    res = InstallationHelper.install_pypi_package(
        str(tmp_path / "missing-0.1-py3-none-any.whl"), tmp_path / "svc")
    assert res["success"] is False
    assert "pip install failed" in res["error"]


def test_resolve_in_venv(installed, tmp_path):
    install_dir, res, _ = installed
    _, python = InstallationHelper.venv_paths(install_dir)
    assert InstallationHelper.resolve_in_venv(["python", "-u", "s.py"], install_dir) \
        == [str(python), "-u", "s.py"]
    assert InstallationHelper.resolve_in_venv(["zfake-mcp", "--x"], install_dir) \
        == [res["command"][0], "--x"]
    assert InstallationHelper.resolve_in_venv(["npx", "-y", "p"], install_dir) == ["npx", "-y", "p"]
    # No venv: left for the policy to judge.
    assert InstallationHelper.resolve_in_venv(["python", "s.py"], tmp_path) == ["python", "s.py"]


# --------------------------------------------------------------------------
# Seams: each provider routes PyPI installs through the helper
# --------------------------------------------------------------------------

@pytest.fixture
def recorder(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []

    def fake_install(package, install_dir, **_kw):
        calls.append((package, Path(install_dir)))
        py = Path(install_dir) / ".venv" / "bin" / "python"
        return {"success": True, "python": str(py),
                "command": [str(Path(install_dir) / ".venv" / "bin" / package)]}

    monkeypatch.setattr(InstallationHelper, "install_pypi_package", staticmethod(fake_install))
    return calls


def _service(**kw):
    base = dict(service_id="svc", service_description="d", version="1",
                support_level=SimpleNamespace(value="community"),
                repository_url=None, author="a",
                installation_instructions={}, provider_metadata={})
    base.update(kw)
    return SimpleNamespace(**base)


def _install(provider, monkeypatch, service):
    async def detail(_sid):
        return service
    monkeypatch.setattr(provider, "get_service_detail", detail)
    return asyncio.run(provider.install_service("svc", "unused"))


def _assert_routed(result, calls, package):
    assert result.success, result.error_message
    assert [c[0] for c in calls] == [package]
    install_dir = calls[0][1]
    assert _inside(result.config_entries["command"][0], install_dir / ".venv")


def test_official_mcp_routes_pypi_through_private_venv(recorder, monkeypatch):
    from app.mcp.registry.providers.official_mcp import OfficialMCPRegistryProvider
    monkeypatch.setattr(InstallationHelper, "check_prerequisites",
                        staticmethod(lambda _t: (True, "")))
    svc = _service(installation_instructions={"type": "pypi", "package": "some-mcp"})
    _assert_routed(_install(OfficialMCPRegistryProvider(), monkeypatch, svc), recorder, "some-mcp")


def test_open_mcp_routes_pypi_through_private_venv(recorder, monkeypatch):
    from app.mcp.registry.providers.open_mcp import OpenMCPProvider
    svc = _service(provider_metadata={"package": {"type": "pypi", "name": "some-mcp"}})
    # Previously persisted a bare 'some-mcp', which the run policy refuses.
    _assert_routed(_install(OpenMCPProvider(), monkeypatch, svc), recorder, "some-mcp")


def test_github_routes_pip_package_through_private_venv(recorder, monkeypatch):
    from app.mcp.registry.providers.github import GitHubRegistryProvider
    svc = _service(provider_metadata={"pip_package": "some-mcp"})
    _assert_routed(_install(GitHubRegistryProvider(), monkeypatch, svc), recorder, "some-mcp")


# --------------------------------------------------------------------------
# Structural guard: no bare pip-install argv under app/mcp/registry/
# --------------------------------------------------------------------------

def _bare_pip_installs(tree: ast.AST):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and len(node.elts) >= 2:
            a, b = node.elts[0], node.elts[1]
            if (isinstance(a, ast.Constant) and a.value in ("pip", "pip3")
                    and isinstance(b, ast.Constant) and b.value == "install"):
                hits.append(node.lineno)
    return hits


def test_no_bare_pip_install_argv_in_registry_code():
    files = sorted(REGISTRY.rglob("*.py"))
    assert len(files) > 5  # the scan ran over the real tree
    offenders = [f"{p.relative_to(REGISTRY)}:{ln}"
                 for p in files for ln in _bare_pip_installs(ast.parse(p.read_text()))]
    assert not offenders, (
        "A bare ['pip', 'install', ...] installs into whatever environment "
        "owns the first pip on PATH (often Ziya's). Use "
        "InstallationHelper.install_pypi_package(package, install_dir):\n  "
        + "\n  ".join(offenders))


def test_scan_detects_a_bare_pip_install():
    assert _bare_pip_installs(ast.parse("subprocess.run(['pip', 'install', p])")) == [1]
    assert _bare_pip_installs(ast.parse("subprocess.run([py, '-m', 'pip', 'install', p])")) == []

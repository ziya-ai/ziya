"""
install_extras.sh must detect what is already installed and install only what
is missing.

Before this, the plan always said "will install Chromium" and listed all
eleven TeX packages even on a machine that already had every one of them, and
--yes would blindly re-run `playwright install chromium` and `sudo tlmgr
install <everything>`.  Now the script asks the same Chromium probe the server
uses (app.utils.optional_features) and `tlmgr info --only-installed` for the
package inventory, prints only the delta, and exits before the prompt when
nothing is missing.

The machine's real state is masked: a fake `tlmgr` on PATH answers the
inventory query from FAKE_TL_INSTALLED and records any install attempt, and a
fake interpreter passed as ZIYA_PYTHON answers the Chromium probe from
FAKE_CHROMIUM and records any `-m playwright` invocation.  Nothing here can
download a browser or prompt for sudo.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "scripts" / "install_extras.sh"

ALL_TEX = "bussproofs chemfig circuitikz dvisvgm forest mhchem pgf pgfplots siunitx standalone tikz-cd".split()


def _executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def harness(tmp_path):
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()

    # Fake tlmgr: `info` lists the "installed" subset; anything else is an
    # install/update attempt and leaves a marker.
    _executable(shim_dir / "tlmgr", f"""#!/usr/bin/env bash
case "$1" in
  info)
    for p in $FAKE_TL_INSTALLED; do echo "$p"; done
    exit 0 ;;
  *)
    echo "$@" >> "{marker_dir}/tlmgr_write"
    exit 0 ;;
esac
""")

    # Fake interpreter (ZIYA_PYTHON): `import playwright` -> ok; the Chromium
    # probe -> 0 (present) / 3 (absent) per FAKE_CHROMIUM; anything else
    # (i.e. `-m playwright install chromium`) leaves a marker and fails.
    fake_py = _executable(shim_dir / "fakepython", f"""#!/usr/bin/env bash
case "$*" in
  *"import playwright"*) exit 0 ;;
  *chromium_browser_available*) [ "$FAKE_CHROMIUM" = "1" ] && exit 0 || exit 3 ;;
  *) echo "$@" >> "{marker_dir}/playwright_write"; exit 1 ;;
esac
""")

    def run(*args, chromium: bool, tex_installed=ALL_TEX):
        env = {
            **os.environ,
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "ZIYA_PYTHON": str(fake_py),
            "FAKE_CHROMIUM": "1" if chromium else "0",
            "FAKE_TL_INSTALLED": " ".join(tex_installed),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, timeout=60, env=env,
        )

    run.markers = marker_dir
    return run


class TestPlanReflectsInstalledState:

    def test_everything_present_reports_nothing_to_do(self, harness):
        r = harness("--dry-run", chromium=True)
        assert r.returncode == 0, r.stderr
        assert "Nothing to do" in r.stdout
        assert "playwright install chromium" not in r.stdout
        assert "tlmgr install" not in r.stdout

    def test_only_missing_tex_packages_are_listed(self, harness):
        present = [p for p in ALL_TEX if p not in ("tikz-cd", "forest")]
        r = harness("--dry-run", chromium=True, tex_installed=present)
        assert r.returncode == 0, r.stderr
        assert f"{len(present)} of {len(ALL_TEX)} packages installed" in r.stdout
        listed = {l.strip().lstrip("- ") for l in r.stdout.splitlines() if l.strip().startswith("- ")}
        assert listed == {"tikz-cd", "forest"}, listed

    def test_missing_chromium_is_still_planned(self, harness):
        r = harness("--browser", "--dry-run", chromium=False)
        assert r.returncode == 0, r.stderr
        assert "playwright install chromium" in r.stdout
        assert "Nothing to do" not in r.stdout

    def test_present_chromium_with_missing_tex_plans_only_tex(self, harness):
        r = harness("--dry-run", chromium=True, tex_installed=ALL_TEX[:-1])
        assert "playwright install chromium" not in r.stdout
        assert "already installed" in r.stdout
        assert "tikz-cd" in r.stdout


class TestInstallPhaseSkipsWhatIsPresent:

    def test_yes_with_everything_present_runs_no_installer(self, harness):
        # Not a dry run: --yes would previously have re-run both installers.
        r = harness("--yes", chromium=True)
        assert r.returncode == 0, r.stderr
        assert "Nothing to do" in r.stdout
        assert not (harness.markers / "playwright_write").exists(), \
            "playwright install was invoked although Chromium is present"
        assert not (harness.markers / "tlmgr_write").exists(), \
            "tlmgr was invoked although every package is present"

    def test_install_step_targets_the_missing_set(self):
        # Seam check: the plan computing MISSING_TEX is worthless if the
        # install line still passes the full TEX_PACKAGES array to tlmgr.
        src = SCRIPT.read_text()
        assert 'install "${MISSING_TEX[@]}"' in src
        assert 'install "${TEX_PACKAGES[@]}"' not in src

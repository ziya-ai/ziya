"""
Helper utilities for installing MCP servers from different sources.
"""

import os
import re
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.mcp.registry.interface import InstallationType
from app.utils.logging_utils import logger
from app.utils.process_utils import resolve_executable


class InstallationHelper:
    """Helper for installing MCP servers using different methods."""
    
    @staticmethod
    def detect_installation_type(instructions: Dict[str, Any]) -> InstallationType:
        """Detect installation type from instructions."""
        install_type = instructions.get('type', '').lower()
        
        type_map = {
            'npm': InstallationType.NPM,
            'pypi': InstallationType.PYPI,
            'python': InstallationType.PYPI,
            'pip': InstallationType.PYPI,
            'docker': InstallationType.DOCKER,
            'oci': InstallationType.DOCKER,
            'git': InstallationType.GIT,
            'remote': InstallationType.REMOTE,
            'binary': InstallationType.BINARY,
            'mcp-registry': InstallationType.MCP_REGISTRY,
            'registry': InstallationType.MCP_REGISTRY
        }
        
        return type_map.get(install_type, InstallationType.UNKNOWN)
    
    @staticmethod
    def check_prerequisites(install_type: InstallationType) -> Tuple[bool, str]:
        """Check if required tools are installed for this installation type."""
        checks = {
            InstallationType.NPM: ('npm', 'npm --version'),
            InstallationType.PYPI: ('python', 'python --version'),
            InstallationType.DOCKER: ('docker', 'docker --version'),
            InstallationType.GIT: ('git', 'git --version'),
            InstallationType.MCP_REGISTRY: ('mcp-registry', 'mcp-registry --version')
        }
        
        if install_type not in checks:
            return True, ""
        
        tool, command = checks[install_type]
        
        if not shutil.which(tool):
            return False, f"{tool} is not installed. Please install it first."
        
        return True, ""
    
    @staticmethod
    def install_npm_package(package: str, install_dir: Path) -> Dict[str, Any]:
        """Install NPM package."""
        try:
            result = subprocess.run(
                [resolve_executable('npm'), 'install', package],
                cwd=str(install_dir),
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f"npm install failed: {result.stderr}"
                }
            
            return {
                'success': True,
                'command': ['npx', '-y', package]
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def venv_paths(install_dir: Path) -> Tuple[Path, Path]:
        """(scripts dir, interpreter) of a service's private venv."""
        venv = Path(install_dir) / ".venv"
        if os.name == "nt":
            return venv / "Scripts", venv / "Scripts" / "python.exe"
        return venv / "bin", venv / "bin" / "python"

    @staticmethod
    def resolve_in_venv(argv: List[str], install_dir: Path) -> List[str]:
        """Point a run command at the service's venv when it has one.

        ``python``/``python3`` become the venv interpreter, and a bare name
        the venv installed as a console script becomes its absolute path.
        Anything else is returned unchanged for validate_run_command to judge.
        """
        if not argv:
            return argv
        bin_dir, python = InstallationHelper.venv_paths(install_dir)
        if not python.exists():
            return argv
        head = str(argv[0])
        if head in ("python", "python3"):
            return [str(python)] + list(argv[1:])
        script = bin_dir / (head + (".exe" if os.name == "nt" else ""))
        if os.sep not in head and script.exists():
            return [str(script)] + list(argv[1:])
        return argv

    @staticmethod
    def ensure_venv(install_dir: Path) -> Tuple[Optional[str], Optional[str]]:
        """Create ``<install_dir>/.venv`` if needed. Returns (python, error)."""
        _, python = InstallationHelper.venv_paths(install_dir)
        if python.exists():
            return str(python), None
        try:
            Path(install_dir).mkdir(parents=True, exist_ok=True)
            made = subprocess.run(
                [sys.executable, '-m', 'venv', str(python.parent.parent)],
                capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"Could not create a venv in {install_dir}: {e}"
        if made.returncode != 0:
            return None, f"Could not create a venv in {install_dir}: {made.stderr}"
        return str(python), None

    @staticmethod
    def install_pypi_package(package: str, install_dir: Path, *,
                             run_name: Optional[str] = None) -> Dict[str, Any]:
        """Install a PyPI package into a private venv under ``install_dir``.

        Never into Ziya's own interpreter. A bare ``pip install`` runs the
        first pip on PATH, which for a pip-installed Ziya is Ziya's own
        environment, so one server's requirements (e.g. mcp 2.x) silently
        replaced the versions Ziya itself depends on.

        ``run_name`` is the console script / module to launch; it defaults
        to the distribution name in ``package``.
        """
        try:
            bin_dir, python = InstallationHelper.venv_paths(install_dir)
            _, venv_error = InstallationHelper.ensure_venv(install_dir)
            if venv_error:
                return {'success': False, 'error': f"{venv_error} (for {package})"}

            result = subprocess.run(
                [str(python), '-m', 'pip', 'install', package],                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f"pip install failed: {result.stderr}"
                }

            if run_name is None:
                m = re.match(r'[A-Za-z0-9._-]+', package)
                run_name = m.group(0) if m else package
            script = bin_dir / (run_name + (".exe" if os.name == "nt" else ""))
            if script.exists():
                command = [str(script)]
            else:
                command = [str(python), '-m', run_name.replace('-', '_')]

            return {'success': True, 'command': command, 'python': str(python)}

        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    @staticmethod
    def setup_docker_container(image: str) -> Dict[str, Any]:
        """Setup Docker container configuration."""
        try:
            # Verify Docker is running
            result = subprocess.run(
                ['docker', 'info'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'error': 'Docker is not running. Please start Docker Desktop.'
                }
            
            return {
                'success': True,
                'command': ['docker', 'run', '-i', '--rm', image]
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def clone_git_repository(repo_url: str, install_dir: Path) -> Dict[str, Any]:
        """Clone Git repository."""
        try:
            result = subprocess.run(
                ['git', 'clone', repo_url, str(install_dir)],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f"git clone failed: {result.stderr}"
                }
            
            return {
                'success': True,
                'path': str(install_dir)
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

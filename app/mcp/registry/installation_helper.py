"""
Helper utilities for installing MCP servers from different sources.
"""

import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.mcp.registry.interface import InstallationType
from app.utils.logging_utils import logger


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
                ['npm', 'install', package],
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
    def install_pypi_package(package: str) -> Dict[str, Any]:
        """Install PyPI package."""
        try:
            result = subprocess.run(
                ['pip', 'install', package],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'error': f"pip install failed: {result.stderr}"
                }
            
            # Determine module name (replace hyphens with underscores)
            module_name = package.split('[')[0].replace('-', '_')
            
            return {
                'success': True,
                'command': ['python', '-m', module_name]
            }
            
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

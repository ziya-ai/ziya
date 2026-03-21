import requests
from packaging import version
import os
import subprocess
import sys
from typing import Optional


def get_latest_version() -> Optional[str]:
    try:
        response = requests.get('https://pypi.org/pypi/ziya/json')
        response.raise_for_status()
        return str(version.parse(response.json()['info']['version']))
    except requests.exceptions.RequestException:
        return None


def update_package() -> None:
    cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade']
    pip_index = os.environ.get('PIP_INDEX_URL', '')
    if not pip_index or 'pypi.org' not in pip_index:
        cmd.extend(['--index-url', 'https://pypi.org/simple/'])
    cmd.append('ziya')
    subprocess.check_call(cmd)


def get_current_version() -> str:
    from importlib.metadata import version
    return str(version('ziya'))


def get_build_info() -> dict:
    """Get build information if available."""
    return {
        "version": get_current_version(),
        "dev_mode": 'dev' in get_current_version()
    }

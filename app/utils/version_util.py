import requests
from packaging import version
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
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'ziya'])


def get_current_version() -> str:
    from importlib.metadata import version
    return str(version('ziya'))


def get_build_info() -> dict:
    """Get build information if available."""
    return {
        "version": get_current_version(),
        "dev_mode": 'dev' in get_current_version()
    }
